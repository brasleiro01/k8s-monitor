import { useEffect, useRef } from 'react';

const SEV_COLORS = {
  critical: 'var(--critical)',
  high: 'var(--high)',
  medium: 'var(--medium)',
  low: 'var(--low)',
};

const SEV_LABEL = { critical: 'Crítico', high: 'Alto', medium: 'Médio', low: 'Baixo' };

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

export default function PostmortemsDrawer({ incidents, onClose }) {
  const drawerRef = useRef(null);

  const postmortems = incidents
    .filter(i => i.resolved && i.postmortem_file)
    .sort((a, b) => new Date(b.resolvedAt) - new Date(a.resolvedAt));

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose(); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Fecha ao clicar fora do drawer
  function handleBackdrop(e) {
    if (drawerRef.current && !drawerRef.current.contains(e.target)) onClose();
  }

  return (
    <div className="drawer-backdrop" onClick={handleBackdrop}>
      <aside className="drawer" ref={drawerRef}>
        <div className="drawer-header">
          <div>
            <h2 className="drawer-title">📋 Histórico de Resoluções</h2>
            <p className="drawer-subtitle">{postmortems.length} postmortem{postmortems.length !== 1 ? 's' : ''} gerado{postmortems.length !== 1 ? 's' : ''}</p>
          </div>
          <button className="drawer-close" onClick={onClose} title="Fechar (Esc)">✕</button>
        </div>

        <div className="drawer-body">
          {postmortems.length === 0 ? (
            <div className="drawer-empty">
              <p>Nenhum postmortem gerado ainda.</p>
              <p>Resolva um incidente para gerar o primeiro.</p>
            </div>
          ) : (
            postmortems.map(i => (
              <div className="pm-card" key={i.id}>
                <div className="pm-card-top">
                  <span
                    className="pm-badge"
                    style={{ color: SEV_COLORS[i.severity], borderColor: SEV_COLORS[i.severity] + '44', background: SEV_COLORS[i.severity] + '15' }}
                  >
                    {SEV_LABEL[i.severity] || i.severity}
                  </span>
                  <span className="pm-date">{fmtDate(i.resolvedAt)}</span>
                </div>

                <div className="pm-pod">
                  {i.pod}
                  <span className="pm-ns"> · {i.namespace}</span>
                </div>

                <div className="pm-error">{i.error_line}</div>

                {i.resolution_description && (
                  <div className="pm-resolution">
                    <span className="pm-resolution-label">✅ Solução</span>
                    <p className="pm-resolution-text">{i.resolution_description}</p>
                    {i.resolution_time && (
                      <span className="pm-resolution-time">⏱ {i.resolution_time}</span>
                    )}
                  </div>
                )}

                <a
                  className="pm-download"
                  href={`/api/incidents/${i.id}/postmortem`}
                  target="_blank"
                  rel="noreferrer"
                >
                  📄 Baixar postmortem (.md)
                </a>
              </div>
            ))
          )}
        </div>
      </aside>
    </div>
  );
}
