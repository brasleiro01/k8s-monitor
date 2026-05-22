import { useState, useEffect, useCallback } from 'react';
import { fetchClusterOverview, recommendPodConfig } from '../api.js';

function fmtUptime(isoStr) {
  if (!isoStr) return null;
  const secs = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hrs < 24) return remMins > 0 ? `${hrs}h ${remMins}m` : `${hrs}h`;
  const days = Math.floor(hrs / 24);
  const remHrs = hrs % 24;
  return remHrs > 0 ? `${days}d ${remHrs}h` : `${days}d`;
}

function barColor(pct) {
  if (pct > 80) return 'var(--critical)';
  if (pct > 60) return 'var(--medium)';
  return 'var(--low)';
}

function MiniBar({ pct }) {
  if (pct == null) return <span className="ov-bar-na">—</span>;
  const p = Math.min(pct, 100);
  return (
    <div className="ov-bar">
      <div className="ov-bar-fill" style={{ width: `${p}%`, background: barColor(pct) }} />
      <span className="ov-bar-pct" style={{ color: barColor(pct) }}>
        {pct < 1 ? '<1' : pct}%
      </span>
    </div>
  );
}

function ProbeTag({ label, value }) {
  return (
    <span className={`ov-probe-tag ${value ? 'ok' : 'miss'}`} title={value || 'ausente'}>
      <span className="ov-probe-dot-sm" />
      {label}
    </span>
  );
}

// ---- YAML builder ----
function buildYAML(rec) {
  const ind = (n, s) => '  '.repeat(n) + s;
  const lines = [];

  const probeLines = (probe, depth) => {
    const L = [];
    if (probe.type === 'httpGet') {
      L.push(ind(depth, 'httpGet:'));
      L.push(ind(depth + 1, `path: ${probe.path}`));
      L.push(ind(depth + 1, `port: ${probe.port}`));
    } else if (probe.type === 'tcpSocket') {
      L.push(ind(depth, 'tcpSocket:'));
      L.push(ind(depth + 1, `port: ${probe.port}`));
    } else if (probe.type === 'exec' && probe.command) {
      L.push(ind(depth, 'exec:'));
      L.push(ind(depth + 1, `command: ${JSON.stringify(probe.command)}`));
    }
    L.push(ind(depth, `initialDelaySeconds: ${probe.initial_delay_seconds}`));
    L.push(ind(depth, `periodSeconds: ${probe.period_seconds}`));
    L.push(ind(depth, `failureThreshold: ${probe.failure_threshold}`));
    L.push(ind(depth, `timeoutSeconds: ${probe.timeout_seconds}`));
    return L;
  };

  if (rec.liveness_probe) {
    lines.push('livenessProbe:');
    lines.push(...probeLines(rec.liveness_probe, 1));
  }
  if (rec.readiness_probe) {
    if (lines.length) lines.push('');
    lines.push('readinessProbe:');
    lines.push(...probeLines(rec.readiness_probe, 1));
  }
  if (rec.resources) {
    if (lines.length) lines.push('');
    const r = rec.resources;
    lines.push('resources:');
    lines.push(ind(1, 'requests:'));
    lines.push(ind(2, `cpu: "${r.requests.cpu}"`));
    lines.push(ind(2, `memory: "${r.requests.memory}"`));
    lines.push(ind(1, 'limits:'));
    lines.push(ind(2, `cpu: "${r.limits.cpu}"`));
    lines.push(ind(2, `memory: "${r.limits.memory}"`));
  }
  return lines.join('\n');
}

// ---- Recommendation panel ----
function RecommendPanel({ rec }) {
  const [copied, setCopied] = useState(false);
  const yaml = buildYAML(rec);

  function copy() {
    navigator.clipboard.writeText(yaml).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const reasons = [
    rec.liveness_probe?.reasoning  && { label: 'Liveness',  text: rec.liveness_probe.reasoning },
    rec.readiness_probe?.reasoning && { label: 'Readiness', text: rec.readiness_probe.reasoning },
    rec.resources?.reasoning       && { label: 'Resources', text: rec.resources.reasoning },
  ].filter(Boolean);

  return (
    <div className="ov-rec-panel">
      <div className="ov-rec-panel-title">
        🤖 Sugestão de configuração
        <span className="ov-rec-source">{rec._source === 'gemini' ? 'Gemini AI' : 'Padrão'}</span>
      </div>
      {reasons.map(r => (
        <div key={r.label} className="ov-rec-reason">
          <strong>{r.label}:</strong> {r.text}
        </div>
      ))}
      <div className="ov-rec-yaml-bar">
        <span className="ov-rec-yaml-label">Adicione ao seu manifest:</span>
        <button className="ov-rec-copy" onClick={copy}>
          {copied ? '✓ Copiado' : '📋 Copiar'}
        </button>
      </div>
      <pre className="ov-rec-yaml">{yaml}</pre>
    </div>
  );
}

// ---- Container row ----
function ContainerRow({ c, metricsAvailable, ns, pod }) {
  const [rec, setRec]         = useState(null);
  const [recLoading, setRec2] = useState(false);
  const [recErr, setRecErr]   = useState('');

  const missingProbes = !c.liveness || !c.readiness;
  const missingLimits = c.cpu_lim === 'N/A' || c.mem_lim === 'N/A';
  const needsRec = missingProbes || missingLimits;

  async function loadRec() {
    setRec2(true);
    setRecErr('');
    try {
      const r = await recommendPodConfig({
        namespace: ns, pod, container: c.name, image: c.image || '',
        ports: c.ports || [],
        cpu_use: c.cpu_use || 'N/A', mem_use: c.mem_use || 'N/A',
        cpu_use_m: c.cpu_use_m ?? null, mem_use_bytes: c.mem_use_bytes ?? null,
        missing_probes: missingProbes, missing_limits: missingLimits,
        has_liveness: !!c.liveness, has_readiness: !!c.readiness,
      });
      setRec(r);
    } catch {
      setRecErr('Falha ao obter sugestão.');
    }
    setRec2(false);
  }

  return (
    <div className="ov-container">
      <div className="ov-container-head">
        <span className={`ov-cstate-dot ${c.ready ? 'ok' : 'bad'}`} />
        <span className="ov-container-name">{c.name}</span>
        {!c.ready && <span className="ov-notready">{c.state || 'not ready'}</span>}
        <span className="ov-container-gap" />
        {needsRec && !rec && (
          <button className="ov-rec-btn" onClick={loadRec} disabled={recLoading}>
            {recLoading ? <span className="nsc-spinner-sm" /> : '🤖'}
            {recLoading ? ' Analisando...' : ' Sugestão IA'}
          </button>
        )}
        {rec && <span className="ov-rec-done">✓ Sugestão pronta</span>}
      </div>

      <div className="ov-container-res">
        <div className="ov-res-row">
          <span className="ov-rlabel">CPU</span>
          {c.cpu_lim === 'N/A'
            ? <span className="ov-nolimit">sem limit ⚠</span>
            : metricsAvailable
              ? <>
                  <span className="ov-ruse">{c.cpu_use !== 'N/A' ? c.cpu_use : '—'}</span>
                  <span className="ov-rsep">/</span>
                  <span className="ov-rlim">{c.cpu_lim}</span>
                  <MiniBar pct={c.cpu_pct ?? 0} />
                </>
              : <span className="ov-rlim">{c.cpu_lim}</span>
          }
        </div>
        <div className="ov-res-row">
          <span className="ov-rlabel">MEM</span>
          {c.mem_lim === 'N/A'
            ? <span className="ov-nolimit">sem limit ⚠</span>
            : metricsAvailable
              ? <>
                  <span className="ov-ruse">{c.mem_use !== 'N/A' ? c.mem_use : '—'}</span>
                  <span className="ov-rsep">/</span>
                  <span className="ov-rlim">{c.mem_lim}</span>
                  <MiniBar pct={c.mem_pct ?? 0} />
                </>
              : <span className="ov-rlim">{c.mem_lim}</span>
          }
        </div>
        <div className="ov-res-row ov-res-probes">
          <span className="ov-rlabel">Probes</span>
          <ProbeTag label="Live"  value={c.liveness} />
          <ProbeTag label="Ready" value={c.readiness} />
        </div>
      </div>

      {recErr && <div className="ov-rec-err">{recErr}</div>}
      {rec && <RecommendPanel rec={rec} />}
    </div>
  );
}

// ---- Pod row ----
function PodRow({ pod, metricsAvailable, ns }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`ov-pod ov-pod-${pod.health}`}>
      <button className="ov-pod-row" onClick={() => setOpen(o => !o)}>
        <span className={`ov-health-dot ov-health-${pod.health}`} />
        <span className="ov-pod-name">{pod.pod}</span>
        <span className="ov-pod-phase">{pod.phase}</span>
        <span className="ov-pod-ready">{pod.ready_count}/{pod.total_containers} ready</span>
        {pod.restarts > 0 && (
          <span className={`ov-restarts ${pod.restarts > 5 ? 'high' : pod.restarts > 2 ? 'med' : ''}`}>
            ↺ {pod.restarts}
          </span>
        )}
        {fmtUptime(pod.start_time) && (
          <span className="ov-uptime" title="Tempo em execução">⏱ {fmtUptime(pod.start_time)}</span>
        )}
        <span className="ov-expand">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="ov-pod-detail">
          {pod.containers?.map((c, i) => (
            <ContainerRow key={i} c={c} metricsAvailable={metricsAvailable}
                          ns={ns} pod={pod.pod} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Namespace card ----
function NamespaceCard({ ns, metricsAvailable }) {
  const [open, setOpen] = useState(ns.health !== 'healthy');
  return (
    <div className={`ov-ns ov-ns-${ns.health}`}>
      <button className="ov-ns-header" onClick={() => setOpen(o => !o)}>
        <span className={`ov-health-dot ov-health-${ns.health}`} />
        <span className="ov-ns-name">{ns.namespace}</span>
        <span className="ov-ns-counts">
          {ns.pods.filter(p => p.health === 'critical').length > 0 && (
            <span className="ov-badge-c">{ns.pods.filter(p => p.health === 'critical').length} crítico</span>
          )}
          {ns.pods.filter(p => p.health === 'warning').length > 0 && (
            <span className="ov-badge-w">{ns.pods.filter(p => p.health === 'warning').length} aviso</span>
          )}
          <span className="ov-badge-total">{ns.pods.length} pod{ns.pods.length !== 1 ? 's' : ''}</span>
        </span>
        <span className="ov-expand">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="ov-ns-body">
          {ns.pods.map((pod, i) => (
            <PodRow key={i} pod={pod} metricsAvailable={metricsAvailable} ns={ns.namespace} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Root ----
export default function ClusterOverview() {
  const [data, setData]             = useState(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [nsFilter, setNsFilter]     = useState('');

  const load = useCallback(async () => {
    try {
      const d = await fetchClusterOverview();
      setData(d);
      setLastUpdated(new Date());
      setError('');
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) return <div className="ov-loading"><span className="nsc-spinner" /> Carregando...</div>;
  if (error)   return <div className="ov-error"><strong>Erro:</strong> {error} <button className="ov-retry" onClick={load}>Tentar novamente</button></div>;
  if (!data)   return null;

  const allNs   = data.namespaces.map(n => n.namespace);
  const filtered = nsFilter ? data.namespaces.filter(n => n.namespace === nsFilter) : data.namespaces;

  return (
    <div className="ov-root">
      <div className="ov-stats">
        <div className="ov-stat">
          <span className="ov-stat-val">{data.total_pods}</span>
          <span className="ov-stat-label">Total de pods</span>
        </div>
        <div className="ov-stat ov-stat-healthy">
          <span className="ov-stat-val">{data.healthy}</span>
          <span className="ov-stat-label">✅ Saudáveis</span>
        </div>
        <div className="ov-stat ov-stat-warning">
          <span className="ov-stat-val">{data.warning}</span>
          <span className="ov-stat-label">⚠️ Atenção</span>
        </div>
        <div className="ov-stat ov-stat-critical">
          <span className="ov-stat-val">{data.critical}</span>
          <span className="ov-stat-label">🔴 Críticos</span>
        </div>
        <div className="ov-stat ov-stat-meta">
          {!data.metrics_available && <span className="ov-metrics-warn">Métricas indisponíveis</span>}
          {lastUpdated && (
            <span className="ov-updated">
              Atualizado às {lastUpdated.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      <div className="ov-filter-bar">
        <label className="ov-filter-label">Namespace:</label>
        <select className="ov-filter-select" value={nsFilter} onChange={e => setNsFilter(e.target.value)}>
          <option value="">Todos ({allNs.length})</option>
          {allNs.map(ns => <option key={ns} value={ns}>{ns}</option>)}
        </select>
        {nsFilter && <button className="ov-filter-clear" onClick={() => setNsFilter('')}>✕ Limpar</button>}
      </div>

      <div className="ov-namespaces">
        {filtered.length === 0
          ? <div className="ov-empty">Nenhum namespace encontrado.</div>
          : filtered.map((ns, i) => (
              <NamespaceCard key={i} ns={ns} metricsAvailable={data.metrics_available} />
            ))
        }
      </div>
    </div>
  );
}
