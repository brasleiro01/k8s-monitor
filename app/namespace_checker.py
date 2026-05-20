import json
import logging
from typing import Generator

from google import genai
from google.genai import types
from kubernetes import client as k8s_client

from config import GEMINI_API_KEY, GEMINI_MODEL
from k8s_watcher import _new_api_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Você é um especialista sênior em SRE e Kubernetes. Analise o estado de saúde de um namespace com base nos status dos pods e seus logs recentes.

Identifique erros críticos (crashes, OOM, falhas de conexão, exceções), avisos (restarts frequentes, latência, degradação) e pods saudáveis.

Responda APENAS com JSON válido (sem markdown, sem texto extra):
{
  "overall_health": "healthy|warning|critical",
  "summary": "resumo de 1-2 frases sobre o estado geral",
  "pods": [
    {
      "pod": "nome-do-pod",
      "health": "healthy|warning|critical",
      "issues": ["problema específico encontrado"],
      "recommendation": "ação recomendada"
    }
  ],
  "recommendations": ["ação global 1", "ação global 2"],
  "healthy_count": 0,
  "warning_count": 0,
  "critical_count": 0
}

Responda em Português do Brasil."""


def check_namespace_stream(namespace: str) -> Generator[str, None, None]:
    """Yields SSE-formatted strings for namespace health check."""

    def evt(step: str, data: dict) -> str:
        return f"event: {step}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield evt("progress", {"step": "pods", "message": f"Buscando pods em '{namespace}'..."})

        api = _new_api_client()
        v1 = k8s_client.CoreV1Api(api_client=api)

        try:
            pod_list = v1.list_namespaced_pod(namespace=namespace, _request_timeout=10)
        except Exception as e:
            yield evt("error", {"message": f"Não foi possível listar pods em '{namespace}': {e}"})
            yield evt("done", {})
            return

        pods = pod_list.items
        if not pods:
            yield evt("result", {
                "namespace": namespace,
                "overall_health": "healthy",
                "summary": f"Nenhum pod encontrado no namespace '{namespace}'.",
                "pods": [], "recommendations": [],
                "healthy_count": 0, "warning_count": 0, "critical_count": 0,
            })
            yield evt("done", {})
            return

        yield evt("progress", {
            "step": "pods",
            "message": f"Encontrados {len(pods)} pod(s). Lendo logs...",
            "total": len(pods),
        })

        pod_data = []
        for idx, pod in enumerate(pods):
            pod_name = pod.metadata.name
            phase = pod.status.phase or "Unknown"
            restart_count = sum(
                cs.restart_count for cs in (pod.status.container_statuses or [])
            )
            containers = [c.name for c in (pod.spec.containers or [])]

            yield evt("progress", {
                "step": "logs",
                "message": f"Lendo logs de {pod_name}...",
                "current": idx + 1,
                "total": len(pods),
            })

            logs_parts = []
            for container in containers:
                try:
                    logs = v1.read_namespaced_pod_log(
                        name=pod_name, namespace=namespace,
                        container=container, tail_lines=80,
                        _request_timeout=10,
                    )
                    if logs:
                        logs_parts.append(f"[{container}]\n{logs}")
                except Exception as e:
                    logs_parts.append(f"[{container}] (erro ao ler logs: {e})")

            pod_data.append({
                "name": pod_name,
                "phase": phase,
                "restarts": restart_count,
                "containers": containers,
                "logs": "\n".join(logs_parts)[:3000],
            })

        yield evt("progress", {"step": "analysis", "message": "Analisando com Gemini AI..."})

        pods_text = ""
        for p in pod_data:
            pods_text += f"\n--- Pod: {p['name']} ---\n"
            pods_text += f"Status: {p['phase']} | Restarts: {p['restarts']} | Containers: {', '.join(p['containers'])}\n"
            pods_text += f"Logs:\n{p['logs'] or '(sem logs disponíveis)'}\n"

        user_msg = f"Namespace: {namespace}\nTotal de pods: {len(pods)}\n{pods_text}"

        try:
            gemini = genai.Client(api_key=GEMINI_API_KEY)
            cfg = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
            )
            response = gemini.models.generate_content(
                model=GEMINI_MODEL, contents=user_msg, config=cfg,
            )
            result = json.loads(response.text)
        except Exception as e:
            logger.error("Gemini falhou na verificação de namespace: %s", e)
            result = _basic_analysis(pod_data)

        result["namespace"] = namespace
        yield evt("result", result)

    except Exception as e:
        logger.error("Erro na verificação de namespace '%s': %s", namespace, e)
        yield evt("error", {"message": str(e)})

    yield evt("done", {})


def _basic_analysis(pod_data: list) -> dict:
    pods_result, h, w, c = [], 0, 0, 0
    for p in pod_data:
        issues = []
        if p["phase"] not in ("Running", "Succeeded"):
            health, c = "critical", c + 1
            issues.append(f"Pod em estado {p['phase']}")
        elif p["restarts"] >= 5:
            health, w = "warning", w + 1
            issues.append(f"{p['restarts']} restarts detectados")
        elif p["restarts"] >= 1:
            health, w = "warning", w + 1
            issues.append(f"{p['restarts']} restart(s) detectado(s)")
        else:
            health, h = "healthy", h + 1

        pods_result.append({
            "pod": p["name"], "health": health, "issues": issues,
            "recommendation": "Verificar logs com kubectl logs" if issues else "Pod saudável",
        })

    overall = "critical" if c > 0 else ("warning" if w > 0 else "healthy")
    return {
        "overall_health": overall,
        "summary": f"Análise básica: {h} pod(s) saudável(is), {w} aviso(s), {c} crítico(s).",
        "pods": pods_result,
        "recommendations": [],
        "healthy_count": h, "warning_count": w, "critical_count": c,
    }
