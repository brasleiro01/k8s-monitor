import { useState } from 'react';
import IncidentCard from './IncidentCard.jsx';

const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

function worstSeverity(incidents) {
  return incidents.reduce((worst, i) => {
    return (SEV_ORDER[i.severity] ?? 4) < (SEV_ORDER[worst] ?? 4) ? i.severity : worst;
  }, 'low');
}

function deduplicateByError(incidents) {
  const map = new Map();
  for (const i of incidents) {
    const existing = map.get(i.id);
    if (!existing) {
      map.set(i.id, { ...i, _occurrences: 1 });
    } else {
      existing._occurrences += 1;
      if (i.timestamp > existing.timestamp) {
        map.set(i.id, { ...i, _occurrences: existing._occurrences });
      }
    }
  }
  return [...map.values()].sort((a, b) => b.timestamp - a.timestamp);
}

export default function PodGroup({ pod, namespace, incidents, onStatusChange }) {
  const [expanded, setExpanded] = useState(true);

  const deduped = deduplicateByError(incidents);
  const openCount = deduped.filter(i => !i.resolved).length;
  const totalOccurrences = incidents.length;
  const sev = worstSeverity(
    deduped.filter(i => !i.resolved).length > 0
      ? deduped.filter(i => !i.resolved)
      : deduped
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
          <span className="pod-count-total">
            {deduped.length} tipo{deduped.length !== 1 ? 's' : ''}
            {totalOccurrences > deduped.length && (
              <span className="pod-count-occurrences"> · {totalOccurrences} ocorrências</span>
            )}
          </span>
        </div>

        <span className={`chevron${expanded ? ' open' : ''}`}>▼</span>
      </div>

      {expanded && (
        <div className="pod-group-body">
          {deduped.map(incident => (
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
