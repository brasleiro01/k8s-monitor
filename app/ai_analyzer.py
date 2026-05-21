import json
import logging
from typing import Optional

from google import genai
from google.genai import types

import rule_analyzer
from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Você é um especialista sênior em SRE (Site Reliability Engineering) e DevOps com profundo conhecimento em Kubernetes, sistemas distribuídos e resposta a incidentes em produção.

Ao receber um erro de pod Kubernetes, analise o erro E o contexto de log fornecido, depois responda APENAS com um objeto JSON válido (sem markdown, sem texto extra) com esta estrutura exata:
{
  "root_cause": "explicação concisa e específica da causa raiz baseada no erro e contexto fornecidos",
  "severity": "critical|high|medium|low",
  "immediate_action": ["solução concreta 1", "solução concreta 2", "solução concreta 3"],
  "prevention": ["medida preventiva 1", "medida preventiva 2"],
  "estimated_impact": "descrição do impacto real nos usuários e serviços",
  "summary": "resumo em uma frase descrevendo o problema e a solução"
}

REGRAS OBRIGATÓRIAS para o campo "immediate_action":
- Forneça SOLUÇÕES CONCRETAS e ESPECÍFICAS para resolver o problema identificado no erro
- Inclua comandos kubectl reais com os nomes de pod e namespace fornecidos quando possível
- NÃO use passos genéricos como "verificar logs" ou "inspecionar eventos"
- Baseie-se no erro real: se um diretório não existe, diga como criá-lo; se há OOM, diga como ajustar limites; se a conexão é recusada, diga como verificar o serviço

Guia de severidade:
- critical: serviço fora do ar, risco de perda de dados, brecha de segurança
- high: indisponibilidade parcial, degradação significativa
- medium: componente não crítico com falha, degradação leve
- low: aviso sem impacto direto ao usuário

Responda sempre em Português do Brasil."""


class AIAnalyzer:
    def __init__(self):
        logger.info("Inicializando AIAnalyzer com modelo: %s", GEMINI_MODEL)
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY não configurada — análise de IA desabilitada, usando fallback de regras")
            self._client = None
            self._config = None
            return
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.1,
        )

    def analyze(self, pod_name: str, namespace: str, error_info: dict) -> Optional[dict]:
        logger.info("Iniciando análise de erro — pod=%s/%s", namespace, pod_name)

        # 1. Tenta análise com Gemini
        result = self._call_gemini(pod_name, namespace, error_info)
        if result:
            return result

        # 2. Fallback: análise baseada em regras (contextual, não genérica)
        logger.warning("Gemini indisponível — usando análise por regras para %s/%s", namespace, pod_name)
        result = rule_analyzer.analyze(pod_name, namespace, error_info)
        if result:
            return result

        # 3. Último recurso: genérico mínimo
        logger.error("Análise por regras também falhou para %s/%s — usando resposta de último recurso", namespace, pod_name)
        return self._last_resort(error_info)

    def _call_gemini(self, pod_name: str, namespace: str, error_info: dict) -> Optional[dict]:
        if self._client is None:
            return None
        context_text = "\n".join(error_info.get("context", []))
        user_message = (
            f"Pod: {pod_name}\n"
            f"Namespace: {namespace}\n"
            f"Linha de erro: {error_info['error_line']}\n"
            f"Contexto do log (linhas anteriores ao erro):\n{context_text}"
        )
        logger.debug("Enviando requisição ao Gemini (%s) — tamanho da mensagem: %d chars", GEMINI_MODEL, len(user_message))
        try:
            response = self._client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config=self._config,
            )
            result = json.loads(response.text)
            logger.info(
                "Gemini analisou %s/%s — severidade: %s | fonte: gemini",
                namespace, pod_name, result.get("severity")
            )
            result["_source"] = "gemini"
            return result
        except json.JSONDecodeError:
            raw = getattr(response, 'text', '')
            logger.warning(
                "Gemini retornou JSON inválido para %s/%s — tentando extração. Resposta: %.300s",
                namespace, pod_name, raw
            )
            return self._extract_json(raw)
        except Exception as e:
            logger.error(
                "Falha na API Gemini para %s/%s: %s: %s",
                namespace, pod_name, type(e).__name__, e
            )
            return None

    def _extract_json(self, text: str) -> Optional[dict]:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                result = json.loads(text[start:end])
                result["_source"] = "gemini_extracted"
                return result
            except json.JSONDecodeError:
                pass
        return None

    def _last_resort(self, error_info: dict) -> dict:
        return {
            "root_cause": "Análise automática indisponível",
            "severity": "high",
            "immediate_action": [
                "Verificar os logs do pod com: kubectl logs POD -n NAMESPACE --previous",
                "Ver eventos: kubectl get events -n NAMESPACE --sort-by=.lastTimestamp",
            ],
            "prevention": ["Configurar health checks e limites de recursos adequados"],
            "estimated_impact": "Indeterminado — investigação manual necessária",
            "summary": f"Erro no pod: {error_info.get('error_line', '')[:120]}",
            "_source": "last_resort",
        }
