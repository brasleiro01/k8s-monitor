import { useState, useEffect, useCallback } from 'react';
import { fetchIncidents, subscribeToEvents } from './api.js';
import StatsBar from './components/StatsBar.jsx';
import Filters from './components/Filters.jsx';
import PodGroup from './components/PodGroup.jsx';
import PostmortemsDrawer from './components/PostmortemsDrawer.jsx';
import LoginScreen from './components/LoginScreen.jsx';

const DEFAULT_FILTERS = { severity: '', status: 'open', namespace: '', search: '', dateFrom: '', dateTo: '' };
const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

// ---- helpers de usuário/tema ---- //
function loadUser() {
  return localStorage.getItem('k8s-monitor.current') || null;
}

function saveUser(name) {
  const users = JSON.parse(localStorage.getItem('k8s-monitor.users') || '[]');
  if (!users.includes(name)) {
    users.push(name);
    localStorage.setItem('k8s-monitor.users', JSON.stringify(users));
  }
  localStorage.setItem('k8s-monitor.current', name);
}

function loadTheme(name) {
  return localStorage.getItem(`k8s-monitor.theme.${name}`) || 'light';
}

function saveTheme(name, theme) {
  localStorage.setItem(`k8s-monitor.theme.${name}`, theme);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
}

function initials(name) {
  return name.trim().split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
}

function avatarColor(name) {
  const colors = ['#cf3081', '#805ad5', '#2b6cb0', '#2f855a', '#dd6b20', '#b7791f'];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffff;
  return colors[h % colors.length];
}

// ---- agrupamento ---- //
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

// ---- componente de logo ---- //
function K8sLogo({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" style={{ flexShrink: 0 }}>
      <circle cx="20" cy="20" r="17.5" stroke="currentColor" strokeWidth="2.5" />
      <text
        x="20" y="26"
        textAnchor="middle"
        fontSize="13"
        fontWeight="800"
        fill="currentColor"
        fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
        letterSpacing="-0.5"
      >k8</text>
    </svg>
  );
}

export default function App() {
  // ---- auth / tema ---- //
  const [user, setUser] = useState(loadUser);
  const [theme, setTheme] = useState(() => {
    const u = loadUser();
    return u ? loadTheme(u) : 'light';
  });
  const [showUserMenu, setShowUserMenu] = useState(false);

  useEffect(() => { applyTheme(theme); }, [theme]);

  function handleLogin(name) {
    saveUser(name);
    const t = loadTheme(name);
    setTheme(t);
    applyTheme(t);
    setUser(name);
  }

  function handleLogout() {
    localStorage.removeItem('k8s-monitor.current');
    setUser(null);
    setShowUserMenu(false);
  }

  function toggleTheme() {
    const next = theme === 'light' ? 'dark' : 'light';
    setTheme(next);
    saveTheme(user, next);
    applyTheme(next);
  }

  // ---- incidents ---- //
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
    } catch {
      setError('Não foi possível conectar à API — o servidor está rodando?');
    }
  }, []);

  useEffect(() => {
    if (!user) return;
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
  }, [load, user]);

  function handleStatusChange(id, resolved, postmortem_file, extra = {}) {
    setIncidents(prev => prev.map(i =>
      i.id === id ? { ...i, resolved, postmortem_file: postmortem_file ?? i.postmortem_file, ...extra } : i
    ));
  }

  // ---- render: login ---- //
  if (!user) return <LoginScreen onLogin={handleLogin} />;

  // ---- filtros ---- //
  const filtered = incidents.filter(i => {
    if (filters.severity && i.severity !== filters.severity) return false;
    if (filters.status === 'open' && i.resolved) return false;
    if (filters.status === 'resolved' && !i.resolved) return false;
    if (filters.namespace && i.namespace !== filters.namespace) return false;
    if (filters.search) {
      const q = filters.search.toLowerCase();
      if (!i.pod.toLowerCase().includes(q) && !i.error_line.toLowerCase().includes(q)) return false;
    }
    if (filters.dateFrom) {
      const from = new Date(filters.dateFrom).getTime() / 1000;
      if (i.timestamp < from) return false;
    }
    if (filters.dateTo) {
      const to = new Date(filters.dateTo).getTime() / 1000 + 86399;
      if (i.timestamp > to) return false;
    }
    return true;
  });

  const groups = groupByPod(filtered);
  const hasCritical = incidents.some(i => i.severity === 'critical' && !i.resolved);
  const postmortemCount = incidents.filter(i => i.resolved && i.postmortem_file).length;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-brand">
          <K8sLogo size={28} />
          <span className="topbar-title">K8s Monitor</span>
        </div>

        <div className={`dot${hasCritical ? ' error' : ''}`} />

        <div className="live-badge">
          <span className={connected ? '' : 'off'} />
          {connected ? 'Ao vivo' : 'Conectando...'}
        </div>

        <div className="topbar-right">
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

          <button
            className="btn-theme"
            onClick={toggleTheme}
            title={theme === 'light' ? 'Mudar para tema escuro' : 'Mudar para tema claro'}
          >
            {theme === 'light' ? '🌙' : '☀️'}
          </button>

          <div className="user-menu-wrap">
            <button
              className="user-avatar-btn"
              onClick={() => setShowUserMenu(o => !o)}
              style={{ background: avatarColor(user) }}
              title={user}
            >
              {initials(user)}
            </button>
            {showUserMenu && (
              <div className="user-menu">
                <div className="user-menu-name">{user}</div>
                <button className="user-menu-item" onClick={() => { setShowUserMenu(false); handleLogout(); }}>
                  Trocar usuário
                </button>
              </div>
            )}
          </div>
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
          <div className="api-error">{error}</div>
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

      {showUserMenu && (
        <div className="user-menu-overlay" onClick={() => setShowUserMenu(false)} />
      )}
    </div>
  );
}
