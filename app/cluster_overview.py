"""
Visão geral do cluster: todos os pods agrupados por namespace, sem IA.
"""
import logging

from kubernetes import client as k8s_client
from kubernetes.client import CustomObjectsApi

from config import EXCLUDE_NAMESPACES, NAMESPACES
from k8s_watcher import _new_api_client
from namespace_checker import (
    _fmt_mem,
    _parse_cpu_m,
    _parse_mem_bytes,
    _pct,
    _probe_desc,
)

logger = logging.getLogger(__name__)


def _health_from_pod(phase: str, restarts: int, ready: bool,
                     cpu_pct: int | None, mem_pct: int | None) -> str:
    if phase not in ("Running", "Succeeded") or restarts > 5 or not ready:
        return "critical"
    if restarts > 2 or (cpu_pct and cpu_pct > 80) or (mem_pct and mem_pct > 80):
        return "warning"
    if (cpu_pct and cpu_pct > 60) or (mem_pct and mem_pct > 60):
        return "warning"
    return "healthy"


def fetch_cluster_overview() -> dict:
    api_client = _new_api_client()
    v1 = k8s_client.CoreV1Api(api_client=api_client)
    custom = CustomObjectsApi(api_client=api_client)

    # ── fetch pods respecting NAMESPACES / EXCLUDE_NAMESPACES ──
    try:
        if NAMESPACES:
            pods_raw = []
            for ns in NAMESPACES:
                result = v1.list_namespaced_pod(ns, timeout_seconds=15)
                pods_raw.extend(result.items)
        else:
            pod_list = v1.list_pod_for_all_namespaces(timeout_seconds=15)
            pods_raw = pod_list.items

        if EXCLUDE_NAMESPACES:
            pods_raw = [p for p in pods_raw
                        if p.metadata.namespace not in EXCLUDE_NAMESPACES]
    except Exception as exc:
        logger.error("[overview] list pods failed: %s", exc)
        return {"namespaces": [], "metrics_available": False, "total_pods": 0,
                "healthy": 0, "warning": 0, "critical": 0}

    # ── fetch cluster-wide metrics ──
    metrics_map: dict[str, dict] = {}
    metrics_available = False
    try:
        metrics = custom.list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="pods"
        )
        for pm in metrics.get("items", []):
            ns = pm["metadata"]["namespace"]
            name = pm["metadata"]["name"]
            metrics_map[f"{ns}/{name}"] = {
                c["name"]: c["usage"] for c in pm.get("containers", [])
            }
        metrics_available = True
    except Exception as exc:
        logger.warning("[overview] metrics unavailable: %s", exc)

    # ── build per-namespace structure ──
    ns_map: dict[str, list] = {}
    totals = {"healthy": 0, "warning": 0, "critical": 0}

    for pod in pods_raw:
        ns = pod.metadata.namespace
        pod_name = pod.metadata.name
        phase = pod.status.phase or "Unknown"

        # restart + ready counts
        restarts = 0
        ready_count = 0
        total_containers = 0
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                restarts += cs.restart_count or 0
                if cs.ready:
                    ready_count += 1
                total_containers += 1
        pod_ready = (ready_count == total_containers and total_containers > 0)

        pod_metrics = metrics_map.get(f"{ns}/{pod_name}", {})

        # containers
        containers = []
        for c in (pod.spec.containers or []):
            res = c.resources or k8s_client.V1ResourceRequirements()
            lim = res.limits or {}
            req = res.requests or {}

            cpu_lim_m = _parse_cpu_m(lim.get("cpu"))
            mem_lim_b = _parse_mem_bytes(lim.get("memory"))
            cpu_lim = f"{cpu_lim_m}m" if cpu_lim_m else "N/A"
            mem_lim = _fmt_mem(mem_lim_b) if mem_lim_b else "N/A"
            cpu_req = f"{_parse_cpu_m(req.get('cpu'))}m" if req.get("cpu") else "N/A"
            mem_req = _fmt_mem(_parse_mem_bytes(req.get("memory"))) if req.get("memory") else "N/A"

            c_metrics = pod_metrics.get(c.name, {})
            cpu_use_m = _parse_cpu_m(c_metrics.get("cpu"))
            mem_use_b = _parse_mem_bytes(c_metrics.get("memory"))
            cpu_use = f"{cpu_use_m}m" if cpu_use_m is not None else "N/A"
            mem_use = _fmt_mem(mem_use_b) if mem_use_b is not None else "N/A"
            cpu_pct = _pct(cpu_use_m, cpu_lim_m)
            mem_pct = _pct(mem_use_b, mem_lim_b)

            # container ready state
            c_ready = False
            c_state = "running"
            if pod.status.container_statuses:
                for cs in pod.status.container_statuses:
                    if cs.name == c.name:
                        c_ready = cs.ready
                        st = cs.state
                        if st.waiting:
                            c_state = f"waiting/{st.waiting.reason or ''}"
                        elif st.terminated:
                            c_state = f"terminated/{st.terminated.reason or ''}"

            containers.append({
                "name": c.name,
                "ready": c_ready,
                "state": c_state,
                "cpu_req": cpu_req,
                "cpu_lim": cpu_lim,
                "mem_req": mem_req,
                "mem_lim": mem_lim,
                "cpu_use": cpu_use if metrics_available else "N/A",
                "mem_use": mem_use if metrics_available else "N/A",
                "cpu_pct": cpu_pct if metrics_available else None,
                "mem_pct": mem_pct if metrics_available else None,
                "liveness": _probe_desc(c.liveness_probe),
                "readiness": _probe_desc(c.readiness_probe),
            })

        # pod-level resource summary (max % across containers)
        cpu_pcts = [c["cpu_pct"] for c in containers if c["cpu_pct"] is not None]
        mem_pcts = [c["mem_pct"] for c in containers if c["mem_pct"] is not None]
        pod_cpu_pct = max(cpu_pcts) if cpu_pcts else None
        pod_mem_pct = max(mem_pcts) if mem_pcts else None

        health = _health_from_pod(phase, restarts, pod_ready, pod_cpu_pct, pod_mem_pct)
        totals[health] += 1

        ns_map.setdefault(ns, []).append({
            "pod": pod_name,
            "phase": phase,
            "restarts": restarts,
            "ready": pod_ready,
            "ready_count": ready_count,
            "total_containers": total_containers,
            "health": health,
            "cpu_pct": pod_cpu_pct,
            "mem_pct": pod_mem_pct,
            "containers": containers,
        })

    # ── sort: critical first, then warning, then by name ──
    HEALTH_ORDER = {"critical": 0, "warning": 1, "healthy": 2}

    namespaces = []
    for ns_name, pods in sorted(ns_map.items()):
        pods_sorted = sorted(pods, key=lambda p: (HEALTH_ORDER.get(p["health"], 3), p["pod"]))
        ns_health = min((p["health"] for p in pods_sorted), key=lambda h: HEALTH_ORDER[h], default="healthy")
        namespaces.append({
            "namespace": ns_name,
            "health": ns_health,
            "pods": pods_sorted,
        })
    namespaces.sort(key=lambda n: (HEALTH_ORDER.get(n["health"], 3), n["namespace"]))

    return {
        "namespaces": namespaces,
        "metrics_available": metrics_available,
        "total_pods": len(pods_raw),
        "healthy": totals["healthy"],
        "warning": totals["warning"],
        "critical": totals["critical"],
    }
