export default function Filters({ incidents, filters, onChange }) {
  const namespaces = [...new Set(incidents.map(i => i.namespace))].sort();

  return (
    <div className="filters">
      <div className="filter-group">
        <label>Severity</label>
        <select value={filters.severity} onChange={e => onChange({ ...filters, severity: e.target.value })}>
          <option value="">All</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      <div className="filter-group">
        <label>Status</label>
        <select value={filters.status} onChange={e => onChange({ ...filters, status: e.target.value })}>
          <option value="">All</option>
          <option value="open">Open</option>
          <option value="resolved">Resolved</option>
        </select>
      </div>

      <div className="filter-group">
        <label>Namespace</label>
        <select value={filters.namespace} onChange={e => onChange({ ...filters, namespace: e.target.value })}>
          <option value="">All</option>
          {namespaces.map(ns => (
            <option key={ns} value={ns}>{ns}</option>
          ))}
        </select>
      </div>

      <div className="filter-group">
        <input
          type="text"
          placeholder="Search pod or error..."
          value={filters.search}
          onChange={e => onChange({ ...filters, search: e.target.value })}
        />
      </div>
    </div>
  );
}
