export default function StatsBar({ incidents }) {
  const total = incidents.length;
  const open = incidents.filter(i => !i.resolved).length;
  const critical = incidents.filter(i => i.severity === 'critical' && !i.resolved).length;
  const high = incidents.filter(i => i.severity === 'high' && !i.resolved).length;
  const medium = incidents.filter(i => i.severity === 'medium' && !i.resolved).length;

  return (
    <div className="stats">
      <div className="stat-card">
        <span className="label">Total</span>
        <span className="value">{total}</span>
      </div>
      <div className="stat-card open">
        <span className="label">Open</span>
        <span className="value">{open}</span>
      </div>
      <div className="stat-card critical">
        <span className="label">Critical</span>
        <span className="value">{critical}</span>
      </div>
      <div className="stat-card high">
        <span className="label">High</span>
        <span className="value">{high}</span>
      </div>
      <div className="stat-card medium">
        <span className="label">Medium</span>
        <span className="value">{medium}</span>
      </div>
    </div>
  );
}
