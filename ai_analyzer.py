import json
import logging
from typing import Optional

import google.generativeai as genai

from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Você é um especialista em SRE (Site Reliability Engineering) e DevOps com profundo conhecimento em Kubernetes, sistemas distribuídos e resposta a incidentes em produção.

Ao receber um erro de pod Kubernetes, analise-o e responda APENAS com um objeto JSON válido (sem markdown, sem texto extra) com esta estrutura exata:
{
  "root_cause": "explicação concisa da causa raiz do erro",
  "severity": "critical|high|medium|low",
  "immediate_action": ["passo 1", "passo 2", "passo 3"],
  "prevention": ["medida 1", "medida 2"],
  "estimated_impact": "descrição do raio de impacto e usuários/serviços afetados",
  "summary": "resumo em uma frase para notificação no Discord"
}

Guia de severidade:
- critical: serviço fora do ar, risco de perda de dados, brecha de segurança
- high: indisponibilidade parcial, degradação significativa de performance
- medium: degradação de performance, falha em componente não crítico
- low: aviso, problema menor sem impacto ao usuário final

Responda sempre em Português do Brasil."""


class AIAnalyzer:
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

    def analyze(self, pod_name: str, namespace: str, error_info: dict) -> Optional[dict]:
        context_text = "\n".join(error_info.get("context", []))
        user_message = (
            f"Pod: {pod_name}\n"
            f"Namespace: {namespace}\n"
            f"Linha de erro: {error_info['error_line']}\n"
            f"Contexto do log (linhas anteriores ao erro):\n{context_text}"
        )

        try:
            response = self._model.generate_content(user_message)
            return json.loads(response.text)
        except json.JSONDecodeError:
            logger.warning("Gemini retornou resposta não-JSON, tentando extração")
            return self._extract_json_fallback(response.text)
        except Exception as e:
            logger.error("Erro na API Gemini: %s", e)
            return self._fallback_analysis(error_info)

    def _extract_json_fallback(self, text: str) -> Optional[dict]:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return self._fallback_analysis({})

    def _fallback_analysis(self, error_info: dict) -> dict:
        return {
            "root_cause": "Não foi possível determinar a causa raiz automaticamente",
            "severity": "high",
            "immediate_action": [
                "Verificar os logs do pod manualmente",
                "Inspecionar os eventos com kubectl describe pod",
                "Verificar os limites de recursos do pod",
            ],
            "prevention": [
                "Revisar o tratamento de erros da aplicação",
                "Configurar limites de recursos adequados",
            ],
            "estimated_impact": "Desconhecido — investigação manual necessária",
            "summary": f"Erro detectado: {error_info.get('error_line', 'erro desconhecido')[:100]}",
        }
