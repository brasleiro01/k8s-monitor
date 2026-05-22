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
        cur.execute("""
            ALTER TABLE incidents
            ADD COLUMN IF NOT EXISTS previous_postmortem_id VARCHAR(64)
        """)
    logger.info("Schema do banco verificado/criado — tabela incidents pronta")


def ensure_schema() -> bool:
    """Garante que o schema existe. Retorna False se DB não disponível."""
    return _get_conn() is not None


def ensure_report_tables() -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            # Step 1 — create tables with original schema (safe if already exists)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS report_schedule (
                    id            INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    times_per_day INTEGER      NOT NULL DEFAULT 1,
                    enabled       BOOLEAN      NOT NULL DEFAULT false,
                    next_run      TIMESTAMPTZ,
                    updated_at    TIMESTAMPTZ  DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cluster_reports (
                    id             VARCHAR(36)  PRIMARY KEY,
                    created_at     TIMESTAMPTZ  DEFAULT NOW(),
                    namespaces     JSONB        NOT NULL DEFAULT '[]',
                    overall_health VARCHAR(20),
                    healthy_count  INTEGER      DEFAULT 0,
                    warning_count  INTEGER      DEFAULT 0,
                    critical_count INTEGER      DEFAULT 0,
                    total_pods     INTEGER      DEFAULT 0,
                    summary        TEXT,
                    details        JSONB        DEFAULT '[]'
                )
            """)
            # Step 2 — migrate: add new column if absent (runs after CREATE, before INSERT)
            cur.execute("""
                ALTER TABLE report_schedule
                ADD COLUMN IF NOT EXISTS scheduled_time VARCHAR(5) NOT NULL DEFAULT '08:00'
            """)
            # Step 3 — seed default row (scheduled_time column now guaranteed to exist)
            cur.execute("""
                INSERT INTO report_schedule (id, times_per_day, scheduled_time, enabled)
                VALUES (1, 1, '08:00', false) ON CONFLICT DO NOTHING
            """)
        logger.info("Tabelas de relatórios verificadas/criadas")
        return True
    except Exception as exc:
        logger.error("Erro ao criar tabelas de relatórios: %s", exc)
        return False


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
                    prevention, estimated_impact, summary, previous_postmortem_id
                ) VALUES (
                    %(id)s, %(pod)s, %(namespace)s, %(severity)s,
                    %(timestamp)s, %(timestamp)s,
                    %(error_line)s, %(context)s, %(root_cause)s, %(immediate_action)s,
                    %(prevention)s, %(estimated_impact)s, %(summary)s,
                    %(previous_postmortem_id)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    last_seen               = EXCLUDED.last_seen,
                    occurrences             = incidents.occurrences + 1,
                    severity                = EXCLUDED.severity,
                    root_cause              = EXCLUDED.root_cause,
                    immediate_action        = EXCLUDED.immediate_action,
                    prevention              = EXCLUDED.prevention,
                    estimated_impact        = EXCLUDED.estimated_impact,
                    summary                 = EXCLUDED.summary,
                    previous_postmortem_id  = COALESCE(incidents.previous_postmortem_id, EXCLUDED.previous_postmortem_id)
            """, {
                **incident,
                "context":                 json.dumps(incident.get("context", [])),
                "immediate_action":        json.dumps(incident.get("immediate_action", [])),
                "prevention":              json.dumps(incident.get("prevention", [])),
                "previous_postmortem_id":  incident.get("previous_postmortem_id"),
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

def update_incident_ai_analysis(incident_id: str, root_cause: str, summary: str,
                                 immediate_action: list, prevention: list,
                                 estimated_impact: str, severity: str) -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE incidents SET
                    root_cause       = %s,
                    summary          = %s,
                    immediate_action = %s,
                    prevention       = %s,
                    estimated_impact = %s,
                    severity         = %s
                WHERE id = %s AND resolved = false
            """, (
                root_cause, summary,
                json.dumps(immediate_action),
                json.dumps(prevention),
                estimated_impact, severity, incident_id,
            ))
        logger.info("Análise AI atualizada para incidente %s", incident_id[:8])
        return True
    except Exception as exc:
        logger.error("Erro ao atualizar análise AI: %s", exc)
        return False


def find_previous_postmortem(namespace: str, pod: str) -> Optional[dict]:
    """Return the most recent resolved incident with postmortem for the same ns/pod."""
    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, pod, postmortem_file, resolved_at
                FROM incidents
                WHERE namespace = %s
                  AND (pod = %s OR pod LIKE %s)
                  AND resolved = true
                  AND postmortem_file IS NOT NULL
                  AND postmortem_file != ''
                ORDER BY resolved_at DESC NULLS LAST
                LIMIT 1
            """, (namespace, pod, f"{pod}-%"))
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "pod": row[1], "postmortem_file": row[2]}
    except Exception as exc:
        logger.error("Erro ao buscar postmortem anterior: %s", exc)
        return None


def load_namespaces() -> list[str]:
    conn = _get_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT namespace FROM incidents ORDER BY namespace")
            return [row[0] for row in cur.fetchall()]
    except Exception as exc:
        logger.error("Erro ao carregar namespaces: %s", exc)
        return []


# ------------------------------------------------------------------ #
# Report schedule                                                      #
# ------------------------------------------------------------------ #

def get_report_schedule() -> dict | None:
    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT times_per_day, enabled, next_run, scheduled_time FROM report_schedule WHERE id = 1")
            row = cur.fetchone()
            if not row:
                return {"times_per_day": 1, "enabled": False, "next_run": None, "scheduled_time": "08:00"}
            return {
                "times_per_day":  row[0],
                "enabled":        row[1],
                "next_run":       row[2].isoformat() if row[2] else None,
                "scheduled_time": row[3] or "08:00",
            }
    except Exception as exc:
        logger.error("Erro ao obter schedule: %s", exc)
        return None


def set_report_schedule(times_per_day: int, enabled: bool, scheduled_time: str = "08:00") -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        from datetime import datetime, timedelta, timezone
        h, m = map(int, scheduled_time.split(":"))
        now = datetime.now(timezone.utc)
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO report_schedule (id, times_per_day, scheduled_time, enabled, next_run, updated_at)
                VALUES (1, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    times_per_day  = EXCLUDED.times_per_day,
                    scheduled_time = EXCLUDED.scheduled_time,
                    enabled        = EXCLUDED.enabled,
                    next_run       = EXCLUDED.next_run,
                    updated_at     = NOW()
            """, (times_per_day, scheduled_time, enabled, candidate))
        return True
    except Exception as exc:
        logger.error("Erro ao salvar schedule: %s", exc)
        return False


def update_schedule_next_run(next_run) -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE report_schedule SET next_run = %s WHERE id = 1",
                (next_run,),
            )
        return True
    except Exception as exc:
        logger.error("Erro ao atualizar next_run: %s", exc)
        return False


# ------------------------------------------------------------------ #
# Cluster reports                                                      #
# ------------------------------------------------------------------ #

def save_cluster_report(report: dict) -> bool:
    conn = _get_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cluster_reports (
                    id, namespaces, overall_health, healthy_count,
                    warning_count, critical_count, total_pods, summary, details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                report["id"],
                json.dumps(report["namespaces"]),
                report["overall_health"],
                report["healthy_count"],
                report["warning_count"],
                report["critical_count"],
                report["total_pods"],
                report["summary"],
                json.dumps(report.get("details", [])),
            ))
        return True
    except Exception as exc:
        logger.error("Erro ao salvar relatório: %s", exc)
        return False


def list_cluster_reports(limit: int = 30) -> list:
    conn = _get_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, created_at, namespaces, overall_health,
                       healthy_count, warning_count, critical_count, total_pods, summary
                FROM cluster_reports ORDER BY created_at DESC LIMIT %s
            """, (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.error("Erro ao listar relatórios: %s", exc)
        return []


def get_cluster_report(report_id: str) -> dict | None:
    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM cluster_reports WHERE id = %s", (report_id,))
            cols = [d[0] for d in cur.description]
            row  = cur.fetchone()
            return dict(zip(cols, row)) if row else None
    except Exception as exc:
        logger.error("Erro ao obter relatório: %s", exc)
        return None


def _row_to_incident(r: dict) -> dict:
    return {
        "id":                     r["id"],
        "pod":                    r["pod"],
        "namespace":              r["namespace"],
        "severity":               r["severity"],
        "timestamp":              r["last_seen"],
        "error_line":             r["error_line"],
        "context":                r["context"] or [],
        "root_cause":             r["root_cause"],
        "immediate_action":       r["immediate_action"] or [],
        "prevention":             r["prevention"] or [],
        "estimated_impact":       r["estimated_impact"],
        "summary":                r["summary"],
        "resolved":               r["resolved"],
        "resolvedAt":             r["resolved_at"].isoformat() if r["resolved_at"] else None,
        "resolution_description": r["resolution_description"],
        "resolution_time":        r["resolution_time"],
        "postmortem_file":        r["postmortem_file"],
        "_occurrences":           r["occurrences"],
        "previous_postmortem_id": r.get("previous_postmortem_id"),
    }
