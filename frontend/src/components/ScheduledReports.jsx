import { useState, useEffect, useCallback } from 'react';
import {
  fetchReportSchedule, setReportSchedule,
  fetchClusterReports, fetchClusterReportDetail,
} from '../api.js';

const H_ICON  = { healthy: '✅', warning: '⚠️', critical: '🔴' };

function localTimeToUTC(timeStr) {
  const [h, m] = timeStr.split(':').map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

function utcTimeToLocal(timeStr) {
  if (!timeStr) return '08:00';
  const [h, m] = timeStr.split(':').map(Number);
  const d = new Date();
  d.setUTCHours(h, m, 0, 0);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function fmtDt(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function fmtRelative(iso) {
  if (!iso) return null;
  const diff = new Date(iso) - Date.now();
  if (diff <= 0) return 'agora';
  const m = Math.floor(diff / 60000);
  if (m < 60) return `em ${m} min`;
  const h = Math.floor(m / 60);
  if (h < 24) return `em ${h}h`;
  return `em ${Math.floor(h / 24)}d`;
}

function CountBadge({ count, type }) {
  if (!count) return null;
  return <span className={`sr-badge sr-badge-${type}`}>{count}</span>;
}

function PodRow({ pod }) {
  return (
    <div className={`sr-pod sr-pod-${pod.health}`}>
      <span className="sr-pod-dot" />
      <span className="sr-pod-name">{pod.pod}</span>
      <span className="sr-pod-phase">{pod.phase}</span>
      {pod.restarts > 0 && <span className="sr-pod-restarts">↺{pod.restarts}</span>}
      {pod.issues?.length > 0 && (
        <ul className="sr-pod-issues">
          {pod.issues.map((iss, i) => <li key={i}>{iss}</li>)}
        </ul>
      )}
    </div>
  );
}

function NsSection({ ns }) {
  const [open, setOpen] = useState(ns.overall_health !== 'healthy');
  return (
    <div className={`sr-ns sr-ns-${ns.overall_health}`}>
      <button className="sr-ns-header" onClick={() => setOpen(o => !o)}>
        <span>{H_ICON[ns.overall_health]}</span>
        <span className="sr-ns-name">{ns.namespace}</span>
        <CountBadge count={ns.critical_count} type="critical" />
        <CountBadge count={ns.warning_count}  type="warning"  />
        <CountBadge count={ns.healthy_count}  type="healthy"  />
        <span className="sr-expand">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="sr-ns-pods">
          {ns.pods?.map((pod, i) => <PodRow key={i} pod={pod} />)}
        </div>
      )}
    </div>
  );
}

function ReportRow({ report, newReportId }) {
  const [open, setOpen]       = useState(report.id === newReportId);
  const [detail, setDetail]   = useState(null);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    if (!open && !detail) {
      setLoading(true);
      try {
        const d = await fetchClusterReportDetail(report.id);
        setDetail(d);
      } catch { /* ignore */ }
      setLoading(false);
    }
    setOpen(o => !o);
  }

  return (
    <div className={`sr-report sr-report-${report.overall_health}`}>
      <button className="sr-report-header" onClick={toggle}>
        <span className="sr-report-icon">{H_ICON[report.overall_health]}</span>
        <span className="sr-report-time">{fmtDt(report.created_at)}</span>
        <span className="sr-report-ns">
          {(report.namespaces || []).join(', ')}
        </span>
        <div className="sr-report-counts">
          <CountBadge count={report.critical_count} type="critical" />
          <CountBadge count={report.warning_count}  type="warning"  />
          <CountBadge count={report.healthy_count}  type="healthy"  />
        </div>
        <span className="sr-expand">
          {loading ? <span className="nsc-spinner-sm" /> : open ? '▾' : '▸'}
        </span>
      </button>

      {open && detail && (
        <div className="sr-report-body">
          <p className="sr-summary">{detail.summary}</p>
          {(detail.details || []).map((ns, i) => (
            <NsSection key={i} ns={ns} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ScheduledReports({ onClose, newReportId }) {
  const [schedule,   setSchedule]  = useState(null);
  const [reports,    setReports]   = useState([]);
  const [saving,     setSaving]    = useState(false);
  const [localTime,  setLocalTime] = useState('08:00');
  const [localOn,    setLocalOn]   = useState(false);

  const loadAll = useCallback(async () => {
    const [sched, reps] = await Promise.all([
      fetchReportSchedule().catch(() => null),
      fetchClusterReports().catch(() => []),
    ]);
    if (sched) {
      setSchedule(sched);
      setLocalTime(utcTimeToLocal(sched.scheduled_time || '08:00'));
      setLocalOn(sched.enabled);
    }
    setReports(reps);
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  async function handleSave() {
    setSaving(true);
    try {
      await setReportSchedule(localTimeToUTC(localTime), localOn);
      await loadAll();
    } catch { /* ignore */ }
    setSaving(false);
  }

  const savedLocalTime = schedule ? utcTimeToLocal(schedule.scheduled_time || '08:00') : '08:00';
  const dirty = schedule && (localTime !== savedLocalTime || localOn !== schedule.enabled);

  return (
    <div className="sr-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="sr-drawer">
        <div className="sr-header">
          <span className="sr-title">📊 Relatórios Agendados</span>
          <button className="nsc-close" onClick={onClose}>✕</button>
        </div>

        <div className="sr-body">
          <div className="sr-config">
            <div className="sr-config-title">Agendamento</div>

            <div className="sr-config-row">
              <label className="sr-toggle-label">
                <span>Ativo</span>
                <button
                  className={`sr-toggle ${localOn ? 'on' : ''}`}
                  onClick={() => setLocalOn(o => !o)}
                >
                  <span className="sr-toggle-knob" />
                </button>
              </label>
            </div>

            <div className="sr-config-row">
              <label className="sr-label">Horário</label>
              <input
                type="time"
                className="sr-time-input"
                value={localTime}
                onChange={e => setLocalTime(e.target.value)}
              />
              <span className="sr-tz-note">horário local</span>
            </div>

            {schedule?.next_run && (
              <div className="sr-next-run">
                {localOn ? 'Próxima execução:' : 'Executaria em:'}
                {' '}<strong>{fmtDt(schedule.next_run)}</strong>
                <span className="sr-next-rel"> ({fmtRelative(schedule.next_run)})</span>
              </div>
            )}

            <button
              className="sr-btn-save"
              onClick={handleSave}
              disabled={saving || !dirty}
            >
              {saving ? 'Salvando...' : 'Salvar'}
            </button>
          </div>

          <div className="sr-history">
            <div className="sr-history-title">
              Histórico
              {reports.length > 0 && <span className="sr-history-count">{reports.length}</span>}
            </div>

            {reports.length === 0 ? (
              <div className="sr-empty">
                Nenhum relatório gerado ainda.
                {!localOn && <span> Ative o agendamento para começar.</span>}
              </div>
            ) : (
              reports.map(r => (
                <ReportRow key={r.id} report={r} newReportId={newReportId} />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
