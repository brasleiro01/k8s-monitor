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
                    await self._run_report(schedule.get("scheduled_time", "08:00"))
                else:
                    wait = (next_run - now).total_seconds()
                    await asyncio.sleep(min(wait, 60))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[scheduler] Erro no loop: %s", e)
                await asyncio.sleep(60)

    def _next_run_for_time(self, scheduled_time: str) -> datetime:
        h, m = map(int, scheduled_time.split(":"))
        now = datetime.now(timezone.utc)
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    async def _run_report(self, scheduled_time: str = "08:00"):
        import db
        from namespace_checker import check_namespace_result

        namespaces = NAMESPACES
        if not namespaces:
            logger.warning("[scheduler] NAMESPACES vazio — relatório ignorado")
            await asyncio.to_thread(db.update_schedule_next_run, self._next_run_for_time(scheduled_time))
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

        await asyncio.to_thread(db.update_schedule_next_run, self._next_run_for_time(scheduled_time))

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
        health = report["overall_health"]
        emoji  = HEALTH_EMOJI.get(health, "❓")

        # ── Coletar pods com problemas e classificar tipos de issues ──
        problem_pods = []
        issue_types: set[str] = set()

        for ns_detail in (report.get("details") or []):
            ns_name = ns_detail.get("namespace", "?")
            for pod in (ns_detail.get("pods") or []):
                if pod.get("health") == "healthy":
                    continue
                pname    = pod.get("pod", "?")
                phase    = pod.get("phase", "Unknown")
                restarts = pod.get("restarts", 0)
                issues   = list(pod.get("issues") or [])

                if restarts > 2:
                    issues.insert(0, f"↺ {restarts} restarts")
                    issue_types.add("restarts")
                if phase not in ("Running", "Succeeded"):
                    issues.insert(0, f"Fase: **{phase}**")
                    issue_types.add("not_running")

                for iss in issues:
                    low = iss.lower()
                    if "limit" in low or "sem limit" in low:
                        issue_types.add("no_limits")
                    if "probe" in low:
                        issue_types.add("no_probes")

                pod_emoji = HEALTH_EMOJI.get(pod.get("health", "warning"), "⚠️")
                issues_text = "\n".join(f"  › {i}" for i in issues[:3])
                problem_pods.append(f"{pod_emoji} `{ns_name}/{pname}`\n{issues_text}")

        # ── Descrição: lista os pods problemáticos ──
        if problem_pods:
            block = "\n\n".join(problem_pods[:5])
            if len(problem_pods) > 5:
                block += f"\n\n… e mais {len(problem_pods) - 5} pod(s) com problemas"
            description = block
        else:
            description = report.get("summary", "Relatório concluído.")

        # ── Dicas de resolução contextuais ──
        tips = []
        if "not_running" in issue_types:
            tips.append(
                "🔍 **Pod fora do ar** — identifique o motivo:\n"
                "  `kubectl describe pod <POD> -n <NS>` (veja a seção Events)"
            )
        if "restarts" in issue_types:
            tips.append(
                "🔄 **Restarts excessivos** — veja o que causou o crash:\n"
                "  `kubectl logs <POD> -n <NS> --previous`"
            )
        if "no_limits" in issue_types:
            tips.append(
                "⚙️ **Sem resource limits** — o pod pode consumir toda CPU/memória do nó.\n"
                "  Adicione `resources.requests` e `resources.limits` no manifest do Deployment."
            )
        if "no_probes" in issue_types:
            tips.append(
                "❤️ **Sem health probes** — o Kubernetes não sabe se sua app está saudável.\n"
                "  Use o botão **🤖 Sugestão IA** no painel K8s Monitor para gerar probes automaticamente."
            )

        # ── Monta o embed ──
        embed = {
            "title":     f"{emoji} Relatório do Cluster — K8s Monitor",
            "description": description,
            "color":     HEALTH_COLOR.get(health, 0xd69e2e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields":    [],
            "footer":    {"text": "K8s Monitor • Abra o painel para detalhes completos"},
        }

        if tips:
            embed["fields"].append({
                "name":   "💡 Como resolver",
                "value":  "\n\n".join(tips)[:1024],
                "inline": False,
            })

        embed["fields"].append({
            "name":  "📊 Resumo",
            "value": (
                f"🔴 Críticos: **{report['critical_count']}**\n"
                f"⚠️ Avisos:  **{report['warning_count']}**\n"
                f"✅ Saudáveis: **{report['healthy_count']}**"
            ),
            "inline": True,
        })
        embed["fields"].append({
            "name":   "🗂️ Namespaces",
            "value":  ", ".join(f"`{n}`" for n in report["namespaces"]),
            "inline": True,
        })

        try:
            requests.post(
                DISCORD_WEBHOOK_URL,
                json={"username": "K8s Monitor", "embeds": [embed]},
                timeout=10,
            ).raise_for_status()
        except Exception as e:
            logger.error("[scheduler] Falha ao notificar Discord: %s", e)
