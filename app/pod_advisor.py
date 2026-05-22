"""
Recomendações de configuração de pod via IA (probes + resource limits).
Usa histórico de métricas do Postgres quando disponível para análise de tendências.
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


def _build_resource_context(info: dict) -> tuple[str, Optional[dict]]:
    """
    Busca histórico de métricas e constrói o texto de contexto para o prompt.
    Retorna (texto_contexto, stats_dict_ou_None).
    """
    try:
        import db
        stats = db.get_resource_stats(
            info.get("namespace", ""),
            info.get("pod", ""),
            info.get("container", ""),
            hours=24,
        )
    except Exception:
        stats = None

    if stats and stats["samples"] >= 6:
        growth = stats["mem_growth_mb_per_hour"]
        growth_line = (
            f"  ⚠️  Crescimento detectado: +{growth:.1f} MB/hora — possível memory leak"
            if growth > 2
            else f"  Memória estável (variação: {growth:+.1f} MB/hora)"
        )
        ctx = (
            f"Histórico de uso — últimas {stats['duration_hours']}h "
            f"({stats['samples']} amostras a cada 5min):\n"
            f"  CPU:     mín={stats['cpu_min']}m | P50={stats['cpu_p50']}m"
            f" | P95={stats['cpu_p95']}m | máx={stats['cpu_max']}m\n"
            f"  Memória: mín={stats['mem_min_mi']}Mi | P50={stats['mem_p50_mi']}Mi"
            f" | P95={stats['mem_p95_mi']}Mi | máx={stats['mem_max_mi']}Mi\n"
            f"{growth_line}"
        )
        return ctx, stats

    # Sem histórico: usa snapshot atual
    cpu  = info.get("cpu_use", "N/A")
    mem  = info.get("mem_use", "N/A")
    ctx  = f"Snapshot atual: CPU={cpu}, Memória={mem} (histórico ainda insuficiente — coletando)"
    return ctx, None


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

        only_probes    = info.get("missing_probes") and not info.get("missing_limits")
        only_resources = info.get("missing_limits") and not info.get("missing_probes")

        resource_ctx, stats = _build_resource_context(info)

        # Orientação extra para o Gemini quando temos histórico
        history_guidance = ""
        if stats:
            history_guidance = (
                "\nREGRAS PARA RESOURCES (obrigatórias quando histórico disponível):\n"
                f"- requests.cpu = P50 do histórico ({stats['cpu_p50']}m)\n"
                f"- limits.cpu   = máximo histórico × 1.3 ({int(stats['cpu_max'] * 1.3)}m)\n"
                f"- requests.memory = P95 do histórico ({stats['mem_p95_mi']}Mi)\n"
                f"- limits.memory   = máximo histórico × 1.2 ({int(stats['mem_max_mi'] * 1.2)}Mi)"
            )
            if stats["mem_growth_mb_per_hour"] > 2:
                history_guidance += (
                    f"\n- ⚠️  CRESCIMENTO DE MEMÓRIA DETECTADO: aumentar limits.memory "
                    f"em pelo menos 50% para acomodar o crescimento"
                )

        prompt = f"""Container Kubernetes sem {' e '.join(needs)} configurado.

Namespace: {info.get('namespace')}
Pod: {info.get('pod')}
Container: {info.get('container')}
Imagem: {info.get('image') or 'desconhecida'}
Portas expostas: {ports_str}

{resource_ctx}
{"IMPORTANTE: sugira APENAS probes. Não inclua resources." if only_probes else ""}
{"IMPORTANTE: sugira APENAS resources. Não inclua probes." if only_resources else ""}
{history_guidance}

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
    "reasoning": "Justificativa baseada nos dados fornecidos"
  }},
  "readiness_probe": {{
    "type": "httpGet",
    "path": "/ready",
    "port": 8080,
    "initial_delay_seconds": 5,
    "period_seconds": 5,
    "failure_threshold": 3,
    "timeout_seconds": 3,
    "reasoning": "Justificativa baseada nos dados fornecidos"
  }},
  "resources": {{
    "requests": {{"cpu": "50m", "memory": "64Mi"}},
    "limits": {{"cpu": "200m", "memory": "128Mi"}},
    "reasoning": "Justificativa baseada nos dados fornecidos"
  }}
}}

Regras gerais:
- Use httpGet para HTTP/HTTPS; tcpSocket para bancos (5432,3306,6379,27017)
- Para exec probes: {{"type":"exec","command":["cmd","arg"]}}
- Não inclua liveness_probe se has_liveness=true; não inclua readiness_probe se has_readiness=true
- No reasoning, cite os números do histórico quando disponíveis
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
        result["_has_history"] = stats is not None
        logger.info("[pod_advisor] Recomendação via Gemini para %s/%s:%s (histórico=%s)",
                    info.get("namespace"), info.get("pod"), info.get("container"),
                    "sim" if stats else "não")
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
        # Tenta usar histórico de métricas primeiro
        _, stats = _build_resource_context(info)

        if stats and stats["samples"] >= 6:
            cpu_req = max(10, stats["cpu_p50"])
            cpu_lim = max(100, int(stats["cpu_max"] * 1.3))
            mem_req = max(32, stats["mem_p95_mi"])
            mem_lim = max(64, int(stats["mem_max_mi"] * 1.2))

            growth = stats["mem_growth_mb_per_hour"]
            if growth > 2:
                # Infla o limit em 50% se a memória está crescendo
                mem_lim = int(mem_lim * 1.5)
                reasoning = (
                    f"Baseado em {stats['duration_hours']}h de histórico "
                    f"({stats['samples']} amostras). "
                    f"⚠️ Memória crescendo +{growth:.1f}MB/h — limit aumentado 50% "
                    f"para evitar OOMKilled. Investigue possível memory leak."
                )
            else:
                reasoning = (
                    f"Baseado em {stats['duration_hours']}h de histórico "
                    f"({stats['samples']} amostras). "
                    f"CPU: P50={stats['cpu_p50']}m, máx={stats['cpu_max']}m. "
                    f"Memória: P95={stats['mem_p95_mi']}Mi, máx={stats['mem_max_mi']}Mi."
                )

            result["resources"] = {
                "requests": {"cpu": f"{cpu_req}m", "memory": f"{mem_req}Mi"},
                "limits":   {"cpu": f"{cpu_lim}m", "memory": f"{mem_lim}Mi"},
                "reasoning": reasoning,
            }
            result["_source"] = "fallback_com_historico"
            result["_has_history"] = True
        else:
            # Sem histórico: usa snapshot atual com multiplicadores conservadores
            cpu_m = info.get("cpu_use_m")
            mem_b = info.get("mem_use_bytes")
            cpu_req = max(10, cpu_m) if cpu_m else 50
            cpu_lim = max(100, cpu_m * 4) if cpu_m else 200
            mem_req_mi = max(32, mem_b // (1024 * 1024)) if mem_b else 64
            mem_lim_mi = max(128, mem_req_mi * 2)
            result["resources"] = {
                "requests": {"cpu": f"{cpu_req}m", "memory": f"{mem_req_mi}Mi"},
                "limits":   {"cpu": f"{cpu_lim}m", "memory": f"{mem_lim_mi}Mi"},
                "reasoning": "Estimativa baseada no uso atual (histórico insuficiente — coletando dados)",
            }
            result["_source"] = "fallback"
            result["_has_history"] = False
        return result

    result["_source"] = "fallback"
    result["_has_history"] = False
    return result
