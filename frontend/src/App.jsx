import { useState, useEffect, useCallback } from 'react';
import { fetchIncidents, subscribeToEvents } from './api.js';
import StatsBar from './components/StatsBar.jsx';
import Filters from './components/Filters.jsx';
import IncidentCard from './components/IncidentCard.jsx';

const DEFAULT_FILTERS = { severity: '', status: 'open', namespace: '', search: '' };

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchIncidents();
      setIncidents(data);
      setError(null);
    } catch (e) {
      setError('Cannot reach API — is the server running?');
    }
  }, []);

  useEffect(() => {
    load();

    const unsub = subscribeToEvents(
      newIncident => {
        setConnected(true);
        setIncidents(prev => [{ ...newIncident, resolved: false }, ...prev]);
      },
      update => {
        if (update.type === 'resolved') {
          setIncidents(prev => prev.map(i => i.id === update.id
            ? { ...i, resolved: true, postmortem_file: update.postmortem_file }
            : i));
        } else if (update.type === 'reopened') {
          setIncidents(prev => prev.map(i => i.id === update.id ? { ...i, resolved: false, postmortem_file: null } : i));
        }
      }
    );

    const heartbeat = setInterval(load, 30000);
    setConnected(true);

    return () => {
      unsub();
      clearInterval(heartbeat);
    };
  }, [load]);

  function handleStatusChange(id, resolved, postmortem_file) {
    setIncidents(prev => prev.map(i => i.id === id ? { ...i, resolved, postmortem_file: postmortem_file ?? i.postmortem_file } : i));
  }

  const filtered = incidents.filter(i => {
    if (filters.severity && i.severity !== filters.severity) return false;
    if (filters.status === 'open' && i.resolved) return false;
    if (filters.status === 'resolved' && !i.resolved) return false;
    if (filters.namespace && i.namespace !== filters.namespace) return false;
    if (filters.search) {
      const q = filters.search.toLowerCase();
      if (!i.pod.toLowerCase().includes(q) && !i.error_line.toLowerCase().includes(q)) return false;
    }
    return true;
  });

  const hasCritical = incidents.some(i => i.severity === 'critical' && !i.resolved);

  return (
    <div className="app">
      <header className="topbar">
        <div className={`dot${hasCritical ? ' error' : ''}`} />
        <h1>K8s Monitor</h1>
        <div className="live-badge">
          <span className={connected ? '' : 'off'} />
          {connected ? 'Live' : 'Connecting...'}
        </div>
      </header>

      <main>
        {error && (
          <div style={{ color: 'var(--critical)', marginBottom: 16, padding: '12px 16px', background: 'rgba(248,81,73,0.1)', borderRadius: 8, border: '1px solid rgba(248,81,73,0.3)' }}>
            {error}
          </div>
        )}


        <StatsBar incidents={incidents} />

        <Filters incidents={incidents} filters={filters} onChange={setFilters} />

        <div className="incident-list">
          {filtered.length === 0 ? (
            <div className="empty">
              {incidents.length === 0
                ? 'Nenhum incidente ainda — o monitor está observando seus pods.'
                : 'Nenhum incidente corresponde aos filtros selecionados.'}
            </div>
          ) : (
            filtered.map(incident => (
              <IncidentCard
                key={incident.id}
                incident={incident}
                onStatusChange={handleStatusChange}
              />
            ))
          )}
        </div>
      </main>
    </div>
  );
}
