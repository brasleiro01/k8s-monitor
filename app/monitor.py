import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional


def _build_auto_postmortem(incident: dict, resolved_at: str) -> str:
    def fmt_ts(ts):
        try:
            return datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S") + " UTC"
        except Exception:
            return "desconhecido"

    detected = fmt_ts(incident.get("last_seen") or incident.get("first_seen") or 0)
    resolved  = resolved_at.replace("T", " ")[:19] + " UTC"

    try:
        dur = int(datetime.now().timestamp() - float(incident.get("first_seen") or 0))
        if dur < 60:       duration = f"{dur}s"
        elif dur < 3600:   duration = f"{dur // 60}min"
        else:              h, m = dur // 3600, (dur % 3600) // 60; duration = f"{h}h {m}min" if m else f"{h}h"
    except Exception:
        duration = "desconhecido"

    context    = "\n".join(incident.get("context") or []) or "(sem contexto capturado)"
    actions    = "\n".join(f"- [x] {a}" for a in (incident.get("immediate_action") or []))
    prevention = "\n".join(f"- {p}" for p in (incident.get("prevention") or []))
    pod = incident.get("pod", ""); ns = incident.get("namespace", ""); sev = incident.get("severity", "")
    inc_id = incident.get("id", ""); error_line = incident.get("error_line", "")
    bt = "```"

    return f"""# Postmortem (Auto) — {pod} — {detected}

## Resumo do Incidente

| Campo | Valor |
|-------|-------|
| **Pod / Workload** | `{pod}` |
| **Namespace** | `{ns}` |
| **Severidade** | `{sev}` |
| **Detectado em** | {detected} |
| **Resolvido em** | {resolved} |
| **Duração** | {duration} |
| **Hash do incidente** | `{inc_id}` |
| **Tipo de resolução** | Auto-resolvido ✅ |

## Detalhe do Erro

{bt}
{error_line}
{bt}

## Contexto

{bt}
{context}
{bt}

## Causa Raiz (análise ao detectar)

{incident.get('root_cause', '_Não disponível_')}

## Impacto Estimado

{incident.get('estimated_impact', '_Não disponível_')}

## Ações Sugeridas (para referência futura)

{actions or '- Investigação manual recomendada'}

## Medidas de Prevenção

{prevention or '- Revisar configuração de probes e resource limits'}

## Resolução

O serviço se auto-recuperou após {duration} — réplicas disponíveis novamente.
Nenhuma ação manual foi necessária desta vez, mas recomenda-se investigar a causa raiz
para evitar recorrência.

## Checklist Pós-Incidente

- [x] Incidente detectado e registrado automaticamente
- [x] Serviço auto-recuperado
- [ ] Causa raiz investigada em detalhe
- [ ] Medidas de prevenção implementadas
- [ ] Alertas/monitoramento revisados
"""

import db
from config import NAMESPACES
from ai_analyzer import AIAnalyzer
from discord_notifier import DiscordNotifier
from error_detector import ErrorDetector
from k8s_watcher import K8sLogWatcher
from postmortem_generator import PostmortemGenerator
from replica_watcher import ReplicaWatcher

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self._broadcast_fn = broadcast_fn or (lambda event, data: None)
        self._detector = ErrorDetector()
        self._analyzer = AIAnalyzer()
        self._notifier = DiscordNotifier()
        self._incident_store = PostmortemGenerator()
        self._ai_lock = threading.Lock()
        self._watcher = K8sLogWatcher(
            namespaces=NAMESPACES,
            on_log_line=self._on_log_line,
        )
        self._replica_watcher = ReplicaWatcher(
            on_zero_replicas=self._on_zero_replicas,
            on_recovered=self._on_recovered,
        )

    def start(self):
        logger.info("Iniciando k8s log monitor | namespaces=%s", NAMESPACES or "todos")
        self._watcher.start()
        self._replica_watcher.start()

    def stop(self):
        logger.info("Encerrando monitor...")
        self._watcher.stop()
        self._replica_watcher.stop()

    # ------------------------------------------------------------------ #
    # Log-based incidents                                                  #
    # ------------------------------------------------------------------ #
    def _on_log_line(self, pod_name: str, namespace: str, pod_key: str, line: str):
        error_info = self._detector.process_line(pod_key, line)
        if error_info is None:
            return

        logger.warning("Erro detectado em %s: %s", pod_key, error_info["error_line"][:120])

        t = threading.Thread(
            target=self._handle_error,
            args=(pod_name, namespace, error_info),
            daemon=True,
        )
        t.start()

    def _handle_error(self, pod_name: str, namespace: str, error_info: dict):
        with self._ai_lock:
            analysis = self._analyzer.analyze(pod_name, namespace, error_info)

        if analysis is None:
            logger.error("Análise de IA retornou None para %s/%s", namespace, pod_name)
            return

        self._incident_store.save_incident_json(pod_name, namespace, error_info, analysis)
        self._notifier.notify(pod_name, namespace, error_info, analysis)

    # ------------------------------------------------------------------ #
    # Replica-based incidents                                              #
    # ------------------------------------------------------------------ #
    def _on_zero_replicas(self, incident: dict):
        # Link to a previous postmortem for the same pod, if one exists
        prev = db.find_previous_postmortem(incident["namespace"], incident["pod"])
        if prev:
            incident["previous_postmortem_id"] = prev["id"]
            logger.info("[monitor] incidente %s tem solução conhecida: %s",
                        incident["id"][:8], prev["id"][:8])

        db.upsert_incident(incident)
        self._notifier.notify(
            incident["pod"], incident["namespace"],
            {"error_line": incident["error_line"], "context": incident["context"]},
            incident,
        )
        self._broadcast_fn("incident", {**incident, "resolved": False})

    def _on_recovered(self, incident_id: str):
        resolved_at = datetime.now(timezone.utc).isoformat()
        description = "Auto-resolvido: réplicas disponíveis novamente"

        # Generate auto-postmortem from saved incident data
        full_incident = db.find_incident_by_id(incident_id)
        postmortem_content = ""
        postmortem_file = ""
        if full_incident:
            postmortem_content = _build_auto_postmortem(full_incident, resolved_at)
            postmortem_file = (
                f"postmortem_{resolved_at.replace('-','').replace(':','')[:15]}"
                f"_{incident_id[:8]}.md"
            )

        db.resolve_incident(
            incident_id, resolved_at, description, "",
            postmortem_file, postmortem_content,
        )
        self._broadcast_fn("update", {
            "type":                   "resolved",
            "id":                     incident_id,
            "resolvedAt":             resolved_at,
            "postmortem_file":        postmortem_file or None,
            "resolution_description": description,
            "resolution_time":        "",
        })
