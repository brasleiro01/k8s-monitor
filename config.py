import os

NAMESPACES = [n.strip() for n in os.environ.get("NAMESPACES", "").split(",") if n.strip()]

LOG_LINES_TAIL = int(os.environ.get("LOG_LINES_TAIL", "100"))
CONTEXT_LINES = int(os.environ.get("CONTEXT_LINES", "10"))
ERROR_COOLDOWN_SECONDS = int(os.environ.get("ERROR_COOLDOWN_SECONDS", "300"))

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

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

POSTMORTEM_DIR = os.environ.get("POSTMORTEM_DIR", "./postmortems")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
