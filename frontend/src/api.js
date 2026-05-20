const BASE = '/api';

export async function fetchIncidents() {
  const res = await fetch(`${BASE}/incidents`);
  if (!res.ok) throw new Error('Failed to fetch incidents');
  return res.json();
}

export async function resolveIncident(id) {
  const res = await fetch(`${BASE}/incidents/${id}/resolve`, { method: 'PATCH' });
  if (!res.ok) throw new Error('Falha ao resolver incidente');
  return res.json();
}

export async function reopenIncident(id) {
  const res = await fetch(`${BASE}/incidents/${id}/reopen`, { method: 'PATCH' });
  if (!res.ok) throw new Error('Failed to reopen incident');
  return res.json();
}

export function subscribeToEvents(onIncident, onUpdate) {
  const source = new EventSource(`${BASE}/events`);
  source.addEventListener('incident', e => onIncident(JSON.parse(e.data)));
  source.addEventListener('update', e => onUpdate(JSON.parse(e.data)));
  source.onerror = () => source.close();
  return () => source.close();
}
