import json
import os
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
    POSTMORTEM_DIR,
)

logger = logging.getLogger(__name__)

_STATE_FILE = os.path.join(POSTMORTEM_DIR, ".alert_state.json")

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
        # Estado persistido: hash -> timestamp do último alerta
        self._last_alert: dict[str, float] = {}
        # Estado persistido: hash -> lista de timestamps nas últimas 24h
        self._daily_alerts: dict[str, list[float]] = {}
        self._load_state()

    # ------------------------------------------------------------------ #
    # Persistência                                                        #
    # ------------------------------------------------------------------ #

    def _load_state(self):
        try:
            if not os.path.exists(_STATE_FILE):
                return
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._last_alert = data.get("last_alert", {})
            raw_daily = data.get("daily_alerts", {})
            # Descarta entradas mais antigas que 24h ao carregar
            cutoff = time.time() - 86400
            self._daily_alerts = {
                h: [t for t in ts if t >= cutoff]
                for h, ts in raw_daily.items()
                if any(t >= cutoff for t in ts)
            }
            logger.info(
                "Estado de alertas carregado: %d erros rastreados (arquivo: %s)",
                len(self._last_alert),
                _STATE_FILE,
            )
        except Exception as exc:
            logger.warning("Não foi possível carregar estado de alertas: %s", exc)

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
            tmp = _STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"last_alert": self._last_alert, "daily_alerts": self._daily_alerts},
                    f,
                )
            os.replace(tmp, _STATE_FILE)
        except Exception as exc:
            logger.warning("Não foi possível salvar estado de alertas: %s", exc)

    # ------------------------------------------------------------------ #
    # Lógica de supressão                                                 #
    # ------------------------------------------------------------------ #

    def _compute_hash(self, pod_key: str, error_line: str) -> str:
        normalized = _normalize(error_line)[:200]
        return hashlib.md5(f"{pod_key}:{normalized}".encode()).hexdigest()

    def _check_and_record(self, error_hash: str) -> bool:
        """
        Verifica se o erro deve ser suprimido e, se não, registra o alerta.
        Retorna True se deve prosseguir (não suprimido), False se deve suprimir.
        Operação atômica protegida por lock.
        """
        now = time.time()

        # 1. Cooldown: mesmo erro nos últimos ERROR_COOLDOWN_SECONDS
        last = self._last_alert.get(error_hash)
        if last and (now - last) < ERROR_COOLDOWN_SECONDS:
            remaining = int(ERROR_COOLDOWN_SECONDS - (now - last))
            logger.debug(
                "Erro suprimido (cooldown %ds restantes): %s", remaining, error_hash
            )
            return False

        # 2. Limite diário: janela móvel de 24h
        cutoff = now - 86400
        history = [t for t in self._daily_alerts.get(error_hash, []) if t >= cutoff]
        self._daily_alerts[error_hash] = history

        if len(history) >= MAX_DAILY_ALERTS:
            logger.info(
                "Erro suprimido — limite diário de %d alertas atingido para hash %s",
                MAX_DAILY_ALERTS,
                error_hash,
            )
            return False

        # Registra e persiste
        self._last_alert[error_hash] = now
        self._daily_alerts[error_hash].append(now)
        self._save_state()
        return True

    # ------------------------------------------------------------------ #
    # Interface pública                                                   #
    # ------------------------------------------------------------------ #

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
