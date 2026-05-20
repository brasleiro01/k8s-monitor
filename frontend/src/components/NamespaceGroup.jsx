import { useState } from 'react';
import PodGroup from './PodGroup.jsx';

const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

function worstSev(incidents) {
  return incidents.reduce((worst, i) => {
    return (SEV_ORDER[i.severity] ?? 4) < (SEV_ORDER[worst] ?? 4) ? i.severity : worst;
  }, 'low');
}

export default function NamespaceGroup({ namespace, pods, onStatusChange }) {
  const [expanded, setExpanded] = useState(true);

  const allIncidents = pods.flatMap(p => p.incidents);
  const openIncidents = allIncidents.filter(i => !i.resolved);
  const openCount = openIncidents.length;
  const podsWithOpen = pods.filter(p => p.incidents.some(i => !i.resolved)).length;
  const sev = worstSev(openCount > 0 ? openIncidents : allIncidents);

  return (
    <div className="ns-group">
      <div className="ns-group-header" onClick={() => setExpanded(e => !e)}>
        <div className={`severity-bar ${sev}`} />

        <span className="ns-group-icon">⬡</span>
        <span className="ns-group-name">{namespace}</span>

        <div className="ns-group-counts">
          {openCount > 0 && (
            <span className="pod-count-open">
              {openCount} aberto{openCount !== 1 ? 's' : ''}
            </span>
          )}
          <span className="pod-count-total">
            {pods.length} pod{pods.length !== 1 ? 's' : ''}
            {podsWithOpen > 0 && openCount === 0 && (
              <span className="ns-resolved-note"> · todos resolvidos</span>
            )}
          </span>
        </div>

        <span className={`chevron${expanded ? ' open' : ''}`}>▼</span>
      </div>

      {expanded && (
        <div className="ns-group-body">
          {pods.map(p => (
            <PodGroup
              key={p.pod}
              pod={p.pod}
              namespace={namespace}
              incidents={p.incidents}
              onStatusChange={onStatusChange}
              nested
            />
          ))}
        </div>
      )}
    </div>
  );
}
