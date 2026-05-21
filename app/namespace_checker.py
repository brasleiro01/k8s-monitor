"""
Verificação de saúde de namespace: pods, logs, recursos, probes, métricas, eventos.
"""
import json
import logging
from typing import Generator

from google import genai
from google.genai import types
from kubernetes import client as k8s_client
from kubernetes.client import CustomObjectsApi

from config import GEMINI_API_KEY, GEMINI_MODEL
from k8s_watcher import _new_api_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Você é um especialista sênior em SRE e Kubernetes.

Analise o estado de saúde de um namespace com base nos dados fornecidos:
- Status e restarts dos pods
- Configuração de readiness e liveness probes
- Resource requests/limits e uso real de CPU/memória
- Eventos recentes (warnings, falhas)
- Logs recentes de cada container

Identifique:
- CRITICAL: crashes, OOM kills, probes falhando, pods não agendados, sem limites com alto consumo
- WARNING: sem resource limits, uso >80%%, restarts frequentes, sem probes, uso 60-80%%
- HEALTHY: pods estáveis com probes configuradas e consumo normal

Responda APENAS com JSON válido:
{
  "overall_health": "healthy|warning|critical",
  "summary": "resumo de 1-2 frases do estado geral",
  "pods": [
    {
      "pod": "nome-do-pod",
      "health": "healthy|warning|critical",
      "issues": ["problema específico encontrado"],
      "recommendation": "ação recomendada e concreta"
    }
  ],
  "recommendations": ["ação global 1", "ação global 2"],
  "healthy_count": 0,
  "warning_count": 0,
  "critical_count": 0
}

Responda em Português do Brasil."""


# ------------------------------------------------------------------ #
# Resource helpers                                                    #
# ------------------------------------------------------------------ #

def _parse_cpu_m(s: str | None) -> int | None:
    """Converte string de CPU para millicores. Aceita '250m', '1', '250123456n'."""
    if not s:
        return None
    s = s.strip()
    try:
        if s.endswith('n'):
            return max(1, int(s[:-1]) // 1_000_000)
        if s.endswith('m'):
            return int(s[:-1])
        return int(float(s) * 1000)
    except (ValueError, OverflowError):
        return None


def _parse_mem_bytes(s: str | None) -> int | None:
    """Converte string de memória para bytes. Aceita '256Mi', '1Gi', '380000Ki'."""
    if not s:
        return None
    s = s.strip()
    for suffix, factor in [
        ('Pi', 1024**5), ('Ti', 1024**4), ('Gi', 1024**3), ('Mi', 1024**2), ('Ki', 1024),
        ('P',  1000**5), ('T',  1000**4), ('G',  1000**3), ('M',  1000**2), ('K',  1000),
    ]:
        if s.endswith(suffix):
            try:
                return int(s[:-len(suffix)]) * factor
            except ValueError:
                return None
    try:
        return int(s)
    except ValueError:
        return None


def _fmt_mem(b: int | None) -> str:
    if b is None:
        return "N/A"
    if b >= 1024**3:
        return f"{b/1024**3:.1f}Gi"
    if b >= 1024**2:
        return f"{b/1024**2:.0f}Mi"
    if b >= 1024:
        return f"{b/1024:.0f}Ki"
    return f"{b}B"


def _pct(usage: int | None, limit: int | None) -> int | None:
    if usage is None or limit is None or limit == 0:
        return None
    return min(100, round(usage / limit * 100))


def _probe_desc(probe) -> str | None:
    if probe is None:
        return None
    if probe.http_get:
        return f"HTTP {probe.http_get.path or '/'}:{probe.http_get.port}"
    if probe.tcp_socket:
        return f"TCP :{probe.tcp_socket.port}"
    if probe.exec:
        cmd = " ".join(probe.exec.command or [])
        return f"exec: {cmd[:50]}"
    if probe.grpc:
        return f"gRPC :{probe.grpc.port}"
    return "configurado"


# ------------------------------------------------------------------ #
# Data collection                                                      #
# ------------------------------------------------------------------ #

def _fetch_metrics(custom: CustomObjectsApi, namespace: str) -> dict:
    """Retorna {pod_name: {container_name: {cpu_m, mem_bytes}}}. Vazio se indisponível."""
    try:
        data = custom.list_namespaced_custom_object(
            group="metrics.k8s.io", version="v1beta1",
            namespace=namespace, plural="pods",
            _request_timeout=8,
        )
        result = {}
        for item in data.get("items", []):
            pod_name = item["metadata"]["name"]
            result[pod_name] = {}
            for c in item.get("containers", []):
                u = c.get("usage", {})
                result[pod_name][c["name"]] = {
                    "cpu_m":      _parse_cpu_m(u.get("cpu")),
                    "mem_bytes":  _parse_mem_bytes(u.get("memory")),
                }
        return result
    except Exception as e:
        logger.info("[checker] métricas indisponíveis: %s", e)
        return {}


def _fetch_events(v1: k8s_client.CoreV1Api, namespace: str) -> dict:
    """Retorna {pod_name: [mensagens de eventos Warning recentes]}."""
    try:
        evts = v1.list_namespaced_event(
            namespace=namespace,
            field_selector="type=Warning",
            _request_timeout=8,
        )
        result: dict[str, list[str]] = {}
        for e in sorted(evts.items, key=lambda x: x.last_timestamp or "", reverse=True):
            pod = e.involved_object.name
            if e.involved_object.kind == "Pod" and e.message:
                result.setdefault(pod, [])
                if len(result[pod]) < 5:
                    result[pod].append(e.message)
        return result
    except Exception as e:
        logger.info("[checker] eventos indisponíveis: %s", e)
        return {}


def _collect_pod_info(
    v1: k8s_client.CoreV1Api,
    pod,
    namespace: str,
    metrics: dict,
    events: dict,
) -> dict:
    pod_name = pod.metadata.name
    phase = pod.status.phase or "Unknown"
    pod_metrics = metrics.get(pod_name, {})

    # Conditions
    conditions = {}
    for c in (pod.status.conditions or []):
        conditions[c.type] = c.status == "True"

    # Containers
    container_statuses = {cs.name: cs for cs in (pod.status.container_statuses or [])}
    containers = []
    for c in (pod.spec.containers or []):
        cs = container_statuses.get(c.name)
        cm = pod_metrics.get(c.name, {})

        req = c.resources.requests or {} if c.resources else {}
        lim = c.resources.limits  or {} if c.resources else {}

        cpu_req_m   = _parse_cpu_m(req.get("cpu"))
        cpu_lim_m   = _parse_cpu_m(lim.get("cpu"))
        mem_req_b   = _parse_mem_bytes(req.get("memory"))
        mem_lim_b   = _parse_mem_bytes(lim.get("memory"))
        cpu_use_m   = cm.get("cpu_m")
        mem_use_b   = cm.get("mem_bytes")

        state = "unknown"
        if cs:
            if cs.state.running:
                state = "running"
            elif cs.state.waiting:
                reason = cs.state.waiting.reason or ""
                state = f"waiting:{reason}" if reason else "waiting"
            elif cs.state.terminated:
                state = f"terminated:{cs.state.terminated.exit_code}"

        containers.append({
            "name":          c.name,
            "ready":         cs.ready if cs else False,
            "state":         state,
            # Resources
            "cpu_req":       f"{cpu_req_m}m"   if cpu_req_m  is not None else None,
            "cpu_lim":       f"{cpu_lim_m}m"   if cpu_lim_m  is not None else None,
            "cpu_use":       f"{cpu_use_m}m"   if cpu_use_m  is not None else None,
            "cpu_pct":       _pct(cpu_use_m, cpu_lim_m),
            "mem_req":       _fmt_mem(mem_req_b),
            "mem_lim":       _fmt_mem(mem_lim_b),
            "mem_use":       _fmt_mem(mem_use_b),
            "mem_pct":       _pct(mem_use_b, mem_lim_b),
            "has_limits":    bool(lim),
            # Probes
            "liveness":      _probe_desc(c.liveness_probe),
            "readiness":     _probe_desc(c.readiness_probe),
            "startup":       _probe_desc(c.startup_probe),
        })

    # Logs
    logs_parts = []
    for c in (pod.spec.containers or []):
        try:
            logs = v1.read_namespaced_pod_log(
                name=pod_name, namespace=namespace,
                container=c.name, tail_lines=60, _request_timeout=8,
            )
            if logs:
                logs_parts.append(f"[{c.name}]\n{logs}")
        except Exception:
            pass

    return {
        "name":        pod_name,
        "phase":       phase,
        "restarts":    sum(cs.restart_count for cs in (pod.status.container_statuses or [])),
        "conditions":  conditions,
        "containers":  containers,
        "events":      events.get(pod_name, []),
        "logs":        "\n".join(logs_parts)[:2500],
    }


# ------------------------------------------------------------------ #
# Gemini analysis                                                      #
# ------------------------------------------------------------------ #

def _build_prompt(namespace: str, pods: list) -> str:
    lines = [f"Namespace: {namespace} | Total pods: {len(pods)}\n"]
    for p in pods:
        lines.append(f"=== Pod: {p['name']} ===")
        lines.append(f"Status: {p['phase']} | Restarts: {p['restarts']}")
        conds = ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in p["conditions"].items())
        if conds:
            lines.append(f"Conditions: {conds}")
        for c in p["containers"]:
            lines.append(f"  Container: {c['name']} ({c['state']})")
            if c["cpu_lim"] or c["mem_lim"]:
                lines.append(
                    f"    Recursos — req: cpu={c['cpu_req'] or '-'} mem={c['mem_req']} | "
                    f"lim: cpu={c['cpu_lim'] or '-'} mem={c['mem_lim']}"
                )
                if c["cpu_pct"] is not None or c["mem_pct"] is not None:
                    lines.append(
                        f"    Uso atual — cpu={c['cpu_use'] or 'N/A'} ({c['cpu_pct'] or '?'}%%) | "
                        f"mem={c['mem_use']} ({c['mem_pct'] or '?'}%%)"
                    )
            else:
                lines.append("    Recursos: SEM LIMITS DEFINIDOS")
            lines.append(
                f"    Liveness: {c['liveness'] or 'AUSENTE'} | "
                f"Readiness: {c['readiness'] or 'AUSENTE'}"
            )
        if p["events"]:
            lines.append(f"  Eventos recentes: {'; '.join(p['events'][:3])}")
        if p["logs"]:
            lines.append(f"  Logs:\n{p['logs']}")
        lines.append("")
    return "\n".join(lines)


def _analyze_with_gemini(namespace: str, pods: list) -> dict:
    try:
        prompt = _build_prompt(namespace, pods)
        g = genai.Client(api_key=GEMINI_API_KEY)
        cfg = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.1,
        )
        resp = g.models.generate_content(model=GEMINI_MODEL, contents=prompt, config=cfg)
        return json.loads(resp.text)
    except Exception as e:
        logger.error("[checker] Gemini falhou: %s", e)
        return _basic_analysis(pods)


def _basic_analysis(pods: list) -> dict:
    results, h, w, c = [], 0, 0, 0
    for p in pods:
        issues = []
        if p["phase"] not in ("Running", "Succeeded"):
            issues.append(f"Pod em estado {p['phase']}")
        if p["restarts"] >= 5:
            issues.append(f"{p['restarts']} restarts detectados")
        for cont in p["containers"]:
            if not cont["has_limits"]:
                issues.append(f"Container '{cont['name']}' sem resource limits")
            if not cont["liveness"]:
                issues.append(f"Container '{cont['name']}' sem liveness probe")
            if not cont["readiness"]:
                issues.append(f"Container '{cont['name']}' sem readiness probe")
            if cont["mem_pct"] and cont["mem_pct"] > 80:
                issues.append(f"Memória em {cont['mem_pct']}%% do limite")
            if cont["cpu_pct"] and cont["cpu_pct"] > 80:
                issues.append(f"CPU em {cont['cpu_pct']}%% do limite")

        if any("estado" in i or "restarts" in i.lower() for i in issues):
            health = "critical"; c += 1
        elif issues:
            health = "warning"; w += 1
        else:
            health = "healthy"; h += 1

        results.append({
            "pod": p["name"], "health": health, "issues": issues,
            "recommendation": "Investigar logs com kubectl logs" if issues else "Pod saudável",
        })

    overall = "critical" if c > 0 else ("warning" if w > 0 else "healthy")
    return {
        "overall_health": overall,
        "summary": f"Análise básica: {h} saudável(is), {w} aviso(s), {c} crítico(s).",
        "pods": results,
        "recommendations": [],
        "healthy_count": h, "warning_count": w, "critical_count": c,
    }


# ------------------------------------------------------------------ #
# Stream entrypoint                                                    #
# ------------------------------------------------------------------ #

def check_namespace_stream(namespace: str) -> Generator[str, None, None]:
    def evt(step: str, data: dict) -> str:
        return f"event: {step}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield evt("progress", {"step": "pods", "message": f"Buscando pods em '{namespace}'..."})

        api = _new_api_client()
        v1 = k8s_client.CoreV1Api(api_client=api)
        custom = CustomObjectsApi(api_client=api)

        try:
            pod_list = v1.list_namespaced_pod(namespace=namespace, _request_timeout=10)
        except Exception as e:
            yield evt("error", {"message": f"Não foi possível listar pods em '{namespace}': {e}"})
            yield evt("done", {})
            return

        pods = pod_list.items
        if not pods:
            yield evt("result", {
                "namespace": namespace, "overall_health": "healthy",
                "summary": f"Nenhum pod encontrado em '{namespace}'.",
                "pods": [], "recommendations": [],
                "healthy_count": 0, "warning_count": 0, "critical_count": 0,
                "metrics_available": False,
            })
            yield evt("done", {})
            return

        yield evt("progress", {
            "step": "pods",
            "message": f"Encontrados {len(pods)} pod(s). Buscando métricas...",
            "total": len(pods),
        })

        # Metrics (best effort)
        yield evt("progress", {"step": "metrics", "message": "Buscando métricas de CPU/memória..."})
        metrics = _fetch_metrics(custom, namespace)
        metrics_available = bool(metrics)

        # Events (best effort)
        yield evt("progress", {"step": "events", "message": "Lendo eventos do namespace..."})
        events_by_pod = _fetch_events(v1, namespace)

        # Per-pod data + logs
        pod_data = []
        for idx, pod in enumerate(pods):
            yield evt("progress", {
                "step": "logs",
                "message": f"Analisando {pod.metadata.name}...",
                "current": idx + 1,
                "total": len(pods),
            })
            pod_data.append(_collect_pod_info(v1, pod, namespace, metrics, events_by_pod))

        yield evt("progress", {"step": "analysis", "message": "Analisando com Gemini AI..."})
        ai = _analyze_with_gemini(namespace, pod_data)

        # Merge AI analysis with raw data
        ai_by_pod = {p["pod"]: p for p in ai.get("pods", [])}
        merged_pods = []
        for p in pod_data:
            ai_p = ai_by_pod.get(p["name"], {
                "pod": p["name"], "health": "healthy", "issues": [], "recommendation": "",
            })
            merged_pods.append({
                **ai_p,
                "phase":      p["phase"],
                "restarts":   p["restarts"],
                "conditions": p["conditions"],
                "containers": p["containers"],
                "events":     p["events"],
            })

        yield evt("result", {
            "namespace":         namespace,
            "overall_health":    ai.get("overall_health", "warning"),
            "summary":           ai.get("summary", ""),
            "recommendations":   ai.get("recommendations", []),
            "healthy_count":     ai.get("healthy_count", 0),
            "warning_count":     ai.get("warning_count", 0),
            "critical_count":    ai.get("critical_count", 0),
            "metrics_available": metrics_available,
            "pods":              merged_pods,
        })

    except Exception as e:
        logger.error("[checker] erro em '%s': %s", namespace, e)
        yield evt("error", {"message": str(e)})

    yield evt("done", {})
