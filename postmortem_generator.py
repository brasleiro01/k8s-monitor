import json
import os
import logging
from datetime import datetime, timezone

from config import POSTMORTEM_DIR

logger = logging.getLogger(__name__)


class PostmortemGenerator:
    def __init__(self):
        os.makedirs(POSTMORTEM_DIR, exist_ok=True)

    def save_incident_json(self, pod_name: str, namespace: str, error_info: dict, analysis: dict) -> str:
        error_hash = error_info.get("error_hash", "unknown")[:8]
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")

        json_filename = f"incident_{timestamp_str}_{error_hash}.json"
        json_filepath = os.path.join(POSTMORTEM_DIR, json_filename)

        incident = {
            "id": error_info.get("error_hash", f"{timestamp_str}_{error_hash}"),
            "pod": pod_name,
            "namespace": namespace,
            "severity": analysis.get("severity", "high"),
            "timestamp": error_info.get("timestamp", now.timestamp()),
            "error_line": error_info.get("error_line", ""),
            "context": error_info.get("context", []),
            "root_cause": analysis.get("root_cause", ""),
            "immediate_action": analysis.get("immediate_action", []),
            "prevention": analysis.get("prevention", []),
            "estimated_impact": analysis.get("estimated_impact", ""),
            "summary": analysis.get("summary", ""),
        }

        with open(json_filepath, "w", encoding="utf-8") as f:
            json.dump(incident, f, ensure_ascii=False, indent=2)

        logger.info("Incidente salvo: %s", json_filepath)
        return json_filepath
