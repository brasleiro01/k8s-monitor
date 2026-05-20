import os

NAMESPACES = [n.strip() for n in os.environ.get("NAMESPACES", "").split(",") if n.strip()]
EXCLUDE_NAMESPACES = [n.strip() for n in os.environ.get("EXCLUDE_NAMESPACES", "k8s-monitor").split(",") if n.strip()]

LOG_LINES_TAIL = int(os.environ.get("LOG_LINES_TAIL", "100"))
CONTEXT_LINES = int(os.environ.get("CONTEXT_LINES", "10"))
ERROR_COOLDOWN_SECONDS = int(os.environ.get("ERROR_COOLDOWN_SECONDS", "3600"))  # 1h entre alertas do mesmo erro
MAX_DAILY_ALERTS = int(os.environ.get("MAX_DAILY_ALERTS", "5"))               # máx 5 alertas/dia por erro

# Padrões que devem ser IGNORADOS mesmo que batam com ERROR_PATTERNS.
# Separe múltiplos padrões com || no env var.
# Exemplo: EXCLUDE_LOG_PATTERNS="disabling udev||health check"
_raw_exclude = os.environ.get("EXCLUDE_LOG_PATTERNS", "")
EXCLUDE_LOG_PATTERNS = [p.strip() for p in _raw_exclude.split("||") if p.strip()]

ERROR_PATTERNS = [
    r"(?i)\bERROR\b",
    r"(?i)\bCRITICAL\b",
    r"(?i)\bFATAL\b",
    r"(?i)EXCEPTION",
    r"(?i)Traceback \(most recent call last\)",
    r"(?i)panic:",
    r"(?i)SEVERE",
    r"(?i)java\.lang\.\w+Exception",
    r"(?i)OOMKilled",
    r"(?i)CrashLoopBackOff",
    r"(?i)ImagePullBackOff",
    r"(?i)ErrImagePull",
]

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
