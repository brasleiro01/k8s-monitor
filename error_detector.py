import re
import hashlib
import logging
import time
from collections import deque
from typing import Optional

from config import CONTEXT_LINES, ERROR_COOLDOWN_SECONDS, ERROR_PATTERNS, MAX_DAILY_ALERTS

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
        self._context_buffers: dict[str, deque] = {}
        # hash -> timestamp do último alerta
        self._last_alert: dict[str, float] = {}
        # hash -> lista de timestamps de alertas nas últimas 24h
        self._daily_alerts: dict[str, list[float]] = {}

    def _compute_hash(self, pod_key: str, error_line: str) -> str:
        normalized = _normalize(error_line)[:200]
        return hashlib.md5(f"{pod_key}:{normalized}".encode()).hexdigest()

    def _should_suppress(self, error_hash: str) -> bool:
        now = time.time()

        # 1. Cooldown de 1h entre alertas do mesmo erro
        last = self._last_alert.get(error_hash)
        if last and (now - last) < ERROR_COOLDOWN_SECONDS:
            return True

        # 2. Limite diário: janela móvel de 24h
        window_start = now - 86400
        history = [t for t in self._daily_alerts.get(error_hash, []) if t >= window_start]
        self._daily_alerts[error_hash] = history

        if len(history) >= MAX_DAILY_ALERTS:
            logger.debug(
                "Erro suprimido (limite diário %d atingido): %s", MAX_DAILY_ALERTS, error_hash
            )
            return True

        return False

    def _record_alert(self, error_hash: str):
        now = time.time()
        self._last_alert[error_hash] = now
        self._daily_alerts.setdefault(error_hash, []).append(now)

    def process_line(self, pod_key: str, line: str) -> Optional[dict]:
        if pod_key not in self._context_buffers:
            self._context_buffers[pod_key] = deque(maxlen=CONTEXT_LINES)

        context_before = list(self._context_buffers[pod_key])
        self._context_buffers[pod_key].append(line)

        for pattern in self._patterns:
            if pattern.search(line):
                error_hash = self._compute_hash(pod_key, line)
                if self._should_suppress(error_hash):
                    return None
                self._record_alert(error_hash)
                return {
                    "error_line": line.strip(),
                    "context": context_before,
                    "error_hash": error_hash,
                    "timestamp": time.time(),
                }

        return None

    def cleanup_pod(self, pod_key: str):
        self._context_buffers.pop(pod_key, None)
