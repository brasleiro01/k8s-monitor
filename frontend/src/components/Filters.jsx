export default function Filters({ incidents, filters, onChange }) {
  const namespaces = [...new Set(incidents.map(i => i.namespace))].sort();

  function clearDates() {
    onChange({ ...filters, dateFrom: '', dateTo: '' });
  }

  const hasDateFilter = filters.dateFrom || filters.dateTo;

  return (
    <div className="filters">
      <div className="filter-group">
        <label>Severity</label>
        <select value={filters.severity} onChange={e => onChange({ ...filters, severity: e.target.value })}>
          <option value="">Todos</option>
          <option value="critical">Crítico</option>
          <option value="high">Alto</option>
          <option value="medium">Médio</option>
          <option value="low">Baixo</option>
        </select>
      </div>

      <div className="filter-group">
        <label>Status</label>
        <select value={filters.status} onChange={e => onChange({ ...filters, status: e.target.value })}>
          <option value="">Todos</option>
          <option value="open">Abertos</option>
          <option value="resolved">Resolvidos</option>
        </select>
      </div>

      <div className="filter-group">
        <label>Namespace</label>
        <select value={filters.namespace} onChange={e => onChange({ ...filters, namespace: e.target.value })}>
          <option value="">Todos</option>
          {namespaces.map(ns => (
            <option key={ns} value={ns}>{ns}</option>
          ))}
        </select>
      </div>

      <div className="filter-group filter-group-dates">
        <label>📅 Período</label>
        <input
          type="date"
          className="filter-date"
          value={filters.dateFrom}
          max={filters.dateTo || undefined}
          onChange={e => onChange({ ...filters, dateFrom: e.target.value })}
          title="Data inicial"
        />
        <span className="date-sep">→</span>
        <input
          type="date"
          className="filter-date"
          value={filters.dateTo}
          min={filters.dateFrom || undefined}
          onChange={e => onChange({ ...filters, dateTo: e.target.value })}
          title="Data final"
        />
        {hasDateFilter && (
          <button className="btn-clear-date" onClick={clearDates} title="Limpar período">✕</button>
        )}
      </div>

      <div className="filter-group">
        <input
          type="text"
          placeholder="Buscar pod ou erro..."
          value={filters.search}
          onChange={e => onChange({ ...filters, search: e.target.value })}
        />
      </div>
    </div>
  );
}
