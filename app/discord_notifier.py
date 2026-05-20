import json
import logging
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

    def notify(self, pod_name: str, namespace: str, error_info: dict, analysis: dict):
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
            "title": f"{emoji} Erro Kubernetes — {sev.upper()}",
            "description": analysis.get("summary", ""),
            "color": color,
            "timestamp": ts,
            "fields": [
                {"name": "Pod",        "value": f"`{pod_name}`",  "inline": True},
                {"name": "Namespace",  "value": f"`{namespace}`", "inline": True},
                {"name": "Severidade", "value": f"`{sev}`",       "inline": True},
                {
                    "name": "🔎 Erro detectado",
                    "value": f"```{error_info['error_line'][:900]}```",
                    "inline": False,
                },
                {
                    "name": "🔍 Causa Raiz",
                    "value": analysis.get("root_cause", "Desconhecida")[:1000],
                    "inline": False,
                },
                {
                    "name": "💥 Impacto Estimado",
                    "value": analysis.get("estimated_impact", "Desconhecido")[:500],
                    "inline": False,
                },
                {
                    "name": "⚡ Ações Imediatas",
                    "value": actions_text[:1000] or "Verificar logs manualmente",
                    "inline": False,
                },
                {
                    "name": "🛡️ Prevenção",
                    "value": prevention_text[:500] or "Revisar tratamento de erros",
                    "inline": False,
                },
                {
                    "name": "📋 Postmortem",
                    "value": "Será gerado automaticamente ao resolver o incidente no dashboard.",
                    "inline": False,
                },
            ],
            "footer": {"text": "K8s Monitor • Resolva o incidente no dashboard para gerar o postmortem"},
        }

        payload = {"username": "K8s Monitor", "embeds": [embed]}

        try:
            resp = requests.post(self._webhook, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Notificação Discord enviada para pod %s", pod_name)
        except requests.RequestException as e:
            logger.error("Falha ao enviar notificação Discord: %s", e)

    def send_postmortem(self, pod_name: str, namespace: str, postmortem_path: str):
        """Envia o arquivo .md de postmortem ao resolver um incidente."""
        try:
            with open(postmortem_path, "rb") as f:
                import os
                filename = os.path.basename(postmortem_path)
                payload = {
                    "username": "K8s Monitor",
                    "content": f"📄 **Postmortem gerado** — `{pod_name}` / `{namespace}`",
                }
                resp = requests.post(
                    self._webhook,
                    data={"payload_json": json.dumps(payload)},
                    files={"file": (filename, f, "text/plain; charset=utf-8")},
                    timeout=20,
                )
                resp.raise_for_status()
                logger.info("Postmortem enviado ao Discord: %s", filename)
        except Exception as e:
            logger.error("Falha ao enviar postmortem ao Discord: %s", e)
