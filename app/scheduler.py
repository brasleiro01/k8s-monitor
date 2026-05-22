"""
Agendador de relatórios periódicos do cluster.
Roda checks nos namespaces configurados, persiste no Postgres e notifica no Discord.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import requests

from config import DISCORD_WEBHOOK_URL, NAMESPACES

logger = logging.getLogger(__name__)

HEALTH_ORDER = {"critical": 0, "warning": 1, "healthy": 2}
HEALTH_EMOJI  = {"healthy": "✅", "warning": "⚠️", "critical": "🔴"}
HEALTH_COLOR  = {"healthy": 0x3fb950, "warning": 0xd69e2e, "critical": 0xe53e3e}


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class ReportScheduler:
    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self._broadcast_fn = broadcast_fn or (lambda e, d: None)
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        import db
        await asyncio.to_thread(db.ensure_report_tables)
        self._task = asyncio.create_task(self._loop())
        logger.info("[scheduler] Agendador de relatórios iniciado")

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        import db
        while True:
            try:
                schedule = await asyncio.to_thread(db.get_report_schedule)
                if not schedule or not schedule.get("enabled"):
                    await asyncio.sleep(60)
                    continue

                next_run = _parse_dt(schedule.get("next_run"))
                now = datetime.now(timezone.utc)

                if next_run is None or next_run <= now:
                    await self._run_report(schedule["times_per_day"])
                else:
                    wait = (next_run - now).total_seconds()
                    await asyncio.sleep(min(wait, 60))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[scheduler] Erro no loop: %s", e)
                await asyncio.sleep(60)

    async def _run_report(self, times_per_day: int):
        import db
        from namespace_checker import check_namespace_result

        namespaces = NAMESPACES
        if not namespaces:
            logger.warning("[scheduler] NAMESPACES vazio — relatório ignorado")
            interval = 86400 / times_per_day
            await asyncio.to_thread(
                db.update_schedule_next_run,
                datetime.now(timezone.utc) + timedelta(seconds=interval),
            )
            return

        logger.info("[scheduler] Iniciando relatório — namespaces: %s", namespaces)
        results = []
        for ns in namespaces:
            try:
                result = await asyncio.to_thread(check_namespace_result, ns)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error("[scheduler] Falha ao verificar %s: %s", ns, e)

        if results:
            report = self._build_report(namespaces, results)
            await asyncio.to_thread(db.save_cluster_report, report)
            logger.info("[scheduler] Relatório salvo: %s (saúde: %s)",
                        report["id"][:8], report["overall_health"])
            self._broadcast_fn("report", {
                "id": report["id"],
                "overall_health": report["overall_health"],
                "summary": report["summary"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            if report["overall_health"] != "healthy":
                await asyncio.to_thread(self._notify_discord, report)

        interval = 86400 / times_per_day
        await asyncio.to_thread(
            db.update_schedule_next_run,
            datetime.now(timezone.utc) + timedelta(seconds=interval),
        )

    def _build_report(self, namespaces: list, results: list) -> dict:
        overall = min(
            (r.get("overall_health", "healthy") for r in results),
            key=lambda h: HEALTH_ORDER.get(h, 2),
        )
        healthy  = sum(r.get("healthy_count",  0) for r in results)
        warning  = sum(r.get("warning_count",  0) for r in results)
        critical = sum(r.get("critical_count", 0) for r in results)
        total    = healthy + warning + critical

        return {
            "id":             str(uuid.uuid4()),
            "namespaces":     namespaces,
            "overall_health": overall,
            "healthy_count":  healthy,
            "warning_count":  warning,
            "critical_count": critical,
            "total_pods":     total,
            "summary": (
                f"{len(namespaces)} namespace(s) verificado(s) — "
                f"{total} pod(s): {critical} crítico(s), {warning} aviso(s), {healthy} saudável(is)."
            ),
            "details": results,
        }

    def _notify_discord(self, report: dict):
        if not DISCORD_WEBHOOK_URL:
            return
        health  = report["overall_health"]
        emoji   = HEALTH_EMOJI.get(health, "❓")
        ns_list = ", ".join(f"`{n}`" for n in report["namespaces"])

        embed = {
            "title":       f"{emoji} Relatório Agendado — K8s Monitor",
            "description": report["summary"],
            "color":       HEALTH_COLOR.get(health, 0xd69e2e),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "fields": [
                {"name": "Namespaces",   "value": ns_list,                          "inline": False},
                {"name": "🔴 Críticos",  "value": str(report["critical_count"]),    "inline": True},
                {"name": "⚠️ Avisos",    "value": str(report["warning_count"]),     "inline": True},
                {"name": "✅ Saudáveis", "value": str(report["healthy_count"]),     "inline": True},
            ],
            "footer": {"text": "K8s Monitor — Relatório automático agendado"},
        }
        try:
            requests.post(
                DISCORD_WEBHOOK_URL,
                json={"username": "K8s Monitor", "embeds": [embed]},
                timeout=10,
            ).raise_for_status()
        except Exception as e:
            logger.error("[scheduler] Falha ao notificar Discord: %s", e)
