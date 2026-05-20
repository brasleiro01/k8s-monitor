"""
Camada de persistência Postgres — obrigatória.
Usada tanto pelo monitor (escrita) quanto pela API FastAPI (leitura/escrita).
"""
import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_SSL = os.environ.get("DB_SSL", "0") == "1"

_lock = threading.Lock()
_conn = None
_schema_created = False


def _get_conn():
    global _conn, _schema_created
    if not DATABASE_URL:
        logger.warning("DATABASE_URL não configurado — banco de dados desabilitado")
        return None
    with _lock:
        try:
            import psycopg2

            if _conn is None or _conn.closed:
                masked = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
                logger.info("Conectando ao Postgres: ...@%s", masked)
                kwargs = {"dsn": DATABASE_URL}
                if DB_SSL:
                    kwargs["sslmode"] = "require"
                _conn = psycopg2.connect(**kwargs)
                _conn.autocommit = True
                logger.info("Conexão com Postgres estabelecida")

            if not _schema_created:
                _create_schema(_conn)
                _schema_created = True

            return _conn
        except Exception as exc:
            logger.error("Falha ao conectar ao banco de dados: %s", exc)
            _conn = None
            return None


def _create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id                     VARCHAR(64)  PRIMARY KEY,
                pod                    VARCHAR(255) NOT NULL,
                namespace              VARCHAR(255) NOT NULL,
                severity               VARCHAR(20)  NOT NULL,
                first_seen             DOUBLE PRECISION NOT NULL,
                last_seen              DOUBLE PRECISION NOT NULL,
                occurrences            INTEGER      DEFAULT 1,
                error_line             TEXT         NOT NULL,
                context                JSONB        DEFAULT '[]',
                root_cause             TEXT,
                immediate_action       JSONB        DEFAULT '[]',
                prevention             JSONB        DEFAULT '[]',
                estimated_impact       TEXT,
                summary                TEXT,
                resolved               BOOLEAN      DEFAULT false,
                resolved_at            TIMESTAMPTZ,
                resolution_description TEXT,
                resolution_time        VARCHAR(100),
                postmortem_content     TEXT,
                postmortem_file        VARCHAR(255),
                created_at             TIMESTAMPTZ  DEFAULT NOW()
            )
        """)
    logger.info("Schema do banco verificado/criado — tabela incidents pronta")


def ensure_schema() -> bool:
    """Garante que o schema existe. Retorna False se DB não disponível."""
    return _get_conn() is not None


def is_configured() -> bool:
    return bool(DATABASE_URL)


# ------------------------------------------------------------------ #
# Escrita (usada pelo monitor)                                        #
# ------------------------------------------------------------------ #

def upsert_incident(incident: dict) -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO incidents (
                    id, pod, namespace, severity, first_seen, last_seen,
                    error_line, context, root_cause, immediate_action,
                    prevention, estimated_impact, summary
                ) VALUES (
                    %(id)s, %(pod)s, %(namespace)s, %(severity)s,
                    %(timestamp)s, %(timestamp)s,
                    %(error_line)s, %(context)s, %(root_cause)s, %(immediate_action)s,
                    %(prevention)s, %(estimated_impact)s, %(summary)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    last_seen        = EXCLUDED.last_seen,
                    occurrences      = incidents.occurrences + 1,
                    severity         = EXCLUDED.severity,
                    root_cause       = EXCLUDED.root_cause,
                    immediate_action = EXCLUDED.immediate_action,
                    prevention       = EXCLUDED.prevention,
                    estimated_impact = EXCLUDED.estimated_impact,
                    summary          = EXCLUDED.summary
            """, {
                **incident,
                "context":          json.dumps(incident.get("context", [])),
                "immediate_action": json.dumps(incident.get("immediate_action", [])),
                "prevention":       json.dumps(incident.get("prevention", [])),
            })
        logger.info("Incidente salvo: %s (pod=%s)", incident["id"][:8], incident.get("pod"))
        return True
    except Exception as exc:
        logger.error("Erro ao salvar incidente: %s", exc)
        return False


# ------------------------------------------------------------------ #
# Leitura (usada pela API)                                            #
# ------------------------------------------------------------------ #

def load_incidents() -> Optional[list]:
    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM incidents ORDER BY last_seen DESC")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        result = [_row_to_incident(r) for r in rows]
        logger.debug("load_incidents: %d incidente(s)", len(result))
        return result
    except Exception as exc:
        logger.error("Erro ao carregar incidentes: %s", exc)
        return None


def find_incident_by_id(incident_id: str) -> Optional[dict]:
    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM incidents WHERE id = %s", (incident_id,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip(cols, row))
    except Exception as exc:
        logger.error("Erro ao buscar incidente %s: %s", incident_id[:8], exc)
        return None


def resolve_incident(incident_id: str, resolved_at: str, description: str,
                     resolution_time: str, postmortem_file: str, postmortem_content: str) -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE incidents SET
                    resolved               = true,
                    resolved_at            = %s,
                    resolution_description = %s,
                    resolution_time        = %s,
                    postmortem_file        = %s,
                    postmortem_content     = %s
                WHERE id = %s
            """, (resolved_at, description, resolution_time,
                  postmortem_file, postmortem_content, incident_id))
        logger.info("Incidente %s resolvido", incident_id[:8])
        return True
    except Exception as exc:
        logger.error("Erro ao resolver incidente: %s", exc)
        return False


def reopen_incident(incident_id: str) -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE incidents SET
                    resolved = false, resolved_at = NULL,
                    resolution_description = NULL, resolution_time = NULL,
                    postmortem_file = NULL, postmortem_content = NULL
                WHERE id = %s
            """, (incident_id,))
        logger.info("Incidente %s reaberto", incident_id[:8])
        return True
    except Exception as exc:
        logger.error("Erro ao reabrir incidente: %s", exc)
        return False


def get_postmortem_content(incident_id: str) -> Optional[dict]:
    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT postmortem_content, postmortem_file FROM incidents WHERE id = %s",
                (incident_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"postmortem_content": row[0], "postmortem_file": row[1]}
    except Exception as exc:
        logger.error("Erro ao obter postmortem: %s", exc)
        return None


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #

def _row_to_incident(r: dict) -> dict:
    return {
        "id":                    r["id"],
        "pod":                   r["pod"],
        "namespace":             r["namespace"],
        "severity":              r["severity"],
        "timestamp":             r["last_seen"],
        "error_line":            r["error_line"],
        "context":               r["context"] or [],
        "root_cause":            r["root_cause"],
        "immediate_action":      r["immediate_action"] or [],
        "prevention":            r["prevention"] or [],
        "estimated_impact":      r["estimated_impact"],
        "summary":               r["summary"],
        "resolved":              r["resolved"],
        "resolvedAt":            r["resolved_at"].isoformat() if r["resolved_at"] else None,
        "resolution_description": r["resolution_description"],
        "resolution_time":       r["resolution_time"],
        "postmortem_file":       r["postmortem_file"],
        "_occurrences":          r["occurrences"],
    }
