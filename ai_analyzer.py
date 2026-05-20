import json
import logging
from typing import Optional

import google.generativeai as genai

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
- Inclua comandos kubectl, configurações ou correções reais quando aplicável
- NÃO use passos genéricos como "verificar logs" ou "inspecionar eventos" — o usuário já sabe fazer isso
- Baseie-se no erro real: se um diretório não existe, diga como criá-lo; se um volume não está montado, diga como corrigir; se há OOM, diga como ajustar os limites
- Exemplos de boas ações imediatas:
  * "Criar o diretório ausente: kubectl exec -n NAMESPACE POD -- mkdir -p /caminho/ausente"
  * "Corrigir o PVC desconectado: kubectl describe pvc NOME -n NAMESPACE para identificar o problema e recriar se necessário"
  * "Aumentar o limite de memória no deployment: kubectl set resources deployment/NOME --limits=memory=512Mi -n NAMESPACE"
  * "Reiniciar o deployment após a correção: kubectl rollout restart deployment/NOME -n NAMESPACE"

Guia de severidade:
- critical: serviço fora do ar, risco de perda de dados, brecha de segurança
- high: indisponibilidade parcial, degradação significativa
- medium: componente não crítico com falha, degradação leve
- low: aviso sem impacto direto ao usuário

Responda sempre em Português do Brasil."""


class AIAnalyzer:
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        self._model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1,
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
            result = json.loads(response.text)
            logger.info("Análise Gemini concluída para %s/%s — severidade: %s", namespace, pod_name, result.get("severity"))
            return result
        except json.JSONDecodeError as e:
            logger.warning("Gemini retornou resposta não-JSON para %s: %s | Resposta: %s", pod_name, e, getattr(response, 'text', '')[:200])
            return self._extract_json_fallback(getattr(response, 'text', ''))
        except Exception as e:
            logger.error("Erro na API Gemini para %s/%s: %s", namespace, pod_name, e)
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
            "root_cause": "Análise automática indisponível — verifique os logs do pod para mais detalhes",
            "severity": "high",
            "immediate_action": [
                "Verificar os logs completos: kubectl logs -n NAMESPACE POD --previous",
                "Descrever o pod para ver eventos: kubectl describe pod POD -n NAMESPACE",
                "Verificar o status dos recursos: kubectl get events -n NAMESPACE --sort-by=.lastTimestamp",
            ],
            "prevention": [
                "Configurar health checks (liveness/readiness probes) adequados",
                "Definir limites de recursos (requests/limits) para o pod",
            ],
            "estimated_impact": "Indeterminado — investigação manual necessária",
            "summary": f"Erro detectado no pod — análise de IA indisponível: {error_info.get('error_line', '')[:100]}",
        }
