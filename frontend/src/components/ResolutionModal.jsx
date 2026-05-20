import { useState, useEffect, useRef } from 'react';

export default function ResolutionModal({ incident, onConfirm, onCancel }) {
  const [description, setDescription] = useState('');
  const [time, setTime] = useState('');
  const textareaRef = useRef(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  function handleConfirm() {
    if (!description.trim()) {
      textareaRef.current?.focus();
      return;
    }
    onConfirm({ description: description.trim(), resolution_time: time });
  }

  function handleKeyDown(e) {
    if (e.key === 'Escape') onCancel();
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleConfirm();
  }

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onCancel()}>
      <div className="modal" onKeyDown={handleKeyDown}>
        <div className="modal-header">
          <span className="modal-icon">📋</span>
          <div>
            <h2 className="modal-title">Registrar resolução</h2>
            <p className="modal-subtitle">
              <code>{incident.pod}</code> · <code>{incident.namespace}</code>
            </p>
          </div>
        </div>

        <div className="modal-error-ref">
          <span className="modal-error-label">Erro resolvido</span>
          <span className="modal-error-text">{incident.error_line}</span>
        </div>

        <div className="modal-body">
          <label className="modal-label">
            O que foi feito para resolver o problema?
            <span className="required">*</span>
          </label>
          <textarea
            ref={textareaRef}
            className="modal-textarea"
            placeholder="Descreva a solução aplicada. Ex: Criado o diretório /var/lib/grafana/dashboards via kubectl exec e reiniciado o pod. Adicionado initContainer para criar o diretório automaticamente nas próximas inicializações."
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={5}
          />

          <label className="modal-label" style={{ marginTop: 14 }}>
            Tempo para resolução
          </label>
          <select className="modal-select" value={time} onChange={e => setTime(e.target.value)}>
            <option value="">Não informado</option>
            <option value="menos de 15 min">Menos de 15 min</option>
            <option value="15 a 30 min">15 a 30 min</option>
            <option value="30 min a 1h">30 min a 1h</option>
            <option value="1h a 2h">1h a 2h</option>
            <option value="mais de 2h">Mais de 2h</option>
          </select>
        </div>

        <div className="modal-footer">
          <button className="btn btn-cancel" onClick={onCancel}>
            Cancelar
          </button>
          <button
            className="btn btn-confirm"
            onClick={handleConfirm}
            disabled={!description.trim()}
          >
            ✓ Confirmar e gerar postmortem
          </button>
        </div>

        <p className="modal-hint">Ctrl+Enter para confirmar · Esc para cancelar</p>
      </div>
    </div>
  );
}
