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
        while self._running:
            try:
                apps_v1 = client.AppsV1Api(api_client=_new_api_client())
                w = watch.Watch()

                list_fn = (
                    apps_v1.list_deployment_for_all_namespaces
                    if kind == "Deployment"
                    else apps_v1.list_stateful_set_for_all_namespaces
                )

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
                                          spec_replicas, ready, available)
                    else:
                        self._maybe_recover(resource_key, inc_id, name, ns, kind)

            except Exception as e:
                logger.warning("[replica-watcher] Erro ao watch %s: %s — retry em 5s", kind, e)
                time.sleep(5)

    def _maybe_alarm(self, kind: str, ns: str, name: str,
                     resource_key: str, inc_id: str,
                     spec_replicas: int, ready: int, available: int):
        with self._lock:
            if resource_key in self._active:
                return
            self._active[resource_key] = inc_id

        now = datetime.now(timezone.utc)
        logger.warning("[replica-watcher] ZERO réplicas: %s %s/%s (spec=%d ready=%d available=%d)",
                       kind, ns, name, spec_replicas, ready, available)

        incident = {
            "id":        inc_id,
            "pod":       name,
            "namespace": ns,
            "severity":  "critical",
            "timestamp": now.timestamp(),
            "error_line": (
                f"{kind} {ns}/{name}: 0 réplicas disponíveis"
                + (f" (desejado: {spec_replicas})" if spec_replicas else " (scaled to 0)")
            ),
            "context": [
                f"Tipo: {kind}",
                f"Réplicas desejadas (spec): {spec_replicas}",
                f"Réplicas prontas (ready): {ready}",
                f"Réplicas disponíveis: {available}",
                f"Detectado: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC",
            ],
            "root_cause": (
                f"{kind} {name} está com 0 réplicas disponíveis. "
                "Possíveis causas: pods em CrashLoopBackOff, ImagePullBackOff, "
                "sem recursos no cluster ou deployment escalado para zero."
            ),
            "immediate_action": [
                f"kubectl get pods -n {ns}",
                f"kubectl describe {kind.lower()} {name} -n {ns}",
                f"kubectl get events -n {ns} --sort-by=.lastTimestamp",
                f"kubectl rollout status {kind.lower()}/{name} -n {ns}",
            ],
            "prevention": [
                "Configurar PodDisruptionBudget (PDB) com minAvailable >= 1",
                "Definir resource requests/limits adequados para evitar Pending",
                "Configurar liveness e readiness probes",
            ],
            "estimated_impact": f"Serviço {name} completamente indisponível no namespace {ns}",
            "summary":          f"{kind} {ns}/{name} com 0 réplicas disponíveis — serviço fora do ar",
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
