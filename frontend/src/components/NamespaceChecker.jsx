import { useState, useEffect, useRef } from 'react';
import { fetchNamespaces, streamNamespaceCheck } from '../api.js';

const HEALTH_LABEL = { healthy: 'Saudável', warning: 'Atenção', critical: 'Crítico' };
const HEALTH_ICON  = { healthy: '✅', warning: '⚠️', critical: '🔴' };

export default function NamespaceChecker({ onClose }) {
  const [namespaces, setNamespaces] = useState([]);
  const [selected, setSelected]     = useState('');
  const [custom, setCustom]         = useState('');
  const [status, setStatus]         = useState('idle'); // idle | running | done | error
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
            } else {
              next.push(data);
            }
            return next;
          });
        },
        onResult: data => {
          if (!abortRef.current) setResult(data);
        },
        onError: msg => {
          if (!abortRef.current) { setErrorMsg(msg); setStatus('error'); }
        },
      });
      if (!abortRef.current) setStatus('done');
    } catch (e) {
      if (!abortRef.current) { setErrorMsg(e.message); setStatus('error'); }
    }
  }

  function handleClose() {
    abortRef.current = true;
    onClose();
  }

  return (
    <div className="nsc-overlay" onClick={e => e.target === e.currentTarget && handleClose()}>
      <div className="nsc-modal">
        <div className="nsc-header">
          <span className="nsc-title">🔍 Verificação de Namespace</span>
          <button className="nsc-close" onClick={handleClose}>✕</button>
        </div>

        <div className="nsc-body">
          {/* ---- selector ---- */}
          <div className="nsc-row">
            <select
              className="nsc-select"
              value={selected}
              onChange={e => setSelected(e.target.value)}
              disabled={status === 'running'}
            >
              {namespaces.map(ns => (
                <option key={ns} value={ns}>{ns}</option>
              ))}
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
              {status === 'running' ? (
                <><span className="nsc-spinner" /> Verificando...</>
              ) : (
                'Verificar agora'
              )}
            </button>
          </div>

          {/* ---- progress ---- */}
          {(status === 'running' || (status === 'done' && steps.length > 0)) && (
            <div className="nsc-progress">
              {steps.map((s, i) => (
                <div key={i} className="nsc-step">
                  <span className="nsc-step-icon">
                    {status === 'running' && i === steps.length - 1
                      ? <span className="nsc-spinner-sm" />
                      : '✓'}
                  </span>
                  <span className="nsc-step-msg">{s.message}</span>
                  {s.step === 'logs' && s.total > 1 && (
                    <span className="nsc-step-sub">{s.current}/{s.total}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ---- error ---- */}
          {status === 'error' && (
            <div className="nsc-error">
              <strong>Erro:</strong> {errorMsg}
            </div>
          )}

          {/* ---- result ---- */}
          {result && (
            <div className="nsc-result">
              <div className={`nsc-overall nsc-health-${result.overall_health}`}>
                <span className="nsc-overall-icon">{HEALTH_ICON[result.overall_health]}</span>
                <div>
                  <div className="nsc-overall-label">
                    Saúde geral: <strong>{HEALTH_LABEL[result.overall_health]}</strong>
                  </div>
                  <div className="nsc-overall-summary">{result.summary}</div>
                </div>
                <div className="nsc-counts">
                  <span className="nsc-count critical">{result.critical_count} crítico{result.critical_count !== 1 ? 's' : ''}</span>
                  <span className="nsc-count warning">{result.warning_count} aviso{result.warning_count !== 1 ? 's' : ''}</span>
                  <span className="nsc-count healthy">{result.healthy_count} saudável{result.healthy_count !== 1 ? 'is' : ''}</span>
                </div>
              </div>

              {result.pods?.length > 0 && (
                <div className="nsc-pods">
                  {result.pods.map((pod, i) => (
                    <div key={i} className={`nsc-pod nsc-pod-${pod.health}`}>
                      <div className="nsc-pod-header">
                        <span className="nsc-pod-icon">{HEALTH_ICON[pod.health]}</span>
                        <span className="nsc-pod-name">{pod.pod}</span>
                        <span className={`nsc-pod-badge nsc-badge-${pod.health}`}>
                          {HEALTH_LABEL[pod.health]}
                        </span>
                      </div>
                      {pod.issues?.length > 0 && (
                        <ul className="nsc-issues">
                          {pod.issues.map((issue, j) => (
                            <li key={j}>{issue}</li>
                          ))}
                        </ul>
                      )}
                      {pod.recommendation && pod.health !== 'healthy' && (
                        <div className="nsc-recommendation">
                          💡 {pod.recommendation}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {result.recommendations?.length > 0 && (
                <div className="nsc-global-recs">
                  <div className="nsc-recs-title">Recomendações gerais</div>
                  <ul>
                    {result.recommendations.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
