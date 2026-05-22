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
        """Query pods and events to identify probe/crash failures. Returns enriched info."""
        try:
            v1 = client.CoreV1Api(api_client=_new_api_client())
            label_str = ",".join(f"{k}={v}" for k, v in selector_labels.items())
            pods = v1.list_namespaced_pod(ns, label_selector=label_str, timeout_seconds=8)

            reasons: set[str] = set()
            probe_msgs: list[str] = []

            for pod in pods.items:
                # Container wait reasons (CrashLoopBackOff, ImagePullBackOff, OOMKilled…)
                for cs in (pod.status.container_statuses or []):
                    if cs.state and cs.state.waiting and cs.state.waiting.reason:
                        reasons.add(cs.state.waiting.reason)
                    if cs.state and cs.state.terminated and cs.state.terminated.reason:
                        reasons.add(cs.state.terminated.reason)

                # Events for this pod
                try:
                    events = v1.list_namespaced_event(
                        ns,
                        field_selector=f"involvedObject.name={pod.metadata.name}",
                        timeout_seconds=8,
                    )
                    for ev in events.items:
                        msg = (ev.message or "").lower()
                        if ev.reason == "Unhealthy":
                            if "liveness" in msg:
                                probe_msgs.append(f"Liveness probe falhou: {ev.message[:120]}")
                            elif "readiness" in msg:
                                probe_msgs.append(f"Readiness probe falhou: {ev.message[:120]}")
                            elif "startup" in msg:
                                probe_msgs.append(f"Startup probe falhou: {ev.message[:120]}")
                        elif ev.reason == "BackOff":
                            reasons.add("CrashLoopBackOff")
                except Exception:
                    pass

            return {"reasons": list(reasons), "probe_msgs": probe_msgs[:4]}
        except Exception as e:
            logger.debug("[replica-watcher] Não foi possível obter causa da falha: %s", e)
            return {"reasons": [], "probe_msgs": []}

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
                f"kubectl get pods -n {ns} -l {','.join(f'{k}={v}' for k, v in (obj.spec.selector.match_labels if obj and obj.spec and obj.spec.selector else {}).items()) or 'app=' + name}",
                f"kubectl describe {kind.lower()} {name} -n {ns}",
                f"kubectl logs -l app={name} -n {ns} --previous --tail=50",
                f"kubectl get events -n {ns} --sort-by=.lastTimestamp",
            ],
            "prevention": prevention,
            "estimated_impact": f"Serviço '{name}' completamente indisponível no namespace '{ns}'",
            "summary": (
                f"{kind} {ns}/{name} com 0 réplicas — "
                + (f"probe failure detectada" if probe_msgs else
                   f"estado: {', '.join(reasons)}" if reasons else "causa desconhecida")
            ),
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
