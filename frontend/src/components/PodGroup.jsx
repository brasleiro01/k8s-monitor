import { useState } from 'react';
import IncidentCard from './IncidentCard.jsx';

const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

function worstSeverity(incidents) {
  return incidents.reduce((worst, i) => {
    return (SEV_ORDER[i.severity] ?? 4) < (SEV_ORDER[worst] ?? 4) ? i.severity : worst;
  }, 'low');
}

export default function PodGroup({ pod, namespace, incidents, onStatusChange }) {
  const [expanded, setExpanded] = useState(true);

  const openCount = incidents.filter(i => !i.resolved).length;
  const sev = worstSeverity(incidents.filter(i => !i.resolved).length > 0
    ? incidents.filter(i => !i.resolved)
    : incidents
  );

  return (
    <div className="pod-group">
      <div className="pod-group-header" onClick={() => setExpanded(e => !e)}>
        <div className={`severity-bar ${sev}`} />

        <div className="pod-group-info">
          <span className="pod-group-name">{pod}</span>
          <span className="pod-group-ns">{namespace}</span>
        </div>

        <div className="pod-group-counts">
          {openCount > 0 && (
            <span className="pod-count-open">{openCount} aberto{openCount !== 1 ? 's' : ''}</span>
          )}
          <span className="pod-count-total">{incidents.length} erro{incidents.length !== 1 ? 's' : ''}</span>
        </div>

        <span className={`chevron${expanded ? ' open' : ''}`}>▼</span>
      </div>

      {expanded && (
        <div className="pod-group-body">
          {incidents.map(incident => (
            <IncidentCard
              key={incident.id}
              incident={incident}
              onStatusChange={onStatusChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}
