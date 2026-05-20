import json
import os
import logging
from datetime import datetime, timezone

from config import POSTMORTEM_DIR

logger = logging.getLogger(__name__)


class PostmortemGenerator:
    def __init__(self):
        os.makedirs(POSTMORTEM_DIR, exist_ok=True)

    def generate(self, pod_name: str, namespace: str, error_info: dict, analysis: dict) -> str:
        error_hash = error_info.get("error_hash", "unknown")[:8]
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")

        md_filename = f"postmortem_{timestamp_str}_{error_hash}.md"
        md_filepath = os.path.join(POSTMORTEM_DIR, md_filename)

        json_filename = f"incident_{timestamp_str}_{error_hash}.json"
        json_filepath = os.path.join(POSTMORTEM_DIR, json_filename)

        content = self._render(pod_name, namespace, error_info, analysis, now)
        with open(md_filepath, "w", encoding="utf-8") as f:
            f.write(content)

        incident = {
            "id": error_info.get("error_hash", f"{timestamp_str}_{error_hash}"),
            "pod": pod_name,
            "namespace": namespace,
            "severity": analysis.get("severity", "high"),
            "timestamp": error_info.get("timestamp", now.timestamp()),
            "error_line": error_info.get("error_line", ""),
            "context": error_info.get("context", []),
            "root_cause": analysis.get("root_cause", ""),
            "immediate_action": analysis.get("immediate_action", []),
            "prevention": analysis.get("prevention", []),
            "estimated_impact": analysis.get("estimated_impact", ""),
            "summary": analysis.get("summary", ""),
            "postmortem_file": md_filename,
        }
        with open(json_filepath, "w", encoding="utf-8") as f:
            json.dump(incident, f, ensure_ascii=False, indent=2)

        logger.info("Postmortem saved: %s", md_filepath)
        return md_filepath

    def _render(self, pod_name: str, namespace: str, error_info: dict, analysis: dict, now: datetime) -> str:
        severity = analysis.get("severity", "unknown")
        detected_at = datetime.fromtimestamp(
            error_info.get("timestamp", now.timestamp()), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")

        immediate_actions = "\n".join(
            f"- [ ] {step}" for step in analysis.get("immediate_action", [])
        )
        prevention = "\n".join(
            f"- {measure}" for measure in analysis.get("prevention", [])
        )
        context_block = "\n".join(error_info.get("context", []))

        return f"""# Postmortem — {pod_name} — {detected_at}

## Incident Summary

| Field | Value |
|-------|-------|
| **Pod** | `{pod_name}` |
| **Namespace** | `{namespace}` |
| **Severity** | `{severity}` |
| **Detected at** | {detected_at} |
| **Postmortem created** | {now.strftime("%Y-%m-%d %H:%M:%S UTC")} |
| **Error hash** | `{error_info.get("error_hash", "N/A")}` |
| **Status** | Open |

## Error Details

```
{error_info.get("error_line", "N/A")}
```

## Log Context (lines before error)

```
{context_block if context_block else "(no context captured)"}
```

## AI Analysis

### Root Cause

{analysis.get("root_cause", "Not determined")}

### Estimated Impact

{analysis.get("estimated_impact", "Unknown")}

### Summary

{analysis.get("summary", "")}

## Immediate Actions

{immediate_actions if immediate_actions else "- [ ] Investigate manually"}

## Prevention Measures

{prevention if prevention else "- Review application error handling"}

## Timeline

| Time | Event |
|------|-------|
| {detected_at} | Error detected in pod logs |
| {now.strftime("%Y-%m-%d %H:%M:%S UTC")} | Postmortem created, Slack notification sent |
| _TBD_ | Root cause confirmed |
| _TBD_ | Fix deployed |
| _TBD_ | Incident closed |

## Post-Incident Checklist

- [ ] Root cause confirmed
- [ ] Fix implemented and deployed
- [ ] Monitoring/alerting reviewed
- [ ] Prevention measures scheduled
- [ ] Stakeholders notified
- [ ] Incident closed
"""
