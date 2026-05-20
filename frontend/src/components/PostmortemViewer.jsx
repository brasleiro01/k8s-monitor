import { useEffect, useState } from 'react';
import { marked } from 'marked';

export default function PostmortemViewer({ incidentId, filename, onClose }) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch(`/api/incidents/${incidentId}/postmortem?view=1`)
      .then(r => {
        if (!r.ok) throw new Error('Não foi possível carregar o postmortem.');
        return r.text();
      })
      .then(text => { setContent(text); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [incidentId]);

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') onClose(); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  function handleBackdrop(e) {
    if (e.target === e.currentTarget) onClose();
  }

  return (
    <div className="modal-backdrop" onClick={handleBackdrop}>
      <div className="pm-viewer-modal">
        <div className="pm-viewer-header">
          <div>
            <div className="pm-viewer-title">📄 Postmortem</div>
            {filename && <div className="pm-viewer-filename">{filename}</div>}
          </div>
          <div className="pm-viewer-actions">
            <a
              className="btn btn-postmortem"
              href={`/api/incidents/${incidentId}/postmortem`}
              target="_blank"
              rel="noreferrer"
              title="Baixar arquivo"
            >
              ⬇ Baixar
            </a>
            <button className="drawer-close" onClick={onClose} title="Fechar (Esc)">✕</button>
          </div>
        </div>

        <div className="pm-viewer-body">
          {loading && <div className="pm-viewer-loading">Carregando...</div>}
          {error && <div className="pm-viewer-error">{error}</div>}
          {!loading && !error && (
            <div
              className="pm-viewer-content"
              dangerouslySetInnerHTML={{ __html: marked.parse(content) }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
