"""
Recomendações de configuração de pod via IA (probes + resource limits).
"""
import json
import logging
import re
from typing import Optional

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

_SYSTEM = """Você é um especialista sênior em Kubernetes. Analise as informações de um container
e sugira configurações ideais de probes e/ou resource limits.
Responda APENAS com JSON válido (sem markdown, sem texto extra).
Use português do Brasil nos campos "reasoning"."""


def get_pod_recommendations(info: dict) -> dict:
    """
    info keys:
      namespace, pod, container, image, ports (list), cpu_use (str), mem_use (str),
      cpu_use_m (int|None), mem_use_bytes (int|None),
      missing_probes (bool), missing_limits (bool),
      has_liveness (bool), has_readiness (bool)
    """
    if not GEMINI_API_KEY:
        logger.warning("[pod_advisor] GEMINI_API_KEY ausente — usando fallback")
        return _fallback(info)

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        cfg = types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            response_mime_type="application/json",
            temperature=0.2,
        )

        ports_str = ", ".join(
            str(p.get("containerPort", p.get("container_port", "")))
            for p in (info.get("ports") or [])
        ) or "nenhuma porta definida"

        needs = []
        if info.get("missing_probes"):
            if not info.get("has_liveness"):
                needs.append("liveness probe")
            if not info.get("has_readiness"):
                needs.append("readiness probe")
        if info.get("missing_limits"):
            needs.append("resource requests e limits")

        only_probes   = info.get("missing_probes") and not info.get("missing_limits")
        only_resources = info.get("missing_limits") and not info.get("missing_probes")

        prompt = f"""Container Kubernetes sem {' e '.join(needs)} configurado.

Namespace: {info.get('namespace')}
Pod: {info.get('pod')}
Container: {info.get('container')}
Imagem: {info.get('image') or 'desconhecida'}
Portas expostas: {ports_str}
Uso atual CPU: {info.get('cpu_use', 'desconhecido')}
Uso atual Memória: {info.get('mem_use', 'desconhecido')}
{"IMPORTANTE: sugira APENAS probes. Não inclua resources." if only_probes else ""}
{"IMPORTANTE: sugira APENAS resources. Não inclua probes." if only_resources else ""}

Responda com este JSON exato (omita chaves não solicitadas):
{{
  "liveness_probe": {{
    "type": "httpGet",
    "path": "/health",
    "port": 8080,
    "initial_delay_seconds": 30,
    "period_seconds": 10,
    "failure_threshold": 3,
    "timeout_seconds": 5,
    "reasoning": "Justificativa"
  }},
  "readiness_probe": {{
    "type": "httpGet",
    "path": "/ready",
    "port": 8080,
    "initial_delay_seconds": 5,
    "period_seconds": 5,
    "failure_threshold": 3,
    "timeout_seconds": 3,
    "reasoning": "Justificativa"
  }},
  "resources": {{
    "requests": {{"cpu": "50m", "memory": "64Mi"}},
    "limits": {{"cpu": "200m", "memory": "128Mi"}},
    "reasoning": "Justificativa"
  }}
}}

Regras:
- Use httpGet se portas expostas são HTTP/HTTPS; tcpSocket para DBs/non-HTTP (5432,3306,6379,27017)
- Para exec probes (caso especial), use {{"type":"exec","command":["cmd","arg"]}}
- Baseie limits no uso atual se disponível; multiplique uso por 3-5x para o limit
- Não inclua liveness_probe se has_liveness=true; não inclua readiness_probe se has_readiness=true
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=cfg,
        )
        text = re.sub(r'^```\w*\n?', '', response.text.strip())
        text = re.sub(r'\n?```$', '', text)
        result = json.loads(text)
        result["_source"] = "gemini"
        logger.info("[pod_advisor] Recomendação gerada via Gemini para %s/%s:%s",
                    info.get("namespace"), info.get("pod"), info.get("container"))
        return result

    except json.JSONDecodeError as e:
        logger.warning("[pod_advisor] JSON inválido do Gemini: %s", e)
        return _fallback(info)
    except Exception as e:
        logger.error("[pod_advisor] Erro ao chamar Gemini: %s", e)
        return _fallback(info)


def _fallback(info: dict) -> dict:
    ports = info.get("ports") or []
    raw_port = None
    for p in ports:
        raw_port = p.get("containerPort") or p.get("container_port")
        if raw_port:
            break
    port = int(raw_port) if raw_port else 8080
    db_ports = {5432, 3306, 6379, 27017, 9200, 5672, 9092}
    probe_type = "tcpSocket" if port in db_ports else "httpGet"

    result: dict = {}

    if info.get("missing_probes"):
        if not info.get("has_liveness"):
            p = {"type": probe_type, "port": port,
                 "initial_delay_seconds": 30, "period_seconds": 10,
                 "failure_threshold": 3, "timeout_seconds": 5,
                 "reasoning": "Valores padrão recomendados (IA indisponível)"}
            if probe_type == "httpGet":
                p["path"] = "/health"
            result["liveness_probe"] = p
        if not info.get("has_readiness"):
            p = {"type": probe_type, "port": port,
                 "initial_delay_seconds": 5, "period_seconds": 5,
                 "failure_threshold": 3, "timeout_seconds": 3,
                 "reasoning": "Valores padrão recomendados (IA indisponível)"}
            if probe_type == "httpGet":
                p["path"] = "/ready"
            result["readiness_probe"] = p

    if info.get("missing_limits"):
        cpu_m = info.get("cpu_use_m")
        mem_b = info.get("mem_use_bytes")
        cpu_req = max(10, cpu_m) if cpu_m else 50
        cpu_lim = max(100, cpu_m * 4) if cpu_m else 200
        mem_req_mi = max(32, mem_b // (1024 * 1024)) if mem_b else 64
        mem_lim_mi = max(128, mem_req_mi * 2)
        result["resources"] = {
            "requests": {"cpu": f"{cpu_req}m", "memory": f"{mem_req_mi}Mi"},
            "limits":   {"cpu": f"{cpu_lim}m",  "memory": f"{mem_lim_mi}Mi"},
            "reasoning": "Estimativa baseada no uso atual (IA indisponível)",
        }

    result["_source"] = "fallback"
    return result
