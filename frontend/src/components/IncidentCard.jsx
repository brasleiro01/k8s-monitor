import { useState } from 'react';
import { resolveIncident, reopenIncident } from '../api.js';

function timeAgo(ts) {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function IncidentCard({ incident, onStatusChange }) {
  const [open, setOpen] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleResolve() {
    setLoading(true);
    try {
      await resolveIncident(incident.id);
      onStatusChange(incident.id, true);
    } finally {
      setLoading(false);
    }
  }

  async function handleReopen() {
    setLoading(true);
    try {
      await reopenIncident(incident.id);
      onStatusChange(incident.id, false);
    } finally {
      setLoading(false);
    }
  }

  const sev = incident.severity || 'high';

  return (
    <div className={`card${incident.resolved ? ' resolved' : ''}`}>
      <div className="card-header" onClick={() => setOpen(o => !o)}>
        <div className={`severity-bar ${sev}`} />
        <span className={`badge ${sev}`}>{sev}</span>

        <div className="card-title">
          <div className="pod">
            {incident.pod}
            <span className="ns"> · {incident.namespace}</span>
          </div>
          <div className="error-line">{incident.error_line}</div>
        </div>

        <div className="card-meta">
          <span className="time">{timeAgo(incident.timestamp)}</span>
          {incident.resolved && <span className="resolved-badge">Resolved</span>}
        </div>

        <span className={`chevron${open ? ' open' : ''}`}>▼</span>
      </div>

      {open && (
        <div className="card-body">
          {/* Error */}
          <div>
            <div className="section-title">Error</div>
            <div className="error-block">{incident.error_line}</div>
          </div>

          {/* AI Analysis */}
          <div>
            <div className="section-title">AI Analysis</div>
            <div className="analysis-grid">
              <div className="analysis-block">
                <div className="section-title">Root cause</div>
                <p className="root-cause">{incident.root_cause}</p>
                <p className="impact">{incident.estimated_impact}</p>
              </div>

              <div className="analysis-block">
                <div className="section-title">Immediate actions</div>
                <ul className="action-list">
                  {(incident.immediate_action || []).map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>

              <div className="analysis-block">
                <div className="section-title">Prevention</div>
                <ul className="action-list prevention">
                  {(incident.prevention || []).map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              </div>
            </div>
          </div>

          {/* Log context */}
          {(incident.context || []).length > 0 && (
            <div>
              <button className="context-toggle" onClick={() => setShowContext(s => !s)}>
                {showContext ? 'Hide' : 'Show'} log context ({incident.context.length} lines)
              </button>
              {showContext && (
                <div className="log-context">{incident.context.join('\n')}</div>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="card-actions">
            {!incident.resolved ? (
              <button className="btn btn-resolve" onClick={handleResolve} disabled={loading}>
                {loading ? 'Saving...' : '✓ Mark as resolved'}
              </button>
            ) : (
              <button className="btn btn-reopen" onClick={handleReopen} disabled={loading}>
                {loading ? 'Saving...' : '↩ Reopen'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
