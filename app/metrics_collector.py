"""
Coleta snapshots de CPU/memória a cada 5 minutos e persiste no Postgres.
Alimenta a análise de tendências do pod_advisor.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

from kubernetes import client
from k8s_watcher import _new_api_client
from config import EXCLUDE_NAMESPACES, NAMESPACES
import db

logger = logging.getLogger(__name__)

COLLECT_INTERVAL = 300  # 5 minutos


async def run_metrics_collector():
    logger.info("[metrics-collector] Iniciado — coleta a cada %ds", COLLECT_INTERVAL)
    while True:
        try:
            await asyncio.to_thread(_collect_once)
        except Exception as e:
            logger.warning("[metrics-collector] Erro na coleta: %s", e)
        await asyncio.sleep(COLLECT_INTERVAL)


def _collect_once():
    try:
        api_client = _new_api_client()
        custom = client.CustomObjectsApi(api_client=api_client)
        metrics = custom.list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="pods"
        )

        records = []
        now = datetime.now(timezone.utc)

        for pm in metrics.get("items", []):
            ns   = pm["metadata"]["namespace"]
            pod  = pm["metadata"]["name"]

            if EXCLUDE_NAMESPACES and ns in EXCLUDE_NAMESPACES:
                continue
            if NAMESPACES and ns not in NAMESPACES:
                continue

            for c in pm.get("containers", []):
                usage = c.get("usage", {})
                cpu_m = _parse_cpu_m(usage.get("cpu", ""))
                mem_b = _parse_mem_bytes(usage.get("memory", ""))

                if cpu_m is not None or mem_b is not None:
                    records.append({
                        "namespace":   ns,
                        "pod":         pod,
                        "container":   c["name"],
                        "cpu_m":       cpu_m,
                        "mem_bytes":   mem_b,
                        "recorded_at": now,
                    })

        if records:
            db.record_metrics(records)
            logger.debug("[metrics-collector] %d amostras salvas", len(records))

        db.purge_old_metrics(days=7)

    except Exception as e:
        logger.warning("[metrics-collector] Falha na coleta: %s", e)


def _parse_cpu_m(s: str):
    if not s:
        return None
    try:
        if s.endswith("n"):   return max(1, int(s[:-1]) // 1_000_000)
        if s.endswith("u"):   return max(1, int(s[:-1]) // 1_000)
        if s.endswith("m"):   return int(s[:-1])
        return int(float(s) * 1000)
    except Exception:
        return None


def _parse_mem_bytes(s: str):
    if not s:
        return None
    try:
        for suffix, mult in [("Ki", 1024), ("Mi", 1024**2), ("Gi", 1024**3),
                              ("K", 1000),  ("M", 1000**2),  ("G", 1000**3)]:
            if s.endswith(suffix):
                return int(s[:-len(suffix)]) * mult
        return int(s)
    except Exception:
        return None


def pod_prefix(pod_name: str) -> str:
    """Remove sufixos de hash de pods gerenciados por ReplicaSet/StatefulSet."""
    m = re.match(r'^(.*)-[a-z0-9]{5,10}-[a-z0-9]{4,5}$', pod_name)
    if m:
        return m.group(1)
    m = re.match(r'^(.*)-\d+$', pod_name)
    if m:
        return m.group(1)
    return pod_name
