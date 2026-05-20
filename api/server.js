'use strict';
const express = require('express');
const path = require('path');
const db = require('./db');

const app = express();
const PORT = process.env.PORT || 3001;

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
// Config (runtime — lida pelo frontend antes de renderizar)           //
// ------------------------------------------------------------------ //

app.get('/api/config', (req, res) => {
  res.json({ googleClientId: process.env.GOOGLE_CLIENT_ID || '' });
});

// ------------------------------------------------------------------ //
// Postmortem Markdown (gerado em memória na resolução)                //
// ------------------------------------------------------------------ //

function generatePostmortemMarkdown(incident, resolvedAt, resolution = {}) {
  const fmt = ts => new Date(ts * 1000).toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
  const fmtIso = iso => iso.replace('T', ' ').substring(0, 19) + ' UTC';
  const detectedAt = fmt(incident.timestamp || incident.first_seen);
  const resolvedStr = fmtIso(resolvedAt);

  const actions = (incident.immediate_action || []).map(a => `- [x] ${a}`).join('\n');
  const prevention = (incident.prevention || []).map(p => `- ${p}`).join('\n');
  const context = (incident.context || []).join('\n') || '(sem contexto capturado)';
  const resolutionDesc = resolution.description || '_Não informado_';
  const resolutionTime = resolution.resolution_time
    ? `**Tempo de resolução:** ${resolution.resolution_time}`
    : '';

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

app.get('/api/incidents', async (req, res) => {
  const rows = await db.loadIncidents();
  if (rows === null) return res.status(503).json({ error: 'Banco de dados indisponível' });
  res.json(rows);
});

app.patch('/api/incidents/:id/resolve', async (req, res) => {
  const { id } = req.params;
  const { description = '', resolution_time = '' } = req.body || {};

  const incident = await db.findIncidentById(id);
  if (!incident) return res.status(404).json({ error: 'Incidente não encontrado' });

  const resolvedAt = new Date().toISOString();
  const resolution = { description, resolution_time };
  const postmortemContent = generatePostmortemMarkdown(incident, resolvedAt, resolution);
  const postmortemFile = `postmortem_${resolvedAt.replace(/[-:T]/g, '').substring(0, 15)}_${id.substring(0, 8)}.md`;

  await db.resolveIncident(id, resolvedAt, description, resolution_time, postmortemFile, postmortemContent);

  broadcast('update', {
    type: 'resolved', id, resolvedAt,
    postmortem_file: postmortemFile,
    resolution_description: description,
    resolution_time,
  });
  res.json({ ok: true, postmortem_file: postmortemFile });
});

app.patch('/api/incidents/:id/reopen', async (req, res) => {
  const { id } = req.params;
  await db.reopenIncident(id);
  broadcast('update', { type: 'reopened', id });
  res.json({ ok: true });
});

app.get('/api/incidents/:id/postmortem', async (req, res) => {
  const { id } = req.params;
  const view = req.query.view === '1';

  const row = await db.getPostmortemContent(id);
  if (!row || !row.postmortem_content) {
    return res.status(404).json({ error: 'Postmortem não gerado ainda' });
  }

  const filename = row.postmortem_file || `postmortem_${id.substring(0, 8)}.md`;
  res.setHeader('Content-Type', 'text/markdown; charset=utf-8');
  res.setHeader('Content-Disposition', `${view ? 'inline' : 'attachment'}; filename="${filename}"`);
  res.send(row.postmortem_content);
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
// DB polling — substitui o chokidar para push SSE em tempo real      //
// ------------------------------------------------------------------ //

const knownIds = new Set();

async function initPolling() {
  const rows = await db.loadIncidents();
  if (rows) rows.forEach(r => knownIds.add(r.id));

  setInterval(async () => {
    if (sseClients.size === 0) return;
    const rows = await db.loadIncidents();
    if (!rows) return;
    for (const incident of rows) {
      if (!knownIds.has(incident.id)) {
        knownIds.add(incident.id);
        broadcast('incident', { ...incident, resolved: false });
      }
    }
  }, 5000);
}

app.listen(PORT, () => {
  console.log(`k8s-monitor API rodando na porta ${PORT}`);
  initPolling().catch(e => console.error('[polling] init error:', e.message));
});
