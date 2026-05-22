const BASE = '/api';

export async function fetchIncidents() {
  const res = await fetch(`${BASE}/incidents`);
  if (!res.ok) throw new Error('Failed to fetch incidents');
  return res.json();
}

export async function resolveIncident(id, resolution) {
  const res = await fetch(`${BASE}/incidents/${id}/resolve`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(resolution),
  });
  if (!res.ok) throw new Error('Falha ao resolver incidente');
  return res.json();
}

export async function reopenIncident(id) {
  const res = await fetch(`${BASE}/incidents/${id}/reopen`, { method: 'PATCH' });
  if (!res.ok) throw new Error('Failed to reopen incident');
  return res.json();
}

export async function fetchClusterOverview() {
  const res = await fetch(`${BASE}/cluster-overview`);
  if (!res.ok) throw new Error('Failed to fetch cluster overview');
  return res.json();
}

export async function fetchNamespaces() {
  const res = await fetch(`${BASE}/namespaces`);
  if (!res.ok) return [];
  return res.json();
}

export async function streamNamespaceCheck(namespace, { onProgress, onResult, onError }) {
  const res = await fetch(`${BASE}/check-namespace`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ namespace }),
  });
  if (!res.ok) throw new Error('Falha ao iniciar verificação');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split('\n\n');
    buf = chunks.pop();
    for (const chunk of chunks) {
      if (!chunk.trim()) continue;
      const evtMatch = chunk.match(/^event:\s*(\w+)/m);
      const dataMatch = chunk.match(/^data:\s*(.+)/m);
      if (!evtMatch || !dataMatch) continue;
      let data;
      try { data = JSON.parse(dataMatch[1]); } catch { continue; }
      if (evtMatch[1] === 'progress') onProgress?.(data);
      else if (evtMatch[1] === 'result') onResult?.(data);
      else if (evtMatch[1] === 'error') onError?.(data.message);
    }
  }
}

export function subscribeToEvents(onIncident, onUpdate, onReport) {
  const source = new EventSource(`${BASE}/events`);
  source.addEventListener('incident', e => onIncident(JSON.parse(e.data)));
  source.addEventListener('update', e => onUpdate(JSON.parse(e.data)));
  if (onReport) source.addEventListener('report', e => onReport(JSON.parse(e.data)));
  source.onerror = () => source.close();
  return () => source.close();
}

export async function fetchReportSchedule() {
  const res = await fetch(`${BASE}/report-schedule`);
  if (!res.ok) throw new Error('Failed to fetch schedule');
  return res.json();
}

export async function setReportSchedule(scheduledTime, enabled) {
  const res = await fetch(`${BASE}/report-schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scheduled_time: scheduledTime, enabled }),
  });
  if (!res.ok) throw new Error('Failed to set schedule');
  return res.json();
}

export async function fetchClusterReports() {
  const res = await fetch(`${BASE}/cluster-reports`);
  if (!res.ok) throw new Error('Failed to fetch reports');
  return res.json();
}

export async function fetchClusterReportDetail(id) {
  const res = await fetch(`${BASE}/cluster-reports/${id}`);
  if (!res.ok) throw new Error('Failed to fetch report');
  return res.json();
}
