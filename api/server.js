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

if (process.env.NODE_ENV === 'production') {
  app.use(express.static(path.join(__dirname, 'public')));
}

// ------------------------------------------------------------------ //
// Helpers                                                             //
// ------------------------------------------------------------------ //

function loadResolved() {
  try {
    if (fs.existsSync(RESOLVED_FILE)) return JSON.parse(fs.readFileSync(RESOLVED_FILE, 'utf-8'));
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
        const data = JSON.parse(fs.readFileSync(path.join(INCIDENTS_DIR, f), 'utf-8'));
        const res = resolved[data.id];
        return {
          ...data,
          resolved: !!res,
          resolvedAt: res ? res.resolvedAt : null,
          postmortem_file: res ? res.postmortem_file : null,
          resolution_description: res ? (res.description || '') : null,
          resolution_time: res ? (res.resolution_time || '') : null,
        };
      } catch (_) { return null; }
    })
    .filter(Boolean)
    .sort((a, b) => b.timestamp - a.timestamp);
}

function findIncidentById(id) {
  if (!fs.existsSync(INCIDENTS_DIR)) return null;
  const files = fs.readdirSync(INCIDENTS_DIR).filter(f => f.startsWith('incident_') && f.endsWith('.json'));
  for (const f of files) {
    try {
      const data = JSON.parse(fs.readFileSync(path.join(INCIDENTS_DIR, f), 'utf-8'));
      if (data.id === id) return data;
    } catch (_) {}
  }
  return null;
}

// ------------------------------------------------------------------ //
// Postmortem generator (Markdown gerado na resolução)                //
// ------------------------------------------------------------------ //

function generatePostmortemMarkdown(incident, resolvedAt, resolution = {}) {
  const fmt = ts => new Date(ts * 1000).toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
  const fmtIso = iso => iso.replace('T', ' ').substring(0, 19) + ' UTC';
  const detectedAt = fmt(incident.timestamp);
  const resolvedStr = fmtIso(resolvedAt);

  const actions = (incident.immediate_action || []).map(a => `- [x] ${a}`).join('\n');
  const prevention = (incident.prevention || []).map(p => `- ${p}`).join('\n');
  const context = (incident.context || []).join('\n') || '(sem contexto capturado)';
  const resolutionDesc = resolution.description || '_Não informado_';
  const resolutionTime = resolution.resolution_time ? `**Tempo de resolução:** ${resolution.resolution_time}` : '';

  return `# Postmortem — ${incident.pod} — ${detectedAt}

## Resumo do Incidente

| Campo | Valor |
|-------|-------|
| **Pod** | \`${incident.pod}\` |
| **Namespace** | \`${incident.namespace}\` |
| **Severidade** | \`${incident.severity}\` |
| **Detectado em** | ${detectedAt} |
| **Resolvido em** | ${resolvedStr} |
| **Hash do erro** | \`${incident.id}\` |
| **Status** | Resolvido ✅ |

## Detalhe do Erro

\`\`\`
${incident.error_line}
\`\`\`

## Contexto do Log (linhas anteriores ao erro)

\`\`\`
${context}
\`\`\`

## Análise de IA

### Causa Raiz

${incident.root_cause}

### Impacto Estimado

${incident.estimated_impact}

### Resumo

${incident.summary}

## ✅ Solução Aplicada

${resolutionDesc}

${resolutionTime}

## Ações Imediatas Sugeridas pela IA

${actions || '- [x] Investigação manual realizada'}

## Medidas de Prevenção

${prevention || '- Revisar tratamento de erros da aplicação'}

## Linha do Tempo

| Horário | Evento |
|---------|--------|
| ${detectedAt} | Erro detectado nos logs do pod |
| ${resolvedStr} | Incidente marcado como resolvido no dashboard |

## Checklist Pós-Incidente

- [x] Causa raiz identificada
- [x] Incidente resolvido
- [ ] Medidas de prevenção implementadas
- [ ] Monitoramento/alertas revisados
- [ ] Partes interessadas notificadas
`;
}

function savePostmortem(incident, resolvedAt, resolution = {}) {
  const date = new Date(resolvedAt);
  const ts = date.toISOString().replace(/[-:T]/g, '').substring(0, 15);
  const hash = incident.id.substring(0, 8);
  const filename = `postmortem_${ts}_${hash}.md`;
  const filepath = path.join(INCIDENTS_DIR, filename);
  fs.writeFileSync(filepath, generatePostmortemMarkdown(incident, resolvedAt, resolution), 'utf-8');
  console.log(`Postmortem gerado: ${filepath}`);
  return filename;
}

// ------------------------------------------------------------------ //
// SSE                                                                 //
// ------------------------------------------------------------------ //

const sseClients = new Set();

function broadcast(event, data) {
  const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  sseClients.forEach(client => client.write(payload));
}

// ------------------------------------------------------------------ //
// Routes                                                              //
// ------------------------------------------------------------------ //

app.get('/api/incidents', (req, res) => res.json(loadIncidents()));

app.patch('/api/incidents/:id/resolve', (req, res) => {
  const { id } = req.params;
  const { description = '', resolution_time = '' } = req.body || {};

  const incident = findIncidentById(id);
  if (!incident) return res.status(404).json({ error: 'Incidente não encontrado' });

  const resolvedAt = new Date().toISOString();
  const resolution = { description, resolution_time };
  const postmortemFile = savePostmortem(incident, resolvedAt, resolution);

  const resolved = loadResolved();
  resolved[id] = { resolvedAt, postmortem_file: postmortemFile, ...resolution };
  saveResolved(resolved);

  broadcast('update', {
    type: 'resolved', id, resolvedAt,
    postmortem_file: postmortemFile,
    resolution_description: description,
    resolution_time,
  });
  res.json({ ok: true, postmortem_file: postmortemFile });
});

app.patch('/api/incidents/:id/reopen', (req, res) => {
  const { id } = req.params;
  const resolved = loadResolved();
  delete resolved[id];
  saveResolved(resolved);
  broadcast('update', { type: 'reopened', id });
  res.json({ ok: true });
});

app.get('/api/incidents/:id/postmortem', (req, res) => {
  const { id } = req.params;
  const resolved = loadResolved();
  const entry = resolved[id];
  if (!entry || !entry.postmortem_file) return res.status(404).json({ error: 'Postmortem não gerado ainda' });

  const filepath = path.join(INCIDENTS_DIR, entry.postmortem_file);
  if (!fs.existsSync(filepath)) return res.status(404).json({ error: 'Arquivo não encontrado' });

  res.setHeader('Content-Type', 'text/markdown; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="${entry.postmortem_file}"`);
  res.send(fs.readFileSync(filepath));
});

app.get('/api/events', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();
  res.write('event: connected\ndata: {}\n\n');

  const heartbeat = setInterval(() => res.write(': ping\n\n'), 30000);
  sseClients.add(res);
  req.on('close', () => { clearInterval(heartbeat); sseClients.delete(res); });
});

if (process.env.NODE_ENV === 'production') {
  app.get('*', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));
}

// ------------------------------------------------------------------ //
// File watcher                                                        //
// ------------------------------------------------------------------ //

fs.mkdirSync(INCIDENTS_DIR, { recursive: true });

chokidar
  .watch(INCIDENTS_DIR, { ignoreInitial: true, awaitWriteFinish: { stabilityThreshold: 300 } })
  .on('add', filePath => {
    const basename = path.basename(filePath);
    if (!basename.startsWith('incident_') || !basename.endsWith('.json')) return;
    try {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
      broadcast('incident', { ...data, resolved: false });
    } catch (_) {}
  });

app.listen(PORT, () => {
  console.log(`k8s-monitor API rodando na porta ${PORT}`);
  console.log(`Monitorando incidentes em: ${INCIDENTS_DIR}`);
});
