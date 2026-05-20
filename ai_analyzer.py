import json
import logging
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert SRE (Site Reliability Engineer) and DevOps specialist with deep knowledge of Kubernetes, distributed systems, and production incident response.

When given a Kubernetes pod error, analyze it and respond ONLY with a valid JSON object (no markdown, no extra text) with this exact structure:
{
  "root_cause": "concise explanation of what caused the error",
  "severity": "critical|high|medium|low",
  "immediate_action": ["step 1", "step 2", "step 3"],
  "prevention": ["measure 1", "measure 2"],
  "estimated_impact": "description of blast radius and affected users/services",
  "summary": "one-sentence human-readable summary for Slack notification"
}

Severity guide:
- critical: service down, data loss risk, security breach
- high: partial outage, significant degradation
- medium: degraded performance, non-critical component failure
- low: warning, minor issue, no user impact"""


class AIAnalyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self._system_with_cache = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def analyze(self, pod_name: str, namespace: str, error_info: dict) -> Optional[dict]:
        context_text = "\n".join(error_info.get("context", []))
        user_message = (
            f"Pod: {pod_name}\n"
            f"Namespace: {namespace}\n"
            f"Error line: {error_info['error_line']}\n"
            f"Log context (lines before error):\n{context_text}"
        )

        try:
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=self._system_with_cache,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Claude returned non-JSON response, attempting extraction")
            return self._extract_json_fallback(raw)
        except anthropic.APIError as e:
            logger.error("Claude API error: %s", e)
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
            "root_cause": "Unable to determine root cause automatically",
            "severity": "high",
            "immediate_action": ["Check pod logs manually", "Inspect pod events with kubectl describe", "Check resource limits"],
            "prevention": ["Review application error handling", "Set up proper resource limits"],
            "estimated_impact": "Unknown — manual investigation required",
            "summary": f"Error detected: {error_info.get('error_line', 'unknown error')[:100]}",
        }
