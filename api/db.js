'use strict';
/**
 * Camada Postgres — opcional.
 * Se DATABASE_URL não estiver configurado, todos os métodos retornam null/false
 * e o server.js usa os arquivos JSON como fallback.
 */
const DATABASE_URL = process.env.DATABASE_URL || '';
const DB_SSL = process.env.DB_SSL === '1';

let pool = null;
let schemaReady = false;

function getPool() {
  if (!DATABASE_URL) return null;
  if (!pool) {
    const { Pool } = require('pg');
    pool = new Pool({
      connectionString: DATABASE_URL,
      ssl: DB_SSL ? { rejectUnauthorized: false } : false,
      max: 10,
      idleTimeoutMillis: 30000,
    });
    pool.on('error', err => console.error('[db] pool error:', err.message));
    ensureSchema().catch(e => console.error('[db] schema error:', e.message));
  }
  return pool;
}

async function ensureSchema() {
  if (schemaReady) return;
  const p = getPool();
  if (!p) return;
  await p.query(`
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
  `);
  schemaReady = true;
  console.log('[db] schema ok');
}

async function loadIncidents() {
  const p = getPool();
  if (!p) return null;
  try {
    const { rows } = await p.query(
      'SELECT * FROM incidents ORDER BY last_seen DESC'
    );
    return rows.map(r => ({
      id:                    r.id,
      pod:                   r.pod,
      namespace:             r.namespace,
      severity:              r.severity,
      timestamp:             r.last_seen,
      error_line:            r.error_line,
      context:               r.context || [],
      root_cause:            r.root_cause,
      immediate_action:      r.immediate_action || [],
      prevention:            r.prevention || [],
      estimated_impact:      r.estimated_impact,
      summary:               r.summary,
      resolved:              r.resolved,
      resolvedAt:            r.resolved_at ? r.resolved_at.toISOString() : null,
      resolution_description: r.resolution_description || null,
      resolution_time:       r.resolution_time || null,
      postmortem_file:       r.postmortem_file || null,
      _occurrences:          r.occurrences,
    }));
  } catch (e) {
    console.error('[db] loadIncidents error:', e.message);
    return null;
  }
}

async function findIncidentById(id) {
  const p = getPool();
  if (!p) return null;
  try {
    const { rows } = await p.query('SELECT * FROM incidents WHERE id = $1', [id]);
    return rows[0] || null;
  } catch (e) {
    console.error('[db] findById error:', e.message);
    return null;
  }
}

async function resolveIncident(id, resolvedAt, description, resolutionTime, postmortemFile, postmortemContent) {
  const p = getPool();
  if (!p) return false;
  try {
    await p.query(`
      UPDATE incidents SET
        resolved               = true,
        resolved_at            = $2,
        resolution_description = $3,
        resolution_time        = $4,
        postmortem_file        = $5,
        postmortem_content     = $6
      WHERE id = $1
    `, [id, resolvedAt, description, resolutionTime, postmortemFile, postmortemContent]);
    return true;
  } catch (e) {
    console.error('[db] resolve error:', e.message);
    return false;
  }
}

async function reopenIncident(id) {
  const p = getPool();
  if (!p) return false;
  try {
    await p.query(`
      UPDATE incidents SET
        resolved = false, resolved_at = NULL,
        resolution_description = NULL, resolution_time = NULL,
        postmortem_file = NULL, postmortem_content = NULL
      WHERE id = $1
    `, [id]);
    return true;
  } catch (e) {
    console.error('[db] reopen error:', e.message);
    return false;
  }
}

async function getPostmortemContent(id) {
  const p = getPool();
  if (!p) return null;
  try {
    const { rows } = await p.query(
      'SELECT postmortem_content, postmortem_file FROM incidents WHERE id = $1',
      [id]
    );
    return rows[0] || null;
  } catch (e) {
    console.error('[db] getPostmortem error:', e.message);
    return null;
  }
}

module.exports = {
  isConfigured: () => !!DATABASE_URL,
  getPool,
  loadIncidents,
  findIncidentById,
  resolveIncident,
  reopenIncident,
  getPostmortemContent,
};
