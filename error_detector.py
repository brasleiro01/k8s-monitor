import re
import hashlib
import time
from collections import deque
from typing import Optional

from config import ERROR_PATTERNS, CONTEXT_LINES, ERROR_COOLDOWN_SECONDS


class ErrorDetector:
    def __init__(self):
        self._patterns = [re.compile(p) for p in ERROR_PATTERNS]
        self._context_buffers: dict[str, deque] = {}
        self._error_timestamps: dict[str, float] = {}

    def _get_context(self, pod_key: str) -> list[str]:
        return list(self._context_buffers.get(pod_key, deque()))

    def _compute_hash(self, pod_key: str, error_line: str) -> str:
        fingerprint = f"{pod_key}:{error_line[:200]}"
        return hashlib.md5(fingerprint.encode()).hexdigest()

    def _is_duplicate(self, error_hash: str) -> bool:
        last_seen = self._error_timestamps.get(error_hash)
        if last_seen is None:
            return False
        return (time.time() - last_seen) < ERROR_COOLDOWN_SECONDS

    def process_line(self, pod_key: str, line: str) -> Optional[dict]:
        if pod_key not in self._context_buffers:
            self._context_buffers[pod_key] = deque(maxlen=CONTEXT_LINES)

        context_before = list(self._context_buffers[pod_key])
        self._context_buffers[pod_key].append(line)

        for pattern in self._patterns:
            if pattern.search(line):
                error_hash = self._compute_hash(pod_key, line)
                if self._is_duplicate(error_hash):
                    return None
                self._error_timestamps[error_hash] = time.time()
                return {
                    "error_line": line.strip(),
                    "context": context_before,
                    "error_hash": error_hash,
                    "timestamp": time.time(),
                }

        return None

    def cleanup_pod(self, pod_key: str):
        self._context_buffers.pop(pod_key, None)
