import json
import logging
import os
import time
from datetime import datetime

import requests

from config import SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, SLACK_WEBHOOK_URL

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "critical": "#FF0000",
    "high": "#FF6600",
    "medium": "#FFCC00",
    "low": "#36A64F",
}

SEVERITY_EMOJI = {
    "critical": ":red_circle:",
    "high": ":orange_circle:",
    "medium": ":yellow_circle:",
    "low": ":large_green_circle:",
}

_SLACK_API = "https://slack.com/api"


class SlackNotifier:
    def __init__(self):
        self._webhook_url = SLACK_WEBHOOK_URL
        self._token = SLACK_BOT_TOKEN
        self._channel = SLACK_CHANNEL_ID

    def notify(self, pod_name: str, namespace: str, error_info: dict, analysis: dict, postmortem_path: str):
        self._send_alert(pod_name, namespace, error_info, analysis, postmortem_path)

        if self._token and self._channel and os.path.isfile(postmortem_path):
            file_url = self._upload_file(postmortem_path, pod_name, namespace)
            if file_url:
                logger.info("Postmortem uploaded to Slack: %s", file_url)

    # ------------------------------------------------------------------ #
    # Webhook alert                                                        #
    # ------------------------------------------------------------------ #

    def _send_alert(self, pod_name, namespace, error_info, analysis, postmortem_path):
        severity = analysis.get("severity", "high")
        color = SEVERITY_COLORS.get(severity, "#FF6600")
        emoji = SEVERITY_EMOJI.get(severity, ":warning:")

        immediate_actions = "\n".join(
            f"• {step}" for step in analysis.get("immediate_action", [])
        )
        prevention = "\n".join(
            f"• {measure}" for measure in analysis.get("prevention", [])
        )

        ts = datetime.fromtimestamp(error_info.get("timestamp", time.time())).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        file_note = (
            ":paperclip: _Postmortem file uploaded to this channel_"
            if self._token and self._channel
            else f"*Postmortem file:*\n`{postmortem_path}`"
        )

        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} Kubernetes Error Detected — {severity.upper()}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Pod:*\n`{pod_name}`"},
                        {"type": "mrkdwn", "text": f"*Namespace:*\n`{namespace}`"},
                        {"type": "mrkdwn", "text": f"*Severity:*\n`{severity}`"},
                        {"type": "mrkdwn", "text": f"*Detected at:*\n{ts}"},
                    ],
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Error:*\n```{error_info['error_line'][:500]}```",
                    },
                },
            ],
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Root Cause:*\n{analysis.get('root_cause', 'Unknown')}",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Estimated Impact:*\n{analysis.get('estimated_impact', 'Unknown')}",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Immediate Actions:*\n{immediate_actions}",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*Prevention Measures:*\n{prevention}",
                            },
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": file_note},
                        },
                    ],
                }
            ],
        }

        try:
            resp = requests.post(
                self._webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Slack alert sent for pod %s", pod_name)
        except requests.RequestException as e:
            logger.error("Failed to send Slack alert: %s", e)

    # ------------------------------------------------------------------ #
    # File upload (Slack Files API v2)                                     #
    # ------------------------------------------------------------------ #

    def _upload_file(self, filepath: str, pod_name: str, namespace: str) -> str | None:
        filename = os.path.basename(filepath)

        with open(filepath, "rb") as f:
            content = f.read()

        file_size = len(content)

        # Step 1: request upload URL
        try:
            r1 = requests.post(
                f"{_SLACK_API}/files.getUploadURLExternal",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"filename": filename, "length": file_size},
                timeout=10,
            )
            r1.raise_for_status()
            data1 = r1.json()
        except requests.RequestException as e:
            logger.error("Slack getUploadURLExternal failed: %s", e)
            return None

        if not data1.get("ok"):
            logger.error("Slack getUploadURLExternal error: %s", data1.get("error"))
            return None

        upload_url = data1["upload_url"]
        file_id = data1["file_id"]

        # Step 2: upload content
        try:
            r2 = requests.post(
                upload_url,
                data=content,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=30,
            )
            r2.raise_for_status()
        except requests.RequestException as e:
            logger.error("Slack file content upload failed: %s", e)
            return None

        # Step 3: complete upload and share to channel
        initial_comment = f":memo: Postmortem for `{pod_name}` (`{namespace}`)"
        try:
            r3 = requests.post(
                f"{_SLACK_API}/files.completeUploadExternal",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "files": [{"id": file_id, "title": filename}],
                    "channel_id": self._channel,
                    "initial_comment": initial_comment,
                },
                timeout=10,
            )
            r3.raise_for_status()
            data3 = r3.json()
        except requests.RequestException as e:
            logger.error("Slack completeUploadExternal failed: %s", e)
            return None

        if not data3.get("ok"):
            logger.error("Slack completeUploadExternal error: %s", data3.get("error"))
            return None

        return data3.get("files", [{}])[0].get("permalink", "")
