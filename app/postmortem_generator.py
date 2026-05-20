import logging
from datetime import datetime, timezone

import db

logger = logging.getLogger(__name__)


class PostmortemGenerator:
    def __init__(self):
        if not db.is_configured():
            raise RuntimeError(
                "DATABASE_URL não configurado. "
                "O banco de dados é obrigatório nesta versão da aplicação."
            )
        logger.info("PostmortemGenerator inicializado — persistência via Postgres")

    def save_incident_json(self, pod_name: str, namespace: str, error_info: dict, analysis: dict) -> str:
        error_hash = error_info.get("error_hash", "unknown")
        now = datetime.now(timezone.utc)

        incident = {
            "id":               error_hash,
            "pod":              pod_name,
            "namespace":        namespace,
            "severity":         analysis.get("severity", "high"),
            "timestamp":        error_info.get("timestamp", now.timestamp()),
            "error_line":       error_info.get("error_line", ""),
            "context":          error_info.get("context", []),
            "root_cause":       analysis.get("root_cause", ""),
            "immediate_action": analysis.get("immediate_action", []),
            "prevention":       analysis.get("prevention", []),
            "estimated_impact": analysis.get("estimated_impact", ""),
            "summary":          analysis.get("summary", ""),
        }

        db.upsert_incident(incident)
        logger.info("Incidente salvo: %s (pod=%s)", error_hash[:8], pod_name)
        return error_hash
