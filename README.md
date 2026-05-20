# K8s Monitor

Aplicação de monitoramento de logs para Kubernetes que detecta erros automaticamente, analisa a causa raiz com inteligência artificial (Google Gemini), envia notificações no Discord, gera postmortems e exibe tudo em um dashboard web em tempo real com suporte a múltiplos usuários.

---

## Sumário

- [Como funciona](#como-funciona)
- [Arquitetura](#arquitetura)
- [Funcionalidades do dashboard](#funcionalidades-do-dashboard)
- [Pré-requisitos](#pré-requisitos)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Configuração inicial](#configuração-inicial)
- [Rodando localmente (desenvolvimento)](#rodando-localmente-desenvolvimento)
- [Build das imagens Docker](#build-das-imagens-docker)
- [Deploy no Kubernetes](#deploy-no-kubernetes)
- [Acessando o dashboard](#acessando-o-dashboard)
- [Banco de dados Postgres (opcional)](#banco-de-dados-postgres-opcional)
- [Login com Google OAuth (opcional)](#login-com-google-oauth-opcional)
- [Estrutura de arquivos](#estrutura-de-arquivos)
- [CI/CD com GitHub Actions](#cicd-com-github-actions)
- [Manutenção](#manutenção)

---

## Como funciona

```
Pods K8s → Logs → Detector de erros → Gemini AI → Discord + Postmortem .md → Dashboard web
```

1. **Monitor Python** conecta via API do Kubernetes e abre um stream de logs para cada pod em execução (uma thread por pod).
2. **Detector de erros** analisa cada linha com expressões regulares. O mesmo erro no mesmo pod é suprimido por até **1 hora** (cooldown) e no máximo **5 vezes por dia** — estado persiste no PVC e sobrevive a reinicializações.
3. **Analisador de IA** tenta três camadas em cascata:
   - **Gemini** (`gemini-2.0-flash`) — análise completa com causa raiz, severidade e ações
   - **rule_analyzer** — fallback baseado em regras (OOM, CrashLoop, TLS, banco, Prometheus, etc.)
   - **last_resort** — resposta genérica garantindo sempre um resultado
4. **Notificador Discord** envia um embed colorido por severidade com toda a análise.
5. **Gerador de postmortem** salva dois arquivos por incidente: `postmortem_*.md` (relatório legível) e `incident_*.json` (dados para o dashboard). Se `DATABASE_URL` estiver configurado, persiste também no Postgres.
6. **API Node.js** lê os incidentes (DB ou arquivos), expõe endpoints REST e envia atualizações em tempo real via Server-Sent Events (SSE).
7. **Dashboard React** exibe os incidentes agrupados por namespace → pod, com filtros, temas e sessões por usuário.

### Severidades detectadas

| Severidade | Cor | Exemplos de padrão |
|------------|-----|--------------------|
| Critical   | 🔴  | `FATAL`, `panic:`, `OOMKilled`, `CrashLoopBackOff`, `runtime error:` |
| High       | 🟠  | `ERROR`, `EXCEPTION`, `java.lang.*Exception`, `x509`, `database.*error` |
| Medium     | 🟡  | `CRITICAL`, `SEVERE`, `ImagePullBackOff`, `permission denied` |
| Low        | 🟢  | Outros padrões, coletores Prometheus desabilitados |

---

## Arquitetura

```
┌──────────────────────────────────────────────────────────────────┐
│                      Namespace: k8s-monitor                      │
│                                                                  │
│  ┌──────────────────┐          ┌──────────────────────────────┐  │
│  │   k8s-monitor    │          │     k8s-monitor-ui           │  │
│  │   (Python)       │          │     (Node.js + React)        │  │
│  │                  │          │                              │  │
│  │ monitor.py       │          │  api/server.js  (porta 3001) │  │
│  │ k8s_watcher.py   │          │  api/db.js      (Postgres)   │  │
│  │ error_detector   │          │  frontend/dist/ (React SPA)  │  │
│  │ rule_analyzer    │          └──────────────┬───────────────┘  │
│  │ ai_analyzer      │                         │                  │
│  │ discord_notifier │          ┌──────────────▼───────────────┐  │
│  │ postmortem_gen   │          │   PersistentVolumeClaim       │  │
│  │ db.py            │◄────────►│   /postmortems                │  │
│  └──────────────────┘          │   incident_*.json             │  │
│                                │   postmortem_*.md             │  │
│  ┌──────────────────┐          │   .alert_state.json           │  │
│  │ postgres-k8s-    │          └──────────────────────────────┘  │
│  │ monitor          │◄── DATABASE_URL (opcional) ────────────────┤
│  │ (postgres:16)    │                                            │
│  └──────────────────┘                                            │
└──────────────────────────────────────────────────────────────────┘
          │                              │
          ▼                              ▼
    Discord Webhook              Browser (dashboard)
```

---

## Funcionalidades do dashboard

| Funcionalidade | Descrição |
|----------------|-----------|
| **Multi-usuário** | Cada usuário tem sessão e tema independentes, armazenados no localStorage |
| **Login com nome** | Basta digitar um nome — sem senha, ideal para uso interno |
| **Login com Google** | Opcional via OAuth 2.0 (requer `VITE_GOOGLE_CLIENT_ID`) |
| **Tema claro / escuro** | Toggle por usuário, persiste entre sessões |
| **Agrupamento por namespace** | Incidentes organizados em namespace (externo) → pod (interno) |
| **Deduplicação** | Mesmo erro no mesmo pod agrupa em 1 card com contador ×N de ocorrências |
| **Filtros** | Por severidade, status (aberto/resolvido), namespace, texto livre e **período de datas** |
| **Tempo real** | Novos incidentes aparecem automaticamente via SSE sem recarregar |
| **Análise da IA** | Causa raiz, impacto estimado, ações imediatas e prevenção |
| **Contexto de log** | Linhas anteriores ao erro (expansível) |
| **Postmortem inline** | Botão "Visualizar" abre o `.md` renderizado em modal, sem download obrigatório |
| **Histórico de resoluções** | Drawer lateral lista todos os postmortems com opção de visualizar e baixar |
| **Indicador de conexão** | Badge "Ao vivo" / "Conectando..." e ponto vermelho quando há critical aberto |

---

## Pré-requisitos

| Ferramenta | Versão mínima | Para quê |
|------------|---------------|----------|
| Docker | 24+ | Build das imagens |
| kubectl | 1.28+ | Deploy no cluster |
| Python | 3.11+ | Rodar o monitor localmente |
| Node.js | 20+ | Rodar o dashboard localmente |
| Conta Docker Hub | — | Armazenar as imagens |
| Chave API Google Gemini | — | Análise de IA |
| Webhook Discord | — | Notificações |

---

## Variáveis de ambiente

### Monitor Python

| Variável | Obrigatório | Padrão | Descrição |
|----------|-------------|--------|-----------|
| `GEMINI_API_KEY` | ✅ | — | Chave da API Google Gemini (Google AI Studio) |
| `DISCORD_WEBHOOK_URL` | ✅ | — | URL do webhook do Discord |
| `NAMESPACES` | — | *(todos)* | Namespaces a monitorar, separados por vírgula. Ex: `default,production` |
| `GEMINI_MODEL` | — | `gemini-2.0-flash` | Modelo Gemini a usar |
| `ERROR_COOLDOWN_SECONDS` | — | `3600` | Cooldown (em segundos) entre alertas do mesmo erro |
| `MAX_DAILY_ALERTS` | — | `5` | Máximo de alertas por erro por dia |
| `CONTEXT_LINES` | — | `10` | Linhas de log capturadas antes de cada erro |
| `LOG_LINES_TAIL` | — | `100` | Linhas lidas ao conectar num pod pela primeira vez |
| `POSTMORTEM_DIR` | — | `./postmortems` | Diretório onde os arquivos são salvos |
| `EXCLUDE_LOG_PATTERNS` | — | *(vazio)* | Padrões a ignorar, separados por `\|\|`. Ex: `healthcheck\|\|readiness` |
| `DATABASE_URL` | — | *(vazio)* | URL do Postgres. Ex: `postgresql://user:pass@host:5432/db` |
| `LOG_LEVEL` | — | `INFO` | Nível de log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### API Node.js

| Variável | Obrigatório | Padrão | Descrição |
|----------|-------------|--------|-----------|
| `DATABASE_URL` | — | *(vazio)* | Mesma URL do Postgres (DB primeiro, arquivos como fallback) |
| `DB_SSL` | — | `0` | `1` para Aurora/RDS com SSL, `0` para Postgres local |
| `INCIDENTS_DIR` | — | `./postmortems` | Diretório dos arquivos JSON (igual ao Python) |
| `PORT` | — | `3001` | Porta da API |

### Frontend (build-time)

| Variável | Obrigatório | Padrão | Descrição |
|----------|-------------|--------|-----------|
| `VITE_GOOGLE_CLIENT_ID` | — | *(vazio)* | Client ID do Google OAuth. Se vazio, o botão Google não aparece |

---

## Configuração inicial

### 1. Chave da API Google Gemini

1. Acesse [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Clique em **Create API Key**
3. Copie a chave no formato `AIza...`

### 2. Webhook do Discord

1. No Discord, abra o canal desejado → **Edit Channel** → **Integrations** → **Webhooks** → **New Webhook**
2. Dê um nome (ex: `K8s Monitor`) e clique em **Copy Webhook URL**
3. O URL terá o formato: `https://discord.com/api/webhooks/ID/TOKEN`

> **Segurança:** nunca exponha o URL do webhook ou a API key em código ou commits. Use Kubernetes Secrets.

### 3. Criar o Secret de credenciais

```bash
kubectl create secret generic k8s-monitor-secrets \
  --namespace k8s-monitor \
  --from-literal=gemini-api-key=AIza... \
  --from-literal=discord-webhook-url=https://discord.com/api/webhooks/...
```

Para incluir também a URL do banco de dados:

```bash
kubectl create secret generic k8s-monitor-secrets \
  --namespace k8s-monitor \
  --from-literal=gemini-api-key=AIza... \
  --from-literal=discord-webhook-url=https://discord.com/api/webhooks/... \
  --from-literal=database-url=postgresql://k8smonitor:SENHA@postgres-k8s-monitor:5432/k8smonitor
```

---

## Rodando localmente (desenvolvimento)

### Monitor Python

```bash
cd monitor
pip install -r requirements.txt

# Copie e edite o .env
cp .env.example .env

export $(cat .env | xargs)
python monitor.py
```

### API Node.js

```bash
cd api
npm install
npm run dev
# API disponível em http://localhost:3001
```

### Frontend React

```bash
cd frontend
npm install
npm run dev
# Dashboard disponível em http://localhost:5173
```

O Vite faz proxy de `/api` para `localhost:3001` automaticamente.

Para habilitar o login com Google em desenvolvimento, crie `frontend/.env.local`:

```
VITE_GOOGLE_CLIENT_ID=seu-client-id.apps.googleusercontent.com
```

---

## Build das imagens Docker

O projeto tem **duas imagens**:

| Imagem | Diretório | Descrição |
|--------|-----------|-----------|
| `k8s-monitor` | raiz `/` | Monitor Python |
| `k8s-monitor-ui` | `frontend/` + `api/` | Dashboard React + API Node.js (multi-stage) |

```bash
# Monitor Python (contexto é monitor/)
docker build -t SEU_USUARIO/k8s-monitor:latest monitor/

# Dashboard (contexto é a raiz para incluir api/ e frontend/)
docker build -f frontend/Dockerfile -t SEU_USUARIO/k8s-monitor-ui:latest .
```

### Build multi-arquitetura (para clusters ARM/Graviton)

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --push \
  -t SEU_USUARIO/k8s-monitor:latest \
  monitor/

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --push \
  -f frontend/Dockerfile \
  -t SEU_USUARIO/k8s-monitor-ui:latest .
```

---

## Deploy no Kubernetes

### Ordem de aplicação

```bash
# 1. Namespace + RBAC (leitura de pods/logs em todo o cluster)
kubectl apply -f k8s/rbac.yaml

# 2. (Opcional) Postgres interno no cluster
kubectl apply -f k8s/postgres.yaml
# Troque a senha em postgres-k8s-monitor-secret antes!

# 3. Secret com credenciais (Gemini + Discord + opcionalmente DATABASE_URL)
kubectl create secret generic k8s-monitor-secrets \
  --namespace k8s-monitor \
  --from-literal=gemini-api-key=AIza... \
  --from-literal=discord-webhook-url=https://discord.com/api/webhooks/...

# 4. Monitor Python
kubectl apply -f k8s/deployment.yaml

# 5. Dashboard (API + React)
kubectl apply -f k8s/frontend.yaml
```

### Verificar se está tudo rodando

```bash
kubectl get all -n k8s-monitor
```

Saída esperada:

```
NAME                                     READY   STATUS    RESTARTS
pod/k8s-monitor-xxxx                     1/1     Running   0
pod/k8s-monitor-ui-xxxx                  1/1     Running   0
pod/postgres-k8s-monitor-xxxx            1/1     Running   0   # se Postgres habilitado

NAME                          TYPE        CLUSTER-IP    PORT(S)
service/k8s-monitor-ui        ClusterIP   10.x.x.x      80/TCP
service/postgres-k8s-monitor  ClusterIP   10.x.x.x      5432/TCP

NAME                               READY   UP-TO-DATE
deployment.apps/k8s-monitor        1/1     1
deployment.apps/k8s-monitor-ui     1/1     1
deployment.apps/postgres-k8s-monitor  1/1  1
```

---

## Acessando o dashboard

### Port-forward (acesso rápido)

```bash
kubectl port-forward service/k8s-monitor-ui 8080:80 -n k8s-monitor
```

Abra `http://localhost:8080` no browser.

### Ingress (acesso externo permanente)

O arquivo `k8s/frontend.yaml` já inclui um bloco de Ingress pré-configurado para `k8s-monitor.mvps.com.br`. Edite o host conforme seu domínio e aplique:

```bash
kubectl apply -f k8s/frontend.yaml
```

---

## Banco de dados Postgres (opcional)

Por padrão a aplicação usa arquivos JSON no PVC. Para persistência relacional, você pode usar um Postgres interno no cluster ou um Aurora/RDS externo.

### Opção A — Postgres interno (k8s/postgres.yaml)

O manifesto cria: Secret com credenciais, PVC de 5 Gi, ConfigMap com `init.sql` (extensões `pg_trgm` e `btree_gin`), Deployment com `postgres:16-alpine` e Service ClusterIP.

**Antes de aplicar, troque a senha no Secret:**

```bash
# Edite k8s/postgres.yaml — linha POSTGRES_PASSWORD e DATABASE_URL
kubectl apply -f k8s/postgres.yaml
```

Depois adicione `database-url` ao Secret de credenciais e descomente o bloco `DATABASE_URL` em `k8s/deployment.yaml`:

```yaml
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: k8s-monitor-secrets
      key: database-url
```

### Opção B — Aurora / RDS externo

Crie o Secret com a URL de conexão e habilite SSL:

```bash
kubectl create secret generic k8s-monitor-secrets \
  --namespace k8s-monitor \
  --from-literal=gemini-api-key=AIza... \
  --from-literal=discord-webhook-url=https://discord.com/api/webhooks/... \
  --from-literal=database-url=postgresql://user:pass@cluster.region.rds.amazonaws.com:5432/k8smonitor
```

Em `k8s/deployment.yaml`, descomente também:

```yaml
- name: DB_SSL
  value: "1"
```

### Fallback transparente

Se `DATABASE_URL` não estiver configurado, **todo o sistema continua funcionando com arquivos**. Não há mudança de comportamento para deployments existentes.

---

## Login com Google OAuth (opcional)

O botão de login com Google aparece automaticamente quando `VITE_GOOGLE_CLIENT_ID` está configurado. É completamente opcional — o login por nome continua disponível sempre.

### Configurar o Client ID

1. Acesse [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials
2. Crie um **OAuth 2.0 Client ID** do tipo "Web application"
3. Adicione em "Authorized JavaScript origins": `http://localhost:5173` (dev) e o domínio de produção
4. Copie o Client ID no formato `*.apps.googleusercontent.com`

Para produção, passe o Client ID como `build-arg` ao construir a imagem:

```bash
docker build -f frontend/Dockerfile \
  --build-arg VITE_GOOGLE_CLIENT_ID=seu-client-id.apps.googleusercontent.com \
  -t SEU_USUARIO/k8s-monitor-ui:latest .
```

---

## Estrutura de arquivos

```
monitoramento/
│
├── monitor/                 # Monitor Python
│   ├── Dockerfile           # Imagem do monitor Python
│   ├── requirements.txt     # Dependências Python (inclui psycopg2-binary)
│   ├── .env.example         # Exemplo de variáveis de ambiente
│   ├── monitor.py           # Ponto de entrada — orquestra todos os componentes
│   ├── config.py            # Configurações via env vars (Gemini, cooldown, limites, etc.)
│   ├── k8s_watcher.py       # Conexão com Kubernetes — watch de pods e stream de logs
│   ├── error_detector.py    # Detecção por regex + deduplicação + rate limiting persistido
│   ├── rule_analyzer.py     # Fallback de análise por regras (quando Gemini falha)
│   ├── ai_analyzer.py       # Integração com Google Gemini (cascata: Gemini → rules → fallback)
│   ├── discord_notifier.py  # Notificações Discord com embed colorido por severidade
│   ├── postmortem_generator.py  # Gera postmortem_*.md, incident_*.json e persiste no DB
│   └── db.py                # Camada Postgres (Python) — transparente quando não configurado
│
├── api/
│   ├── package.json         # Dependências Node.js (inclui pg)
│   ├── server.js            # Express REST API + SSE + file watcher (chokidar)
│   └── db.js                # Camada Postgres (Node.js) — DB-first com fallback em arquivos
│
├── frontend/
│   ├── Dockerfile           # Build multi-stage: Vite → Node.js
│   ├── package.json         # Inclui @react-oauth/google e marked
│   ├── .env.example         # Documenta VITE_GOOGLE_CLIENT_ID
│   ├── vite.config.js       # Proxy /api → localhost:3001
│   ├── index.html
│   └── src/
│       ├── App.jsx           # Estado global, auth, tema, SSE, filtros, agrupamento
│       ├── App.css           # Tema claro + tema escuro (html[data-theme="dark"])
│       ├── api.js            # Funções fetch para a API
│       └── components/
│           ├── LoginScreen.jsx      # Tela de login (nome + Google OAuth opcional)
│           ├── StatsBar.jsx         # Contadores por severidade
│           ├── Filters.jsx          # Filtros: severidade, status, namespace, texto, período
│           ├── NamespaceGroup.jsx   # Grupo externo por namespace (expansível)
│           ├── PodGroup.jsx         # Grupo interno por pod com deduplicação e ×N badge
│           ├── IncidentCard.jsx     # Card expansível com análise, contexto e ações
│           ├── PostmortemViewer.jsx # Modal de visualização inline do postmortem (.md)
│           └── PostmortemsDrawer.jsx # Drawer lateral com histórico de resoluções
│
└── k8s/
    ├── rbac.yaml            # Namespace k8s-monitor + ServiceAccount + ClusterRole
    ├── deployment.yaml      # Monitor Python + PVC postmortems-pvc (1 Gi)
    ├── frontend.yaml        # Deployment + Service + Ingress do dashboard
    └── postgres.yaml        # Postgres 16 opcional: Secret + PVC 5Gi + Deployment + Service
```

---

## CI/CD com GitHub Actions

O repositório inclui workflow de CI/CD que:

1. Faz build das duas imagens Docker
2. Publica no Docker Hub com tag `latest` e versão automática `v{MAJOR.MINOR}.{run_number}`
3. Imagens publicadas em: `vinnioliver66/k8s-monitor` e `vinnioliver66/k8s-monitor-ui`

Para usar com seu próprio repositório, configure os secrets no GitHub:
- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

---

## Manutenção

### Atualizar a aplicação

```bash
# Rebuild e push
docker build -t vinnioliver66/k8s-monitor:latest monitor/
docker push vinnioliver66/k8s-monitor:latest

# Forçar rollout no cluster
kubectl rollout restart deployment/k8s-monitor -n k8s-monitor
kubectl rollout status deployment/k8s-monitor -n k8s-monitor
```

### Limpar o PVC (remover todos os incidentes)

```bash
# Acessa um pod temporário com o PVC montado
kubectl run limpar-pvc --rm -it \
  --image=busybox \
  --restart=Never \
  --overrides='{"spec":{"volumes":[{"name":"pv","persistentVolumeClaim":{"claimName":"postmortems-pvc"}}],"containers":[{"name":"limpar-pvc","image":"busybox","command":["sh"],"stdin":true,"tty":true,"volumeMounts":[{"mountPath":"/data","name":"pv"}]}]}}' \
  -n k8s-monitor

# Dentro do pod:
rm -rf /data/*.json /data/*.md /data/.alert_state.json
```

### Remover tudo do cluster

```bash
kubectl delete -f k8s/frontend.yaml
kubectl delete -f k8s/deployment.yaml
kubectl delete -f k8s/postgres.yaml        # se foi aplicado
kubectl delete -f k8s/rbac.yaml
kubectl delete secret k8s-monitor-secrets -n k8s-monitor
```

> O PVC `postmortems-pvc` **não** é deletado automaticamente para preservar os postmortems. Para deletar também:
> ```bash
> kubectl delete pvc postmortems-pvc -n k8s-monitor
> kubectl delete pvc postgres-k8s-monitor-pvc -n k8s-monitor
> ```
