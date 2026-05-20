import json
import logging
import os
import time
from datetime import datetime, timezone

import requests

from config import DISCORD_WEBHOOK_URL

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "critical": 0xFF0000,
    "high":     0xFF6600,
    "medium":   0xFFCC00,
    "low":      0x3FB950,
}

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🟢",
}


class DiscordNotifier:
    def __init__(self):
        self._webhook = DISCORD_WEBHOOK_URL

    def notify(self, pod_name: str, namespace: str, error_info: dict, analysis: dict, postmortem_path: str):
        sev = analysis.get("severity", "high")
        emoji = SEVERITY_EMOJI.get(sev, "⚠️")
        color = SEVERITY_COLORS.get(sev, 0xFF6600)
        ts = datetime.fromtimestamp(
            error_info.get("timestamp", time.time()), tz=timezone.utc
        ).isoformat()

        actions_text = "\n".join(
            f"`{i+1}.` {step}" for i, step in enumerate(analysis.get("immediate_action", []))
        )
        prevention_text = "\n".join(
            f"• {m}" for m in analysis.get("prevention", [])
        )

        embed = {
            "title": f"{emoji} Kubernetes Error — {sev.upper()}",
            "color": color,
            "timestamp": ts,
            "fields": [
                {"name": "Pod",        "value": f"`{pod_name}`",  "inline": True},
                {"name": "Namespace",  "value": f"`{namespace}`", "inline": True},
                {"name": "Severity",   "value": f"`{sev}`",       "inline": True},
                {
                    "name": "Error",
                    "value": f"```{error_info['error_line'][:900]}```",
                    "inline": False,
                },
                {
                    "name": "🔍 Root Cause",
                    "value": analysis.get("root_cause", "Unknown")[:1000],
                    "inline": False,
                },
                {
                    "name": "💥 Estimated Impact",
                    "value": analysis.get("estimated_impact", "Unknown")[:500],
                    "inline": False,
                },
                {
                    "name": "⚡ Immediate Actions",
                    "value": actions_text[:1000] or "Check logs manually",
                    "inline": False,
                },
                {
                    "name": "🛡️ Prevention",
                    "value": prevention_text[:500] or "Review error handling",
                    "inline": False,
                },
            ],
            "footer": {"text": f"Postmortem: {os.path.basename(postmortem_path)}"},
        }

        payload = {"username": "K8s Monitor", "embeds": [embed]}

        if os.path.isfile(postmortem_path):
            self._send_with_file(payload, postmortem_path)
        else:
            self._send_json(payload)

    def _send_json(self, payload: dict):
        try:
            resp = requests.post(
                self._webhook,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Discord notification sent")
        except requests.RequestException as e:
            logger.error("Discord notification failed: %s", e)

    def _send_with_file(self, payload: dict, filepath: str):
        filename = os.path.basename(filepath)
        try:
            with open(filepath, "rb") as f:
                resp = requests.post(
                    self._webhook,
                    data={"payload_json": json.dumps(payload)},
                    files={"file": (filename, f, "text/plain; charset=utf-8")},
                    timeout=20,
                )
            resp.raise_for_status()
            logger.info("Discord notification + postmortem file sent")
        except requests.RequestException as e:
            logger.error("Discord notification with file failed: %s — retrying without file", e)
            self._send_json(payload)
