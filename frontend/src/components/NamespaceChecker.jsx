import { useState, useEffect, useRef } from 'react';
import { fetchNamespaces, streamNamespaceCheck } from '../api.js';

const H_LABEL = { healthy: 'Saudável', warning: 'Atenção', critical: 'Crítico' };
const H_ICON  = { healthy: '✅', warning: '⚠️', critical: '🔴' };

// ---- helpers ---- //
function barColor(pct) {
  if (pct > 80) return 'var(--critical)';
  if (pct > 60) return 'var(--medium)';
  return 'var(--low)';
}

function ResourceBar({ label, use, lim, pct }) {
  if (!lim || lim === 'N/A') {
    return (
      <div className="nsc-res-row">
        <span className="nsc-res-label">{label}</span>
        <span className="nsc-res-nolimit">sem limit</span>
      </div>
    );
  }
  return (
    <div className="nsc-res-row">
      <span className="nsc-res-label">{label}</span>
      <div className="nsc-bar-wrap">
        <div className="nsc-bar">
          {pct != null && (
            <div className="nsc-bar-fill" style={{ width: `${pct}%`, background: barColor(pct) }} />
          )}
        </div>
        <span className="nsc-res-vals">
          {use && use !== 'N/A' ? `${use} / ${lim}` : lim}
          {pct != null && (
            <span style={{ color: barColor(pct), fontWeight: 600 }}> {pct}%</span>
          )}
        </span>
      </div>
    </div>
  );
}

function ProbeBadge({ label, desc }) {
  return (
    <div className="nsc-probe">
      <span className={`nsc-probe-dot ${desc ? 'ok' : 'miss'}`} />
      <span className="nsc-probe-label">{label}:</span>
      <span className="nsc-probe-val">{desc || <em>ausente</em>}</span>
    </div>
  );
}

function ContainerCard({ c, metricsAvailable }) {
  const stateClass = c.state.startsWith('waiting') || c.state.startsWith('terminated')
    ? 'nsc-cstate-bad' : 'nsc-cstate-ok';
  return (
    <div className="nsc-container">
      <div className="nsc-container-title">
        <span className={`nsc-cstate ${stateClass}`} />
        <strong>{c.name}</strong>
        {!c.ready && <span className="nsc-notready">not ready</span>}
      </div>

      <div className="nsc-resources">
        {metricsAvailable ? (
          <>
            <ResourceBar label="CPU" use={c.cpu_use} lim={c.cpu_lim} pct={c.cpu_pct} />
            <ResourceBar label="Mem" use={c.mem_use} lim={c.mem_lim} pct={c.mem_pct} />
          </>
        ) : (
          <>
            <ResourceBar label="CPU" use={null} lim={c.cpu_lim} pct={null} />
            <ResourceBar label="Mem" use={null} lim={c.mem_lim} pct={null} />
          </>
        )}
      </div>

      <div className="nsc-probes">
        <ProbeBadge label="Liveness"  desc={c.liveness}  />
        <ProbeBadge label="Readiness" desc={c.readiness} />
      </div>
    </div>
  );
}

function PodCard({ pod, metricsAvailable }) {
  const [open, setOpen] = useState(pod.health !== 'healthy');
  return (
    <div className={`nsc-pod nsc-pod-${pod.health}`}>
      <button className="nsc-pod-header" onClick={() => setOpen(o => !o)}>
        <span className="nsc-pod-icon">{H_ICON[pod.health]}</span>
        <span className="nsc-pod-name">{pod.pod}</span>
        <span className="nsc-pod-meta">
          {pod.phase} · {pod.restarts} restart{pod.restarts !== 1 ? 's' : ''}
        </span>
        <span className={`nsc-pod-badge nsc-badge-${pod.health}`}>{H_LABEL[pod.health]}</span>
        <span className="nsc-expand">{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="nsc-pod-body">
          {pod.issues?.length > 0 && (
            <ul className="nsc-issues">
              {pod.issues.map((iss, i) => <li key={i}>{iss}</li>)}
            </ul>
          )}

          {pod.containers?.length > 0 && (
            <div className="nsc-containers">
              {pod.containers.map((c, i) => (
                <ContainerCard key={i} c={c} metricsAvailable={metricsAvailable} />
              ))}
            </div>
          )}

          {pod.events?.length > 0 && (
            <div className="nsc-events">
              <div className="nsc-events-title">⚡ Eventos recentes</div>
              {pod.events.map((e, i) => <div key={i} className="nsc-event">{e}</div>)}
            </div>
          )}

          {pod.recommendation && (
            <div className="nsc-recommendation">💡 {pod.recommendation}</div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- export ---- //
function generateReport(result) {
  const lines = [
    `# Relatório de Saúde — Namespace: ${result.namespace}`,
    `**Data:** ${new Date().toLocaleString('pt-BR')}`,
    `**Saúde geral:** ${H_LABEL[result.overall_health]} (${result.overall_health})`,
    `**Métricas:** ${result.metrics_available ? 'disponíveis' : 'indisponíveis (Metrics Server não encontrado)'}`,
    '',
    `## Resumo`,
    result.summary,
    '',
    `| Status | Quantidade |`,
    `|--------|-----------|`,
    `| ✅ Saudáveis | ${result.healthy_count} |`,
    `| ⚠️ Avisos | ${result.warning_count} |`,
    `| 🔴 Críticos | ${result.critical_count} |`,
    '',
  ];

  if (result.recommendations?.length) {
    lines.push('## Recomendações Gerais', '');
    result.recommendations.forEach(r => lines.push(`- ${r}`));
    lines.push('');
  }

  lines.push('## Pods', '');
  for (const pod of (result.pods || [])) {
    lines.push(`### ${H_ICON[pod.health]} ${pod.pod}`);
    lines.push(`**Status:** ${pod.phase} · **Restarts:** ${pod.restarts} · **Saúde:** ${H_LABEL[pod.health]}`);
    if (pod.issues?.length) {
      lines.push('', '**Problemas:**');
      pod.issues.forEach(i => lines.push(`- ${i}`));
    }
    if (pod.containers?.length) {
      lines.push('', '**Containers:**', '');
      for (const c of pod.containers) {
        lines.push(`#### ${c.name} (${c.state})`);
        lines.push(`- CPU: req=${c.cpu_req || '-'} lim=${c.cpu_lim || 'sem limit'} uso=${c.cpu_use || 'N/A'}${c.cpu_pct != null ? ` (${c.cpu_pct}%)` : ''}`);
        lines.push(`- Mem: req=${c.mem_req} lim=${c.mem_lim} uso=${c.mem_use}${c.mem_pct != null ? ` (${c.mem_pct}%)` : ''}`);
        lines.push(`- Liveness: ${c.liveness || 'AUSENTE ⚠️'}`);
        lines.push(`- Readiness: ${c.readiness || 'AUSENTE ⚠️'}`);
      }
    }
    if (pod.events?.length) {
      lines.push('', '**Eventos:**');
      pod.events.forEach(e => lines.push(`- ${e}`));
    }
    if (pod.recommendation) lines.push('', `💡 **Recomendação:** ${pod.recommendation}`);
    lines.push('');
  }

  return lines.join('\n');
}

function downloadReport(result) {
  const text = generateReport(result);
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `saude-${result.namespace}-${new Date().toISOString().slice(0,10)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---- main component ---- //
export default function NamespaceChecker({ onClose }) {
  const [namespaces, setNamespaces] = useState([]);
  const [selected, setSelected]     = useState('');
  const [custom, setCustom]         = useState('');
  const [status, setStatus]         = useState('idle');
  const [steps, setSteps]           = useState([]);
  const [result, setResult]         = useState(null);
  const [errorMsg, setErrorMsg]     = useState('');
  const abortRef = useRef(false);

  useEffect(() => {
    fetchNamespaces().then(ns => {
      setNamespaces(ns);
      if (ns.length > 0) setSelected(ns[0]);
    });
  }, []);

  const namespace = selected === '__custom__' ? custom.trim() : selected;

  async function handleCheck() {
    if (!namespace) return;
    abortRef.current = false;
    setStatus('running');
    setSteps([]);
    setResult(null);
    setErrorMsg('');

    try {
      await streamNamespaceCheck(namespace, {
        onProgress: data => {
          if (abortRef.current) return;
          setSteps(prev => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.step === data.step && data.step === 'logs') {
              next[next.length - 1] = data;
            } else if (last?.step !== data.step) {
              next.push(data);
            }
            return next;
          });
        },
        onResult:   data => { if (!abortRef.current) setResult(data); },
        onError:    msg  => { if (!abortRef.current) { setErrorMsg(msg); setStatus('error'); } },
      });
      if (!abortRef.current) setStatus('done');
    } catch (e) {
      if (!abortRef.current) { setErrorMsg(e.message); setStatus('error'); }
    }
  }

  function handleClose() { abortRef.current = true; onClose(); }

  const STEP_LABELS = {
    pods:     'Listando pods',
    metrics:  'Métricas CPU/Mem',
    events:   'Eventos',
    logs:     'Lendo logs',
    analysis: 'Análise com IA',
  };

  return (
    <div className="nsc-overlay" onClick={e => e.target === e.currentTarget && handleClose()}>
      <div className="nsc-modal">
        {/* header */}
        <div className="nsc-header">
          <span className="nsc-title">🔍 Verificação de Namespace</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {result && (
              <button className="nsc-btn-export" onClick={() => downloadReport(result)}>
                ⬇ Exportar relatório
              </button>
            )}
            <button className="nsc-close" onClick={handleClose}>✕</button>
          </div>
        </div>

        <div className="nsc-body">
          {/* selector */}
          <div className="nsc-row">
            <select
              className="nsc-select"
              value={selected}
              onChange={e => setSelected(e.target.value)}
              disabled={status === 'running'}
            >
              {namespaces.map(ns => <option key={ns} value={ns}>{ns}</option>)}
              <option value="__custom__">Digitar namespace...</option>
            </select>

            {selected === '__custom__' && (
              <input
                className="nsc-input"
                placeholder="nome-do-namespace"
                value={custom}
                onChange={e => setCustom(e.target.value)}
                disabled={status === 'running'}
                autoFocus
              />
            )}

            <button
              className="nsc-btn-check"
              onClick={handleCheck}
              disabled={status === 'running' || !namespace}
            >
              {status === 'running'
                ? <><span className="nsc-spinner" /> Verificando...</>
                : 'Verificar agora'}
            </button>
          </div>

          {/* progress */}
          {steps.length > 0 && (
            <div className="nsc-progress">
              {steps.map((s, i) => (
                <div key={i} className="nsc-step">
                  <span className="nsc-step-icon">
                    {status === 'running' && i === steps.length - 1
                      ? <span className="nsc-spinner-sm" />
                      : '✓'}
                  </span>
                  <span className="nsc-step-cat">{STEP_LABELS[s.step] || s.step}</span>
                  <span className="nsc-step-msg">{s.message}</span>
                  {s.step === 'logs' && s.total > 1 && (
                    <span className="nsc-step-sub">{s.current}/{s.total}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* error */}
          {status === 'error' && (
            <div className="nsc-error"><strong>Erro:</strong> {errorMsg}</div>
          )}

          {/* result */}
          {result && (
            <div className="nsc-result">
              {/* overall */}
              <div className={`nsc-overall nsc-health-${result.overall_health}`}>
                <span className="nsc-overall-icon">{H_ICON[result.overall_health]}</span>
                <div style={{ flex: 1 }}>
                  <div className="nsc-overall-label">
                    Saúde geral: <strong>{H_LABEL[result.overall_health]}</strong>
                    {!result.metrics_available && (
                      <span className="nsc-metrics-warn"> · métricas indisponíveis</span>
                    )}
                  </div>
                  <div className="nsc-overall-summary">{result.summary}</div>
                </div>
                <div className="nsc-counts">
                  <span className="nsc-count critical">{result.critical_count} crítico{result.critical_count !== 1 ? 's' : ''}</span>
                  <span className="nsc-count warning">{result.warning_count} aviso{result.warning_count !== 1 ? 's' : ''}</span>
                  <span className="nsc-count healthy">{result.healthy_count} saudável{result.healthy_count !== 1 ? 'is' : ''}</span>
                </div>
              </div>

              {/* pods */}
              <div className="nsc-pods">
                {result.pods?.map((pod, i) => (
                  <PodCard key={i} pod={pod} metricsAvailable={result.metrics_available} />
                ))}
              </div>

              {/* global recommendations */}
              {result.recommendations?.length > 0 && (
                <div className="nsc-global-recs">
                  <div className="nsc-recs-title">Recomendações gerais</div>
                  <ul>
                    {result.recommendations.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}

              {!result.metrics_available && (
                <div className="nsc-metrics-tip">
                  ℹ️ Métricas de CPU/memória indisponíveis. Verifique se o <strong>Metrics Server</strong> está instalado no cluster:
                  <code>kubectl get deployment metrics-server -n kube-system</code>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
