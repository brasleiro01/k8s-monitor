import re
import hashlib
import logging
import threading
import time
from collections import deque
from typing import Optional

from config import (
    CONTEXT_LINES,
    ERROR_COOLDOWN_SECONDS,
    ERROR_PATTERNS,
    EXCLUDE_LOG_PATTERNS,
    MAX_DAILY_ALERTS,
)

logger = logging.getLogger(__name__)

_NOISE = re.compile(
    r"""
    \d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?
    | t=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*
    | \b\d{13,}\b
    | [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}
    | \b[0-9a-f]{16,}\b
    | \[\d+\]
    | :\d{4,5}\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _normalize(line: str) -> str:
    return " ".join(_NOISE.sub("", line).split()).lower()


class ErrorDetector:
    def __init__(self):
        self._patterns = [re.compile(p) for p in ERROR_PATTERNS]
        self._exclude_patterns = [re.compile(p, re.I) for p in EXCLUDE_LOG_PATTERNS]
        self._context_buffers: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._last_alert: dict[str, float] = {}
        self._daily_alerts: dict[str, list[float]] = {}

    def _compute_hash(self, pod_key: str, error_line: str) -> str:
        normalized = _normalize(error_line)[:200]
        return hashlib.md5(f"{pod_key}:{normalized}".encode()).hexdigest()

    def _check_and_record(self, error_hash: str) -> bool:
        now = time.time()

        last = self._last_alert.get(error_hash)
        if last and (now - last) < ERROR_COOLDOWN_SECONDS:
            remaining = int(ERROR_COOLDOWN_SECONDS - (now - last))
            logger.debug("Erro suprimido (cooldown %ds restantes): %s", remaining, error_hash)
            return False

        cutoff = now - 86400
        history = [t for t in self._daily_alerts.get(error_hash, []) if t >= cutoff]
        self._daily_alerts[error_hash] = history

        if len(history) >= MAX_DAILY_ALERTS:
            logger.info(
                "Erro suprimido — limite diário de %d alertas atingido para hash %s",
                MAX_DAILY_ALERTS, error_hash,
            )
            return False

        self._last_alert[error_hash] = now
        self._daily_alerts[error_hash].append(now)
        return True

    def process_line(self, pod_key: str, line: str) -> Optional[dict]:
        if pod_key not in self._context_buffers:
            self._context_buffers[pod_key] = deque(maxlen=CONTEXT_LINES)

        context_before = list(self._context_buffers[pod_key])
        self._context_buffers[pod_key].append(line)

        if self._exclude_patterns and any(p.search(line) for p in self._exclude_patterns):
            return None

        for pattern in self._patterns:
            if not pattern.search(line):
                continue

            error_hash = self._compute_hash(pod_key, line)

            with self._lock:
                should_proceed = self._check_and_record(error_hash)

            if not should_proceed:
                return None

            return {
                "error_line": line.strip(),
                "context": context_before,
                "error_hash": error_hash,
                "timestamp": time.time(),
            }

        return None

    def cleanup_pod(self, pod_key: str):
        self._context_buffers.pop(pod_key, None)
