import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import db
from config import (
    DISCORD_WEBHOOK_URL,
    GEMINI_API_KEY,
    GOOGLE_CLIENT_ID,
    LOG_LEVEL,
    NAMESPACES,
)
from cluster_overview import fetch_cluster_overview
from k8s_watcher import get_known_namespaces, load_k8s_config
from monitor import Monitor
from namespace_checker import check_namespace_stream

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# SSE broadcast (thread-safe: monitor thread → asyncio queues)       #
# ------------------------------------------------------------------ #
_loop: Optional[asyncio.AbstractEventLoop] = None
_sse_queues: set[asyncio.Queue] = set()


def broadcast(event_type: str, data: dict) -> None:
    if not _loop or not _sse_queues:
        return
    payload = {"event": event_type, "data": json.dumps(data)}
    for q in list(_sse_queues):
        _loop.call_soon_threadsafe(q.put_nowait, payload)


# ------------------------------------------------------------------ #
# DB polling → SSE push                                               #
# ------------------------------------------------------------------ #
async def _polling_task() -> None:
    known_ids: set[str] = set()
    rows = await asyncio.to_thread(db.load_incidents)
    for r in (rows or []):
        known_ids.add(r["id"])
    logger.info("[polling] estado inicial: %d incidente(s)", len(known_ids))

    while True:
        await asyncio.sleep(5)
        if not _sse_queues:
            continue
        rows = await asyncio.to_thread(db.load_incidents)
        if not rows:
            continue
        novos = 0
        for incident in rows:
            if incident["id"] not in known_ids:
                known_ids.add(incident["id"])
                broadcast("incident", {**incident, "resolved": False})
                novos += 1
        if novos:
            logger.info("[polling] %d novo(s) incidente(s) via SSE", novos)


# ------------------------------------------------------------------ #
# Lifespan                                                            #
# ------------------------------------------------------------------ #
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()

    missing = [n for n, v in [("GEMINI_API_KEY", GEMINI_API_KEY), ("DISCORD_WEBHOOK_URL", DISCORD_WEBHOOK_URL)] if not v]
    if missing:
        logger.error("Variáveis obrigatórias não configuradas: %s", ", ".join(missing))

    logger.info("[db] garantindo schema do banco...")
    ok = await asyncio.to_thread(db.ensure_schema)
    if not ok:
        logger.error("[db] banco de dados indisponível — incidentes não serão persistidos")

    load_k8s_config()
    monitor = Monitor(broadcast_fn=broadcast)
    t = threading.Thread(target=monitor.start, daemon=True)
    t.start()

    polling = asyncio.create_task(_polling_task())

    yield

    monitor.stop()
    polling.cancel()
    logger.info("Aplicação encerrada.")


# ------------------------------------------------------------------ #
# App                                                                 #
# ------------------------------------------------------------------ #
app = FastAPI(title="k8s-monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# ------------------------------------------------------------------ #
# API routes                                                          #
# ------------------------------------------------------------------ #
@app.get("/api/config")
async def api_config():
    return {"googleClientId": GOOGLE_CLIENT_ID, "namespaces": NAMESPACES}


@app.get("/api/incidents")
async def list_incidents():
    rows = await asyncio.to_thread(db.load_incidents)
    if rows is None:
        raise HTTPException(503, "Banco de dados indisponível")
    return rows


@app.get("/api/events")
async def sse_events(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues.add(queue)
    logger.info("[sse] cliente conectado — total: %d", len(_sse_queues))

    async def stream() -> AsyncGenerator[str, None]:
        yield "event: connected\ndata: {}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: {event['event']}\ndata: {event['data']}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _sse_queues.discard(queue)
            logger.info("[sse] cliente desconectado — total: %d", len(_sse_queues))

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


class ResolveBody(BaseModel):
    description: str = ""
    resolution_time: str = ""


@app.patch("/api/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str, body: ResolveBody):
    row = await asyncio.to_thread(db.find_incident_by_id, incident_id)
    if not row:
        raise HTTPException(404, "Incidente não encontrado")

    resolved_at = datetime.now(timezone.utc).isoformat()
    postmortem_content = _generate_postmortem(row, resolved_at, body.description, body.resolution_time)
    postmortem_file = f"postmortem_{resolved_at.replace('-','').replace(':','')[:15]}_{incident_id[:8]}.md"

    await asyncio.to_thread(
        db.resolve_incident,
        incident_id, resolved_at, body.description,
        body.resolution_time, postmortem_file, postmortem_content,
    )
    broadcast("update", {
        "type": "resolved", "id": incident_id, "resolvedAt": resolved_at,
        "postmortem_file": postmortem_file,
        "resolution_description": body.description,
        "resolution_time": body.resolution_time,
    })
    return {"ok": True, "postmortem_file": postmortem_file}


@app.patch("/api/incidents/{incident_id}/reopen")
async def reopen_incident(incident_id: str):
    await asyncio.to_thread(db.reopen_incident, incident_id)
    broadcast("update", {"type": "reopened", "id": incident_id})
    return {"ok": True}


@app.get("/api/namespaces")
async def list_namespaces():
    watched = get_known_namespaces()
    from_db = await asyncio.to_thread(db.load_namespaces)
    return sorted(set(watched) | set(from_db))


class CheckNamespaceBody(BaseModel):
    namespace: str


@app.post("/api/check-namespace")
async def check_namespace(body: CheckNamespaceBody):
    ns = body.namespace.strip()
    if not ns:
        raise HTTPException(400, "Namespace não informado")

    q: asyncio.Queue = asyncio.Queue()
    current_loop = asyncio.get_event_loop()

    def run():
        try:
            for chunk in check_namespace_stream(ns):
                current_loop.call_soon_threadsafe(q.put_nowait, chunk)
        except Exception as e:
            current_loop.call_soon_threadsafe(
                q.put_nowait,
                f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n",
            )
        finally:
            current_loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=run, daemon=True).start()

    async def stream():
        while True:
            chunk = await q.get()
            if chunk is None:
                break
            yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/cluster-overview")
async def cluster_overview():
    data = await asyncio.to_thread(fetch_cluster_overview)
    return data


@app.get("/api/incidents/{incident_id}/postmortem")
async def get_postmortem(incident_id: str, view: str = "0"):
    row = await asyncio.to_thread(db.get_postmortem_content, incident_id)
    if not row or not row.get("postmortem_content"):
        raise HTTPException(404, "Postmortem não gerado ainda")
    filename = row.get("postmortem_file") or f"postmortem_{incident_id[:8]}.md"
    disposition = "inline" if view == "1" else f'attachment; filename="{filename}"'
    return Response(
        content=row["postmortem_content"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


# ------------------------------------------------------------------ #
# SPA fallback — serve React build                                    #
# ------------------------------------------------------------------ #
PUBLIC_DIR = Path(__file__).parent / "public"


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    target = PUBLIC_DIR / full_path
    if target.exists() and target.is_file():
        return FileResponse(target)
    index = PUBLIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return Response("Frontend não encontrado", status_code=404)


# ------------------------------------------------------------------ #
# Postmortem markdown                                                 #
# ------------------------------------------------------------------ #
def _generate_postmortem(incident: dict, resolved_at: str, description: str, resolution_time: str) -> str:
    def fmt_ts(ts):
        return datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S") + " UTC"

    detected = fmt_ts(incident.get("last_seen") or incident.get("timestamp") or 0)
    resolved = resolved_at.replace("T", " ")[:19] + " UTC"
    actions = "\n".join(f"- [x] {a}" for a in (incident.get("immediate_action") or []))
    prevention = "\n".join(f"- {p}" for p in (incident.get("prevention") or []))
    context = "\n".join(incident.get("context") or []) or "(sem contexto capturado)"
    pod = incident.get("pod", "")
    ns = incident.get("namespace", "")
    sev = incident.get("severity", "")
    inc_id = incident.get("id", "")
    error_line = incident.get("error_line", "")

    backtick3 = "```"

    return f"""# Postmortem — {pod} — {detected}

## Resumo do Incidente

| Campo | Valor |
|-------|-------|
| **Pod** | `{pod}` |
| **Namespace** | `{ns}` |
| **Severidade** | `{sev}` |
| **Detectado em** | {detected} |
| **Resolvido em** | {resolved} |
| **Hash do erro** | `{inc_id}` |
| **Status** | Resolvido ✅ |

## Detalhe do Erro

{backtick3}
{error_line}
{backtick3}

## Contexto do Log

{backtick3}
{context}
{backtick3}

## Análise de IA

### Causa Raiz

{incident.get('root_cause', '')}

### Impacto Estimado

{incident.get('estimated_impact', '')}

### Resumo

{incident.get('summary', '')}

## ✅ Solução Aplicada

{description or '_Não informado_'}

{f'**Tempo de resolução:** {resolution_time}' if resolution_time else ''}

## Ações Imediatas

{actions or '- [x] Investigação manual realizada'}

## Medidas de Prevenção

{prevention or '- Revisar tratamento de erros da aplicação'}

## Checklist Pós-Incidente

- [x] Causa raiz identificada
- [x] Incidente resolvido
- [ ] Medidas de prevenção implementadas
- [ ] Monitoramento/alertas revisados
"""
