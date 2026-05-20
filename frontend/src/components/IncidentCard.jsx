import { useState } from 'react';
import { resolveIncident, reopenIncident } from '../api.js';
import ResolutionModal from './ResolutionModal.jsx';
import PostmortemViewer from './PostmortemViewer.jsx';

function timeAgo(ts) {
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s atrás`;
  if (diff < 3600) return `${Math.floor(diff / 60)}min atrás`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h atrás`;
  return `${Math.floor(diff / 86400)}d atrás`;
}

const SEV_LABEL = { critical: 'Crítico', high: 'Alto', medium: 'Médio', low: 'Baixo' };

export default function IncidentCard({ incident, onStatusChange }) {
  const [open, setOpen] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [showViewer, setShowViewer] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleConfirmResolve({ description, resolution_time }) {
    setShowModal(false);
    setLoading(true);
    try {
      const result = await resolveIncident(incident.id, { description, resolution_time });
      onStatusChange(incident.id, true, result.postmortem_file);
    } finally {
      setLoading(false);
    }
  }

  async function handleReopen() {
    setLoading(true);
    try {
      await reopenIncident(incident.id);
      onStatusChange(incident.id, false, null);
    } finally {
      setLoading(false);
    }
  }

  function handleDownloadPostmortem() {
    window.open(`/api/incidents/${incident.id}/postmortem`, '_blank');
  }

  const sev = incident.severity || 'high';

  return (
    <>
      {showModal && (
        <ResolutionModal
          incident={incident}
          onConfirm={handleConfirmResolve}
          onCancel={() => setShowModal(false)}
        />
      )}
      {showViewer && (
        <PostmortemViewer
          incidentId={incident.id}
          filename={incident.postmortem_file}
          onClose={() => setShowViewer(false)}
        />
      )}

      <div className={`card${incident.resolved ? ' resolved' : ''}`}>
        <div className="card-header" onClick={() => setOpen(o => !o)}>
          <div className={`severity-bar ${sev}`} />
          <span className={`badge ${sev}`}>{SEV_LABEL[sev] || sev}</span>

          <div className="card-title">
            <div className="pod">
              {incident.pod}
              <span className="ns"> · {incident.namespace}</span>
            </div>
            <div className="error-line">{incident.error_line}</div>
          </div>

          <div className="card-meta">
            <span className="time">{timeAgo(incident.timestamp)}</span>
            {incident.resolved && <span className="resolved-badge">Resolvido</span>}
          </div>

          <span className={`chevron${open ? ' open' : ''}`}>▼</span>
        </div>

        {open && (
          <div className="card-body">
            <div>
              <div className="section-title">Erro detectado</div>
              <div className="error-block">{incident.error_line}</div>
            </div>

            <div>
              <div className="section-title">Análise de IA</div>
              <div className="analysis-grid">
                <div className="analysis-block">
                  <div className="section-title">Causa raiz</div>
                  <p className="root-cause">{incident.root_cause}</p>
                  <p className="impact">{incident.estimated_impact}</p>
                </div>

                <div className="analysis-block">
                  <div className="section-title">Ações imediatas</div>
                  <ul className="action-list">
                    {(incident.immediate_action || []).map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </div>

                <div className="analysis-block">
                  <div className="section-title">Prevenção</div>
                  <ul className="action-list prevention">
                    {(incident.prevention || []).map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>

            {incident.resolved && incident.resolution_description && (
              <div>
                <div className="section-title">✅ Solução aplicada</div>
                <div className="resolution-block">
                  <p className="resolution-text">{incident.resolution_description}</p>
                  {incident.resolution_time && (
                    <span className="resolution-time">⏱ {incident.resolution_time}</span>
                  )}
                </div>
              </div>
            )}

            {(incident.context || []).length > 0 && (
              <div>
                <button className="context-toggle" onClick={() => setShowContext(s => !s)}>
                  {showContext ? 'Ocultar' : 'Ver'} contexto do log ({incident.context.length} linhas)
                </button>
                {showContext && (
                  <div className="log-context">{incident.context.join('\n')}</div>
                )}
              </div>
            )}

            <div className="card-actions">
              {!incident.resolved ? (
                <button
                  className="btn btn-resolve"
                  onClick={() => setShowModal(true)}
                  disabled={loading}
                >
                  {loading ? 'Salvando...' : '✓ Marcar como resolvido'}
                </button>
              ) : (
                <>
                  <button className="btn btn-reopen" onClick={handleReopen} disabled={loading}>
                    {loading ? 'Salvando...' : '↩ Reabrir'}
                  </button>
                  {incident.postmortem_file && (
                    <>
                      <button className="btn btn-postmortem" onClick={() => setShowViewer(true)}>
                        👁 Visualizar
                      </button>
                      <button className="btn btn-postmortem" onClick={handleDownloadPostmortem}>
                        📄 Baixar
                      </button>
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
