import { useState, useEffect, useCallback } from 'react';
import { fetchIncidents, subscribeToEvents } from './api.js';
import StatsBar from './components/StatsBar.jsx';
import Filters from './components/Filters.jsx';
import PodGroup from './components/PodGroup.jsx';
import PostmortemsDrawer from './components/PostmortemsDrawer.jsx';

const DEFAULT_FILTERS = { severity: '', status: 'open', namespace: '', search: '' };
const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

function groupByPod(incidents) {
  const map = new Map();
  for (const i of incidents) {
    const key = `${i.namespace}/${i.pod}`;
    if (!map.has(key)) map.set(key, { pod: i.pod, namespace: i.namespace, incidents: [] });
    map.get(key).incidents.push(i);
  }
  return [...map.values()].sort((a, b) => {
    const worstOpen = g => g.incidents
      .filter(i => !i.resolved)
      .reduce((w, i) => Math.min(w, SEV_ORDER[i.severity] ?? 4), 4);
    const sevDiff = worstOpen(a) - worstOpen(b);
    if (sevDiff !== 0) return sevDiff;
    const latestTs = g => Math.max(...g.incidents.map(i => i.timestamp));
    return latestTs(b) - latestTs(a);
  });
}

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchIncidents();
      setIncidents(data);
      setError(null);
    } catch (e) {
      setError('Não foi possível conectar à API — o servidor está rodando?');
    }
  }, []);

  useEffect(() => {
    load();

    const unsub = subscribeToEvents(
      newIncident => {
        setConnected(true);
        setIncidents(prev => {
          if (prev.some(i => i.id === newIncident.id)) return prev;
          return [{ ...newIncident, resolved: false }, ...prev];
        });
      },
      update => {
        if (update.type === 'resolved') {
          setIncidents(prev => prev.map(i => i.id === update.id
            ? { ...i, resolved: true, postmortem_file: update.postmortem_file, resolution_description: update.resolution_description, resolution_time: update.resolution_time }
            : i));
        } else if (update.type === 'reopened') {
          setIncidents(prev => prev.map(i => i.id === update.id
            ? { ...i, resolved: false, postmortem_file: null }
            : i));
        }
      }
    );

    const heartbeat = setInterval(load, 30000);
    setConnected(true);

    return () => { unsub(); clearInterval(heartbeat); };
  }, [load]);

  function handleStatusChange(id, resolved, postmortem_file, extra = {}) {
    setIncidents(prev => prev.map(i =>
      i.id === id ? { ...i, resolved, postmortem_file: postmortem_file ?? i.postmortem_file, ...extra } : i
    ));
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

  const groups = groupByPod(filtered);
  const hasCritical = incidents.some(i => i.severity === 'critical' && !i.resolved);
  const postmortemCount = incidents.filter(i => i.resolved && i.postmortem_file).length;

  return (
    <div className="app">
      <header className="topbar">
        <div className={`dot${hasCritical ? ' error' : ''}`} />
        <h1>MT-K8s-Monitor</h1>

        <button
          className={`btn-postmortems${drawerOpen ? ' active' : ''}`}
          onClick={() => setDrawerOpen(o => !o)}
          title="Histórico de resoluções"
        >
          📋 Resoluções
          {postmortemCount > 0 && (
            <span className="pm-count-badge">{postmortemCount}</span>
          )}
        </button>

        <div className="live-badge">
          <span className={connected ? '' : 'off'} />
          {connected ? 'Ao vivo' : 'Conectando...'}
        </div>
      </header>

      {drawerOpen && (
        <PostmortemsDrawer
          incidents={incidents}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      <main>
        {error && (
          <div style={{ color: 'var(--critical)', marginBottom: 16, padding: '12px 16px', background: 'rgba(248,81,73,0.1)', borderRadius: 8, border: '1px solid rgba(248,81,73,0.3)' }}>
            {error}
          </div>
        )}

        <StatsBar incidents={incidents} />
        <Filters incidents={incidents} filters={filters} onChange={setFilters} />

        <div className="incident-list">
          {groups.length === 0 ? (
            <div className="empty">
              {incidents.length === 0
                ? 'Nenhum incidente ainda — o monitor está observando seus pods.'
                : 'Nenhum incidente corresponde aos filtros selecionados.'}
            </div>
          ) : (
            groups.map(g => (
              <PodGroup
                key={`${g.namespace}/${g.pod}`}
                pod={g.pod}
                namespace={g.namespace}
                incidents={g.incidents}
                onStatusChange={handleStatusChange}
              />
            ))
          )}
        </div>
      </main>
    </div>
  );
}
