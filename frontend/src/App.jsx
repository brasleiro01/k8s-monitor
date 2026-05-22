import { useState, useEffect, useCallback } from 'react';
import { fetchIncidents, subscribeToEvents } from './api.js';
import StatsBar from './components/StatsBar.jsx';
import Filters from './components/Filters.jsx';
import NamespaceGroup from './components/NamespaceGroup.jsx';
import PostmortemsDrawer from './components/PostmortemsDrawer.jsx';
import LoginScreen from './components/LoginScreen.jsx';
import NamespaceChecker from './components/NamespaceChecker.jsx';
import ClusterOverview from './components/ClusterOverview.jsx';
import ScheduledReports from './components/ScheduledReports.jsx';

const DEFAULT_FILTERS = { severity: '', status: 'open', namespace: '', search: '', dateFrom: '', dateTo: '' };
const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

// ---- helpers de usuário/tema/timezone ---- //
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

function loadTimezone(name) {
  return localStorage.getItem(`k8s-monitor.tz.${name}`) || 'America/Sao_Paulo';
}

function saveTimezone(name, tz) {
  localStorage.setItem(`k8s-monitor.tz.${name}`, tz);
}

const TZ_OPTIONS = [
  { value: 'America/Sao_Paulo',    label: 'São Paulo (UTC-3)',          short: 'BRT'  },
  { value: 'America/Manaus',       label: 'Manaus (UTC-4)',             short: 'AMT'  },
  { value: 'America/Belem',        label: 'Belém / Fortaleza (UTC-3)',  short: 'BRT'  },
  { value: 'America/Noronha',      label: 'Noronha (UTC-2)',            short: 'FNT'  },
  { value: 'UTC',                  label: 'UTC (UTC+0)',                short: 'UTC'  },
  { value: 'America/New_York',     label: 'Nova York (UTC-5)',          short: 'EST'  },
  { value: 'Europe/Lisbon',        label: 'Lisboa (UTC+1)',             short: 'WET'  },
  { value: 'Europe/Madrid',        label: 'Madrid / Paris (UTC+2)',     short: 'CET'  },
];

function TopbarClock({ timezone }) {
  const [time, setTime] = useState('');
  useEffect(() => {
    const tick = () => setTime(
      new Date().toLocaleTimeString('pt-BR', {
        timeZone: timezone, hour: '2-digit', minute: '2-digit', second: '2-digit',
      })
    );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [timezone]);
  const tzLabel = TZ_OPTIONS.find(o => o.value === timezone)?.short || 'UTC';
  return (
    <div className="topbar-clock">
      <span className="topbar-clock-time">{time}</span>
      <span className="topbar-clock-tz">{tzLabel}</span>
    </div>
  );
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
function groupByNamespace(incidents) {
  const worstOpenSev = list => list
    .filter(i => !i.resolved)
    .reduce((w, i) => Math.min(w, SEV_ORDER[i.severity] ?? 4), 4);
  const latestTs = list => Math.max(...list.map(i => i.timestamp));

  // Agrupa por namespace → por pod
  const nsMap = new Map();
  for (const i of incidents) {
    if (!nsMap.has(i.namespace)) nsMap.set(i.namespace, new Map());
    const podMap = nsMap.get(i.namespace);
    if (!podMap.has(i.pod)) podMap.set(i.pod, []);
    podMap.get(i.pod).push(i);
  }

  return [...nsMap.entries()]
    .map(([namespace, podMap]) => {
      const pods = [...podMap.entries()]
        .map(([pod, podIncidents]) => ({ pod, incidents: podIncidents }))
        .sort((a, b) => {
          const sd = worstOpenSev(a.incidents) - worstOpenSev(b.incidents);
          return sd !== 0 ? sd : latestTs(b.incidents) - latestTs(a.incidents);
        });
      return { namespace, pods };
    })
    .sort((a, b) => {
      const allA = a.pods.flatMap(p => p.incidents);
      const allB = b.pods.flatMap(p => p.incidents);
      const sd = worstOpenSev(allA) - worstOpenSev(allB);
      return sd !== 0 ? sd : latestTs(allB) - latestTs(allA);
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

export default function App({ googleClientId = '', configuredNamespaces = [] }) {
  // ---- auth / tema ---- //
  const [user, setUser] = useState(loadUser);
  const [theme, setTheme] = useState(() => {
    const u = loadUser();
    return u ? loadTheme(u) : 'light';
  });
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [timezone, setTimezone] = useState(() => {
    const u = loadUser();
    return u ? loadTimezone(u) : 'America/Sao_Paulo';
  });

  useEffect(() => { applyTheme(theme); }, [theme]);

  function handleLogin(name) {
    saveUser(name);
    const t = loadTheme(name);
    setTheme(t);
    applyTheme(t);
    setTimezone(loadTimezone(name));
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

  function handleTimezone(tz) {
    setTimezone(tz);
    saveTimezone(user, tz);
  }

  // ---- incidents ---- //
  const [incidents, setIncidents] = useState([]);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const [drawerOpen, setDrawerOpen]     = useState(false);
  const [checkerOpen, setCheckerOpen]   = useState(false);
  const [reportsOpen, setReportsOpen]   = useState(false);
  const [newReportId, setNewReportId]   = useState(null);
  const [reportsBadge, setReportsBadge] = useState(0);
  const [tab, setTab] = useState('alerts');

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
        } else if (update.type === 'ai_analysis') {
          setIncidents(prev => prev.map(i => i.id === update.id
            ? {
                ...i,
                root_cause:       update.root_cause,
                summary:          update.summary,
                immediate_action: update.immediate_action,
                prevention:       update.prevention,
                estimated_impact: update.estimated_impact,
                severity:         update.severity,
                _ai_enriched:     true,
              }
            : i));
        }
      },
      report => {
        setNewReportId(report.id);
        setReportsBadge(n => n + 1);
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
  if (!user) return <LoginScreen onLogin={handleLogin} googleClientId={googleClientId} />;

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

  const groups = groupByNamespace(filtered);
  const hasCritical = incidents.some(i => i.severity === 'critical' && !i.resolved);
  const postmortemCount = incidents.filter(i => i.resolved && i.postmortem_file).length;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-brand">
          <K8sLogo size={28} />
          <span className="topbar-title">K8s Monitor</span>
        </div>

        {hasCritical && <div className="dot error" />}

        {!connected && (
          <div className="live-badge" title="Conectando...">
            <span className="off" />
            <span className="live-badge-text">Conectando...</span>
          </div>
        )}

        <TopbarClock timezone={timezone} />

        <div className="topbar-right">
          <button
            className={`btn-checker${checkerOpen ? ' active' : ''}`}
            onClick={() => setCheckerOpen(o => !o)}
            title="Verificação manual de namespace"
          >
            🔍 Verificar
          </button>

          <button
            className={`btn-reports${reportsOpen ? ' active' : ''}`}
            onClick={() => { setReportsOpen(o => !o); setReportsBadge(0); }}
            title="Relatórios agendados"
          >
            📊 Relatórios
            {reportsBadge > 0 && (
              <span className="pm-count-badge">{reportsBadge}</span>
            )}
          </button>

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
                <div className="user-menu-tz-block">
                  <div className="user-menu-tz-label">🌍 Fuso horário</div>
                  <select
                    className="user-menu-tz-select"
                    value={timezone}
                    onChange={e => handleTimezone(e.target.value)}
                  >
                    {TZ_OPTIONS.map(o => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                </div>
                <button className="user-menu-item" onClick={() => { setShowUserMenu(false); handleLogout(); }}>
                  Trocar usuário
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {checkerOpen && (
        <NamespaceChecker onClose={() => setCheckerOpen(false)} />
      )}

      {drawerOpen && (
        <PostmortemsDrawer
          incidents={incidents}
          onClose={() => setDrawerOpen(false)}
        />
      )}

      {reportsOpen && (
        <ScheduledReports
          onClose={() => setReportsOpen(false)}
          newReportId={newReportId}
          timezone={timezone}
        />
      )}

      <nav className="tabs-bar">
        <button
          className={`tab-btn${tab === 'overview' ? ' active' : ''}`}
          onClick={() => setTab('overview')}
        >
          🖥 Visão Geral
        </button>
        <button
          className={`tab-btn${tab === 'alerts' ? ' active' : ''}`}
          onClick={() => setTab('alerts')}
        >
          🚨 Alertas
          {incidents.filter(i => !i.resolved).length > 0 && (
            <span className="tab-badge">{incidents.filter(i => !i.resolved).length}</span>
          )}
        </button>
      </nav>

      <main>
        {tab === 'overview' && <ClusterOverview />}

        {tab === 'alerts' && (
          <>
            {error && <div className="api-error">{error}</div>}
            <StatsBar incidents={incidents} />
            <Filters incidents={incidents} filters={filters} onChange={setFilters} configuredNamespaces={configuredNamespaces} />
            <div className="incident-list">
              {groups.length === 0 ? (
                <div className="empty">
                  {incidents.length === 0
                    ? 'Nenhum incidente ainda — o monitor está observando seus pods.'
                    : 'Nenhum incidente corresponde aos filtros selecionados.'}
                </div>
              ) : (
                groups.map(g => (
                  <NamespaceGroup
                    key={g.namespace}
                    namespace={g.namespace}
                    pods={g.pods}
                    onStatusChange={handleStatusChange}
                  />
                ))
              )}
            </div>
          </>
        )}
      </main>

      {showUserMenu && (
        <div className="user-menu-overlay" onClick={() => setShowUserMenu(false)} />
      )}
    </div>
  );
}
