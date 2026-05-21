import { useState, useEffect, useCallback } from 'react';
import { fetchClusterOverview } from '../api.js';

const H_LABEL = { healthy: 'Saudável', warning: 'Atenção', critical: 'Crítico' };
const H_ICON  = { healthy: '✅', warning: '⚠️', critical: '🔴' };

function barColor(pct) {
  if (pct > 80) return 'var(--critical)';
  if (pct > 60) return 'var(--medium)';
  return 'var(--low)';
}

function MiniBar({ pct }) {
  if (pct == null) return <span className="ov-bar-na">—</span>;
  return (
    <div className="ov-bar">
      <div className="ov-bar-fill" style={{ width: `${Math.min(pct, 100)}%`, background: barColor(pct) }} />
      <span className="ov-bar-pct" style={{ color: barColor(pct) }}>{pct}%</span>
    </div>
  );
}

function ProbeDot({ value }) {
  return <span className={`ov-probe-dot ${value ? 'ok' : 'miss'}`} title={value || 'ausente'} />;
}

function ContainerDetail({ c, metricsAvailable }) {
  return (
    <div className="ov-cdetail">
      <div className="ov-cdetail-name">
        <span className={`ov-cstate-dot ${c.ready ? 'ok' : 'bad'}`} />
        {c.name}
        {!c.ready && <span className="ov-notready">not ready</span>}
      </div>
      <div className="ov-cdetail-res">
        {metricsAvailable ? (
          <>
            <span className="ov-rlabel">CPU</span>
            {c.cpu_lim === 'N/A'
              ? <span className="ov-nolimit">sem limit</span>
              : <><span className="ov-rval">{c.cpu_use !== 'N/A' ? `${c.cpu_use} / ${c.cpu_lim}` : c.cpu_lim}</span><MiniBar pct={c.cpu_pct} /></>
            }
            <span className="ov-rlabel">Mem</span>
            {c.mem_lim === 'N/A'
              ? <span className="ov-nolimit">sem limit</span>
              : <><span className="ov-rval">{c.mem_use !== 'N/A' ? `${c.mem_use} / ${c.mem_lim}` : c.mem_lim}</span><MiniBar pct={c.mem_pct} /></>
            }
          </>
        ) : (
          <>
            <span className="ov-rlabel">CPU limit</span>
            <span className="ov-rval">{c.cpu_lim === 'N/A' ? <em className="ov-nolimit">sem limit</em> : c.cpu_lim}</span>
            <span className="ov-rlabel">Mem limit</span>
            <span className="ov-rval">{c.mem_lim === 'N/A' ? <em className="ov-nolimit">sem limit</em> : c.mem_lim}</span>
          </>
        )}
        <span className="ov-rlabel">Probes</span>
        <span className="ov-rval ov-probes-row">
          <ProbeDot value={c.liveness} /> Live
          <ProbeDot value={c.readiness} /> Ready
        </span>
      </div>
    </div>
  );
}

function PodRow({ pod, metricsAvailable }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`ov-pod ov-pod-${pod.health}`}>
      <button className="ov-pod-row" onClick={() => setOpen(o => !o)}>
        <span className={`ov-health-dot ov-health-${pod.health}`} />
        <span className="ov-pod-name">{pod.pod}</span>
        <span className="ov-pod-phase">{pod.phase}</span>
        <span className="ov-pod-ready">
          {pod.ready_count}/{pod.total_containers} ready
        </span>
        {pod.restarts > 0 && (
          <span className={`ov-restarts ${pod.restarts > 5 ? 'high' : pod.restarts > 2 ? 'med' : ''}`}>
            ↺ {pod.restarts}
          </span>
        )}
        <div className="ov-pod-bars">
          <MiniBar pct={pod.cpu_pct} />
          <MiniBar pct={pod.mem_pct} />
        </div>
        <span className="ov-expand">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="ov-pod-detail">
          {pod.containers?.map((c, i) => (
            <ContainerDetail key={i} c={c} metricsAvailable={metricsAvailable} />
          ))}
        </div>
      )}
    </div>
  );
}

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
            <PodRow key={i} pod={pod} metricsAvailable={metricsAvailable} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function ClusterOverview() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);

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
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  if (loading) {
    return (
      <div className="ov-loading">
        <span className="nsc-spinner" /> Carregando visão geral do cluster...
      </div>
    );
  }

  if (error) {
    return (
      <div className="ov-error">
        <strong>Erro ao carregar visão geral:</strong> {error}
        <button className="ov-retry" onClick={load}>Tentar novamente</button>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="ov-root">
      {/* stats bar */}
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
          {!data.metrics_available && (
            <span className="ov-metrics-warn">Métricas indisponíveis</span>
          )}
          {lastUpdated && (
            <span className="ov-updated">
              Atualizado às {lastUpdated.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      {/* namespaces */}
      <div className="ov-namespaces">
        {data.namespaces.length === 0 ? (
          <div className="ov-empty">Nenhum pod encontrado no cluster.</div>
        ) : (
          data.namespaces.map((ns, i) => (
            <NamespaceCard key={i} ns={ns} metricsAvailable={data.metrics_available} />
          ))
        )}
      </div>
    </div>
  );
}
