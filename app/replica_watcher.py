"""
Monitora Deployments e StatefulSets: alerta quando réplicas disponíveis == 0,
resolve automaticamente quando volta a ter ao menos 1.
"""
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from kubernetes import client, watch

from config import EXCLUDE_NAMESPACES, NAMESPACES
from k8s_watcher import _new_api_client

logger = logging.getLogger(__name__)


def _incident_id(kind: str, namespace: str, name: str) -> str:
    raw = f"replica-zero:{kind.lower()}:{namespace}/{name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ReplicaWatcher:
    def __init__(
        self,
        on_zero_replicas: Callable[[dict], None],
        on_recovered: Callable[[str], None],
    ):
        self._on_zero_replicas = on_zero_replicas
        self._on_recovered = on_recovered
        self._active: dict[str, str] = {}  # resource_key -> incident_id
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        for kind in ("Deployment", "StatefulSet"):
            t = threading.Thread(target=self._watch, args=(kind,), daemon=True)
            t.start()
        logger.info("[replica-watcher] Iniciado — monitorando Deployments e StatefulSets")

    def stop(self):
        self._running = False

    def _should_watch(self, namespace: str) -> bool:
        if namespace in EXCLUDE_NAMESPACES:
            return False
        if NAMESPACES and namespace not in NAMESPACES:
            return False
        return True

    def _watch(self, kind: str):
        retry_delay = 5
        while self._running:
            try:
                apps_v1 = client.AppsV1Api(api_client=_new_api_client())
                w = watch.Watch()

                list_fn = (
                    apps_v1.list_deployment_for_all_namespaces
                    if kind == "Deployment"
                    else apps_v1.list_stateful_set_for_all_namespaces
                )

                retry_delay = 5  # reset on successful connect
                for event in w.stream(list_fn, timeout_seconds=0):
                    if not self._running:
                        break

                    obj        = event["object"]
                    event_type = event["type"]
                    ns         = obj.metadata.namespace
                    name       = obj.metadata.name

                    if not self._should_watch(ns):
                        continue

                    resource_key = f"{kind.lower()}:{ns}/{name}"
                    inc_id       = _incident_id(kind, ns, name)

                    if event_type == "DELETED":
                        self._maybe_recover(resource_key, inc_id, name, ns, kind)
                        continue

                    spec_replicas = (obj.spec.replicas or 0) if obj.spec else 0
                    available     = (obj.status.available_replicas or 0) if obj.status else 0
                    ready         = (obj.status.ready_replicas    or 0) if obj.status else 0

                    if available == 0:
                        self._maybe_alarm(kind, ns, name, resource_key, inc_id,
                                          spec_replicas, ready, available, obj)
                    else:
                        self._maybe_recover(resource_key, inc_id, name, ns, kind)

            except Exception as e:
                err_str = str(e)
                if "403" in err_str or "Forbidden" in err_str:
                    retry_delay = 120
                    logger.warning(
                        "[replica-watcher] %s: sem permissão RBAC para apps/%ss "
                        "(adicione 'apps/deployments,statefulsets get/list/watch' ao ClusterRole) "
                        "— retry em %ds",
                        kind, kind.lower(), retry_delay,
                    )
                else:
                    retry_delay = min(retry_delay * 2, 60)
                    logger.warning("[replica-watcher] Erro ao watch %s: %s — retry em %ds",
                                   kind, e, retry_delay)
                time.sleep(retry_delay)

    def _check_failure_reason(self, ns: str, selector_labels: dict) -> dict:
        """Query pods, conditions and events. Returns structured diagnostic data."""
        try:
            v1 = client.CoreV1Api(api_client=_new_api_client())
            label_str = ",".join(f"{k}={v}" for k, v in selector_labels.items())
            pods = v1.list_namespaced_pod(ns, label_selector=label_str, timeout_seconds=8)

            reasons: set[str] = set()
            probe_msgs: list[str] = []
            events_context: list[str] = []
            pod_names: list[str] = []

            for pod in pods.items:
                pod_name = pod.metadata.name
                pod_names.append(pod_name)

                # Pod conditions (ContainersReady=False usually has a useful message)
                for cond in (pod.status.conditions or []):
                    if cond.status == "False" and cond.message:
                        events_context.append(
                            f"[{pod_name}] Condição {cond.type}=False: {cond.message[:150]}"
                        )

                # Container states
                for cs in (pod.status.container_statuses or []):
                    if cs.restart_count:
                        events_context.append(
                            f"[{pod_name}/{cs.name}] Reiniciou {cs.restart_count}x"
                        )
                    if cs.state and cs.state.waiting:
                        r = cs.state.waiting.reason or ""
                        if r:
                            reasons.add(r)
                        if cs.state.waiting.message:
                            events_context.append(
                                f"[{pod_name}/{cs.name}] Aguardando/{r}: "
                                f"{cs.state.waiting.message[:150]}"
                            )
                    if cs.state and cs.state.terminated:
                        r = cs.state.terminated.reason or ""
                        if r:
                            reasons.add(r)
                        if cs.state.terminated.message:
                            events_context.append(
                                f"[{pod_name}/{cs.name}] Terminado/{r}: "
                                f"{cs.state.terminated.message[:150]}"
                            )

                # Events for this pod (sorted newest-first, take last 12)
                try:
                    evs = v1.list_namespaced_event(
                        ns,
                        field_selector=f"involvedObject.name={pod_name}",
                        timeout_seconds=8,
                    )
                    sorted_evs = sorted(
                        evs.items,
                        key=lambda e: (e.last_timestamp or e.event_time or ""),
                        reverse=True,
                    )
                    for ev in sorted_evs[:12]:
                        msg = (ev.message or "").strip()
                        reason = (ev.reason or "").strip()
                        if not msg:
                            continue
                        low = msg.lower()
                        if reason == "Unhealthy":
                            if "liveness" in low:
                                probe_msgs.append(f"Liveness probe falhou: {msg[:130]}")
                            elif "readiness" in low:
                                probe_msgs.append(f"Readiness probe falhou: {msg[:130]}")
                            elif "startup" in low:
                                probe_msgs.append(f"Startup probe falhou: {msg[:130]}")
                        elif reason == "BackOff":
                            reasons.add("CrashLoopBackOff")
                        events_context.append(f"[Evento/{reason}] {msg[:150]}")
                except Exception:
                    pass

            return {
                "reasons":        list(reasons),
                "probe_msgs":     probe_msgs[:4],
                "events_context": events_context[:25],
                "pod_names":      pod_names,
            }
        except Exception as e:
            logger.debug("[replica-watcher] Não foi possível obter diagnóstico: %s", e)
            return {"reasons": [], "probe_msgs": [], "events_context": [], "pod_names": []}

    def _maybe_alarm(self, kind: str, ns: str, name: str,
                     resource_key: str, inc_id: str,
                     spec_replicas: int, ready: int, available: int, obj=None):
        with self._lock:
            if resource_key in self._active:
                return
            self._active[resource_key] = inc_id

        now = datetime.now(timezone.utc)
        logger.warning("[replica-watcher] ZERO réplicas: %s %s/%s (spec=%d ready=%d available=%d)",
                       kind, ns, name, spec_replicas, ready, available)

        # ── Enrich with probe/crash info ──
        failure = {}
        if obj is not None:
            try:
                sel = obj.spec.selector
                if sel and sel.match_labels:
                    failure = self._check_failure_reason(ns, sel.match_labels)
            except Exception:
                pass

        reasons   = failure.get("reasons", [])
        probe_msgs = failure.get("probe_msgs", [])

        # ── Build specific error_line ──
        if probe_msgs:
            error_line = f"{kind} {ns}/{name}: {probe_msgs[0]}"
        elif "CrashLoopBackOff" in reasons:
            error_line = f"{kind} {ns}/{name}: CrashLoopBackOff — container reiniciando constantemente"
        elif "ImagePullBackOff" in reasons or "ErrImagePull" in reasons:
            error_line = f"{kind} {ns}/{name}: ImagePullBackOff — falha ao baixar a imagem"
        elif "OOMKilled" in reasons:
            error_line = f"{kind} {ns}/{name}: OOMKilled — container encerrado por falta de memória"
        else:
            error_line = (
                f"{kind} {ns}/{name}: 0 réplicas disponíveis"
                + (f" (desejado: {spec_replicas})" if spec_replicas else " (scaled to 0)")
            )

        # ── Build specific root_cause ──
        if probe_msgs:
            root_cause = (
                f"O {kind} '{name}' está com 0 réplicas porque as health probes estão falhando. "
                f"{' '.join(probe_msgs[:2])} "
                "Verifique se os caminhos das probes correspondem a endpoints reais da aplicação, "
                "ou ajuste initialDelaySeconds se a aplicação demora para inicializar."
            )
            prevention = [
                "Verificar se o path da liveness/readiness probe existe na aplicação",
                "Aumentar initialDelaySeconds se a app demora para iniciar",
                "Para nginx/static: usar path '/' em vez de '/health' ou '/ready'",
                "Usar tcpSocket probe se a app não expõe endpoint HTTP de health",
            ]
        elif "CrashLoopBackOff" in reasons:
            root_cause = (
                f"O container do {kind} '{name}' está em CrashLoopBackOff: "
                "inicia e falha repetidamente. Verifique os logs com "
                f"'kubectl logs {name} -n {ns} --previous' para identificar a causa."
            )
            prevention = [
                "Revisar logs do container para identificar a exceção de inicialização",
                "Verificar variáveis de ambiente e secrets obrigatórios",
                "Configurar resource limits adequados para evitar OOMKilled",
            ]
        elif "OOMKilled" in reasons:
            root_cause = (
                f"O container do {kind} '{name}' foi encerrado pelo OOM killer: "
                "o processo consumiu mais memória do que o limite definido. "
                "Aumente o memory limit ou investigue vazamento de memória."
            )
            prevention = [
                "Aumentar memory limit no manifest do Deployment",
                "Investigar vazamento de memória na aplicação",
                "Configurar alertas de uso de memória antes de atingir o limite",
            ]
        else:
            root_cause = (
                f"{kind} '{name}' está com 0 réplicas disponíveis. "
                "Possíveis causas: pods em CrashLoopBackOff, ImagePullBackOff, "
                "sem recursos no cluster ou deployment escalado para zero."
            )
            prevention = [
                "Configurar PodDisruptionBudget (PDB) com minAvailable >= 1",
                "Definir resource requests/limits adequados para evitar Pending",
                "Configurar liveness e readiness probes corretas",
            ]

        # Build context (saved to DB + used as AI input seed)
        context = [
            f"Tipo: {kind}",
            f"Réplicas desejadas (spec): {spec_replicas}",
            f"Réplicas prontas (ready): {ready}",
            f"Réplicas disponíveis: {available}",
            f"Detectado: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        ]
        if reasons:
            context.append(f"Estado dos containers: {', '.join(reasons)}")
        if probe_msgs:
            context.extend(probe_msgs)
        context.extend(failure.get("events_context", []))

        # Extract selector for background AI log-fetch (not persisted to DB)
        selector_labels = {}
        try:
            if obj and obj.spec and obj.spec.selector and obj.spec.selector.match_labels:
                selector_labels = dict(obj.spec.selector.match_labels)
        except Exception:
            pass

        label_selector = ",".join(f"{k}={v}" for k, v in selector_labels.items()) or f"app={name}"

        incident = {
            "id":        inc_id,
            "pod":       name,
            "namespace": ns,
            "severity":  "critical",
            "timestamp": now.timestamp(),
            "error_line": error_line,
            "context":   context,
            "root_cause": root_cause,
            "immediate_action": [
                f"kubectl get pods -n {ns} -l {label_selector}",
                f"kubectl describe {kind.lower()} {name} -n {ns}",
                f"kubectl logs -l {label_selector} -n {ns} --previous --tail=50",
                f"kubectl get events -n {ns} --sort-by=.lastTimestamp",
            ],
            "prevention": prevention,
            "estimated_impact": f"Serviço '{name}' completamente indisponível no namespace '{ns}'",
            "summary": (
                f"{kind} {ns}/{name} com 0 réplicas — "
                + (f"probe failure detectada" if probe_msgs else
                   f"estado: {', '.join(reasons)}" if reasons else "causa desconhecida")
            ),
            # Private: consumed by monitor, not saved to DB
            "_selector": selector_labels,
            "_pod_names": failure.get("pod_names", []),
        }
        self._on_zero_replicas(incident)

    def _maybe_recover(self, resource_key: str, inc_id: str,
                       name: str, ns: str, kind: str):
        with self._lock:
            if resource_key not in self._active:
                return
            del self._active[resource_key]

        logger.info("[replica-watcher] Recuperado: %s %s/%s", kind, ns, name)
        self._on_recovered(inc_id)
