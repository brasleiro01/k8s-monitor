const express = require('express');
const fs = require('fs');
const path = require('path');
const chokidar = require('chokidar');

const app = express();
const PORT = process.env.PORT || 3001;
const INCIDENTS_DIR = process.env.INCIDENTS_DIR || path.join(__dirname, '..', 'postmortems');
const RESOLVED_FILE = path.join(INCIDENTS_DIR, '.resolved.json');

app.use(express.json());

app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, PATCH, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// Serve React build in production
if (process.env.NODE_ENV === 'production') {
  const publicDir = path.join(__dirname, 'public');
  app.use(express.static(publicDir));
}

// ------------------------------------------------------------------ //
// Helpers                                                             //
// ------------------------------------------------------------------ //

function loadResolved() {
  try {
    if (fs.existsSync(RESOLVED_FILE)) {
      return JSON.parse(fs.readFileSync(RESOLVED_FILE, 'utf-8'));
    }
  } catch (_) {}
  return {};
}

function saveResolved(resolved) {
  fs.writeFileSync(RESOLVED_FILE, JSON.stringify(resolved, null, 2));
}

function loadIncidents() {
  if (!fs.existsSync(INCIDENTS_DIR)) return [];
  const resolved = loadResolved();

  return fs
    .readdirSync(INCIDENTS_DIR)
    .filter(f => f.startsWith('incident_') && f.endsWith('.json'))
    .map(f => {
      try {
        const raw = fs.readFileSync(path.join(INCIDENTS_DIR, f), 'utf-8');
        const data = JSON.parse(raw);
        return { ...data, resolved: !!resolved[data.id], resolvedAt: resolved[data.id] || null };
      } catch (_) {
        return null;
      }
    })
    .filter(Boolean)
    .sort((a, b) => b.timestamp - a.timestamp);
}

// ------------------------------------------------------------------ //
// SSE clients                                                         //
// ------------------------------------------------------------------ //

const sseClients = new Set();

function broadcast(event, data) {
  const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  sseClients.forEach(client => client.write(payload));
}

// ------------------------------------------------------------------ //
// Routes                                                              //
// ------------------------------------------------------------------ //

app.get('/api/incidents', (req, res) => {
  res.json(loadIncidents());
});

app.patch('/api/incidents/:id/resolve', (req, res) => {
  const { id } = req.params;
  const resolved = loadResolved();
  resolved[id] = new Date().toISOString();
  saveResolved(resolved);
  broadcast('update', { type: 'resolved', id });
  res.json({ ok: true });
});

app.patch('/api/incidents/:id/reopen', (req, res) => {
  const { id } = req.params;
  const resolved = loadResolved();
  delete resolved[id];
  saveResolved(resolved);
  broadcast('update', { type: 'reopened', id });
  res.json({ ok: true });
});

app.get('/api/events', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();
  res.write('event: connected\ndata: {}\n\n');

  const heartbeat = setInterval(() => res.write(': ping\n\n'), 30000);
  sseClients.add(res);

  req.on('close', () => {
    clearInterval(heartbeat);
    sseClients.delete(res);
  });
});

// SPA fallback in production
if (process.env.NODE_ENV === 'production') {
  app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
  });
}

// ------------------------------------------------------------------ //
// File watcher — notify clients when a new incident arrives          //
// ------------------------------------------------------------------ //

fs.mkdirSync(INCIDENTS_DIR, { recursive: true });

chokidar
  .watch(INCIDENTS_DIR, { ignoreInitial: true, awaitWriteFinish: { stabilityThreshold: 300 } })
  .on('add', filePath => {
    const basename = path.basename(filePath);
    if (!basename.startsWith('incident_') || !basename.endsWith('.json')) return;
    try {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
      broadcast('incident', data);
    } catch (_) {}
  });

app.listen(PORT, () => {
  console.log(`k8s-monitor API running on port ${PORT}`);
  console.log(`Watching incidents in: ${INCIDENTS_DIR}`);
});
