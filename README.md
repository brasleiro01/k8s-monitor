# K8s Monitor

Aplicação de monitoramento de logs para Kubernetes que detecta erros automaticamente, analisa a causa raiz com inteligência artificial (Google Gemini), envia notificações no Discord e exibe tudo em um dashboard web em tempo real.

---

## Sumário

- [Como funciona](#como-funciona)
- [Arquitetura](#arquitetura)
- [Funcionalidades do dashboard](#funcionalidades-do-dashboard)
- [Pré-requisitos](#pré-requisitos)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Configuração inicial](#configuração-inicial)
- [Rodando localmente (desenvolvimento)](#rodando-localmente-desenvolvimento)
- [Build da imagem Docker](#build-da-imagem-docker)
- [Deploy no Kubernetes](#deploy-no-kubernetes)
- [Acessando o dashboard](#acessando-o-dashboard)
- [Banco de dados Postgres](#banco-de-dados-postgres)
- [Login com Google OAuth (opcional)](#login-com-google-oauth-opcional)
- [Estrutura de arquivos](#estrutura-de-arquivos)
- [CI/CD com GitHub Actions](#cicd-com-github-actions)
- [Manutenção](#manutenção)

---

## Como funciona

```
Pods K8s → Logs → Detector de erros → Gemini AI → Discord + Postmortem → Dashboard web
```

1. **K8sLogWatcher** conecta via API do Kubernetes e abre um stream de logs para cada pod em execução (uma thread por pod).
2. **Detector de erros** analisa cada linha com expressões regulares. O mesmo erro no mesmo pod é suprimido por até **1 hora** (cooldown) e no máximo **5 vezes por dia** — contadores persistem no Postgres e sobrevivem a reinicializações.
3. **Analisador de IA** tenta três camadas em cascata:
   - **Gemini** (`gemini-2.0-flash`) — análise completa com causa raiz, severidade e ações corretivas
   - **rule_analyzer** — fallback baseado em regras (OOM, CrashLoop, TLS, banco, etc.)
   - **last_resort** — resposta genérica garantindo sempre um resultado
4. **Notificador Discord** envia um embed colorido por severidade com toda a análise.
5. **Gerador de postmortem** persiste o incidente no Postgres (`incidents`). O relatório `.md` é gerado na resolução (não na detecção), capturando a solução real.
6. **FastAPI** expõe a REST API, serve o frontend React como arquivos estáticos e envia atualizações em tempo real via Server-Sent Events (SSE).
7. **Dashboard React** exibe os incidentes agrupados por namespace → pod, com filtros, temas e conexão SSE ao vivo.

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
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │               k8s-monitor  (imagem única)                   │ │
│  │                                                             │ │
│  │  Python FastAPI  (porta 8000)                               │ │
│  │  ├── k8s_watcher.py    → watch pods + stream logs           │ │
│  │  ├── monitor.py        → detecção + rate limit              │ │
│  │  ├── ai_analyzer.py    → Gemini 2.0 Flash                   │ │
│  │  ├── discord_notifier  → webhook Discord                    │ │
│  │  ├── postmortem_gen    → gera .md na resolução              │ │
│  │  ├── db.py             → Postgres (psycopg2)                │ │
│  │  └── public/           → React SPA (build estático)         │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│  ┌───────────────────────────▼─────────────────────────────────┐ │
│  │  postgres-k8s-monitor  (postgres:16-alpine + PVC 5Gi)       │ │
│  │  tabela: incidents                                           │ │
│  └─────────────────────────────────────────────────────────────┘ │
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
| **Login com Google** | Opcional via OAuth 2.0 (requer `GOOGLE_CLIENT_ID`) |
| **Tema claro / escuro** | Toggle por usuário, persiste entre sessões |
| **Agrupamento por namespace** | Incidentes organizados em namespace (externo) → pod (interno) |
| **Deduplicação** | Mesmo erro no mesmo pod agrupa em 1 card com contador ×N de ocorrências |
| **Filtros** | Por severidade, status (aberto/resolvido), namespace, texto livre e período de datas |
| **Tempo real** | Novos incidentes aparecem automaticamente via SSE sem recarregar |
| **Análise da IA** | Causa raiz, impacto estimado, ações imediatas e prevenção |
| **Contexto de log** | Linhas anteriores ao erro (expansível) |
| **Postmortem inline** | Botão "Visualizar" abre o `.md` renderizado em modal |
| **Histórico de resoluções** | Drawer lateral lista todos os postmortems com opção de visualizar e baixar |
| **Indicador de conexão** | Badge "Ao vivo" / "Conectando..." e ponto vermelho quando há critical aberto |

---

## Pré-requisitos

| Ferramenta | Versão mínima | Para quê |
|------------|---------------|----------|
| Docker | 24+ | Build da imagem |
| kubectl | 1.28+ | Deploy no cluster |
| Python | 3.12+ | Rodar o backend localmente |
| Node.js | 20+ | Rodar o frontend localmente |
| Conta Docker Hub | — | Armazenar a imagem |
| Chave API Google Gemini | — | Análise de IA |
| Webhook Discord | — | Notificações |

---

## Variáveis de ambiente

| Variável | Obrigatório | Padrão | Descrição |
|----------|-------------|--------|-----------|
| `GEMINI_API_KEY` | ✅ | — | Chave da API Google Gemini (Google AI Studio) |
| `DISCORD_WEBHOOK_URL` | ✅ | — | URL do webhook do Discord |
| `DATABASE_URL` | ✅ | — | URL do Postgres. Caracteres especiais na senha devem ser percent-encoded (`@` → `%40`) |
| `NAMESPACES` | — | *(todos)* | Namespaces a monitorar, separados por vírgula. Ex: `default,production` |
| `GEMINI_MODEL` | — | `gemini-2.0-flash` | Modelo Gemini a usar |
| `ERROR_COOLDOWN_SECONDS` | — | `3600` | Cooldown (segundos) entre alertas do mesmo erro no mesmo pod |
| `MAX_DAILY_ALERTS` | — | `5` | Máximo de alertas por erro por dia |
| `CONTEXT_LINES` | — | `10` | Linhas de log capturadas antes de cada erro |
| `EXCLUDE_LOG_PATTERNS` | — | *(vazio)* | Padrões a ignorar, separados por `\|\|`. Ex: `healthcheck\|\|readiness` |
| `GOOGLE_CLIENT_ID` | — | *(vazio)* | Client ID do Google OAuth. Se vazio, botão Google não aparece |
| `DB_SSL` | — | `0` | `1` para Aurora/RDS com SSL, `0` para Postgres local/interno |
| `LOG_LEVEL` | — | `INFO` | Nível de log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `PORT` | — | `8000` | Porta do servidor uvicorn |

---

## Configuração inicial

### 1. Chave da API Google Gemini

1. Acesse [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Clique em **Create API Key**
3. Copie a chave no formato `AIza...`

### 2. Webhook do Discord

1. No Discord, abra o canal desejado → **Edit Channel** → **Integrations** → **Webhooks** → **New Webhook**
2. Dê um nome (ex: `K8s Monitor`) e clique em **Copy Webhook URL**

> **Segurança:** nunca exponha credenciais em código ou commits. Use Kubernetes Secrets.

### 3. Criar o Secret de credenciais

O arquivo `k8s/04-secrets.yaml` (em `.gitignore`) centraliza todas as credenciais. Exemplo de conteúdo:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: k8s-monitor-secrets
  namespace: k8s-monitor
type: Opaque
stringData:
  gemini-api-key: "AIza..."
  discord-webhook-url: "https://discord.com/api/webhooks/..."
  DATABASE_URL: "postgresql://k8smonitor:SENHA@postgres-k8s-monitor:5432/k8smonitor"
  POSTGRES_DB: "k8smonitor"
  POSTGRES_USER: "k8smonitor"
  POSTGRES_PASSWORD: "SENHA"
  google-client-id: ""   # deixe vazio para desabilitar login Google
```

> **Atenção com senhas especiais:** se a senha do banco contiver `@`, encode como `%40` na `DATABASE_URL`. Ex: `senha@123` → `senha%40123`.

---

## Rodando localmente (desenvolvimento)

### Backend Python

```bash
cd app
pip install -r requirements.txt

export GEMINI_API_KEY=AIza...
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
export DATABASE_URL=postgresql://user:pass@localhost:5432/k8smonitor

uvicorn main:app --reload --port 8000
# API disponível em http://localhost:8000
```

### Frontend React

```bash
cd frontend
npm install
npm run dev
# Dashboard disponível em http://localhost:5173
```

O Vite faz proxy de `/api` para `localhost:8000` automaticamente (configurado em `vite.config.js`).

Para habilitar o login com Google em desenvolvimento, crie `frontend/.env.local`:

```
VITE_GOOGLE_CLIENT_ID=seu-client-id.apps.googleusercontent.com
```

---

## Build da imagem Docker

O projeto usa **uma única imagem** com build multi-stage: o Node.js compila o React, e o Python serve o resultado como arquivos estáticos via FastAPI.

```bash
docker build -t vinnioliver66/k8s-monitor:latest .
docker push vinnioliver66/k8s-monitor:latest
```

O CI/CD faz isso automaticamente a cada push na branch `main`.

---

## Deploy no Kubernetes

### Ordem de aplicação

```bash
# 1. Namespace + RBAC + token estático de ServiceAccount
kubectl apply -f k8s/01-rbac.yaml

# 2. Postgres interno no cluster
# Edite k8s/04-secrets.yaml com suas credenciais antes!
kubectl apply -f k8s/04-secrets.yaml
kubectl apply -f k8s/00-postgres.yaml

# 3. Aguardar Postgres ficar pronto
kubectl rollout status deployment/postgres-k8s-monitor -n k8s-monitor

# 4. Deploy da aplicação
kubectl apply -f k8s/02-monitor.yaml
```

### Verificar se está tudo rodando

```bash
kubectl get all -n k8s-monitor
```

Saída esperada:

```
NAME                                        READY   STATUS    RESTARTS
pod/k8s-monitor-xxxx                        1/1     Running   0
pod/postgres-k8s-monitor-xxxx               1/1     Running   0

NAME                           TYPE        CLUSTER-IP    PORT(S)
service/k8s-monitor            ClusterIP   10.x.x.x      80/TCP
service/postgres-k8s-monitor   ClusterIP   10.x.x.x      5432/TCP

NAME                                    READY   UP-TO-DATE
deployment.apps/k8s-monitor             1/1     1
deployment.apps/postgres-k8s-monitor    1/1     1
```

### Com ArgoCD (GitOps)

Se você usa ArgoCD apontando para a pasta `k8s/`, garanta que o `04-secrets.yaml` **não** esteja no repositório (está em `.gitignore`). Crie o Secret manualmente no cluster antes de sincronizar:

```bash
kubectl apply -f k8s/04-secrets.yaml   # apenas uma vez, fora do GitOps
```

O ArgoCD gerencia os demais manifestos (`01-rbac.yaml`, `00-postgres.yaml`, `02-monitor.yaml`) e sincroniza automaticamente quando o CI atualiza a tag da imagem em `02-monitor.yaml`.

---

## Acessando o dashboard

### Port-forward (acesso rápido)

```bash
kubectl port-forward service/k8s-monitor 8080:80 -n k8s-monitor
```

Abra `http://localhost:8080` no browser.

### Ingress (acesso externo permanente)

O `k8s/02-monitor.yaml` já inclui um Ingress configurado para `k8s-monitor.mvps.com.br`. Edite o host para o seu domínio antes de aplicar.

---

## Banco de dados Postgres

### Postgres interno ao cluster (`k8s/00-postgres.yaml`)

O manifesto cria:
- **PVC** de 5 Gi para dados persistentes
- **ConfigMap** com `init.sql` (extensões `pg_trgm` e `btree_gin`)
- **Deployment** com `postgres:16-alpine`
- **Service** ClusterIP na porta 5432

A tabela `incidents` é criada automaticamente pelo Python na primeira conexão.

### Postgres externo (Aurora / RDS)

Altere a `DATABASE_URL` no `04-secrets.yaml` para a string de conexão externa e habilite SSL:

```yaml
DATABASE_URL: "postgresql://user:pass@cluster.region.rds.amazonaws.com:5432/k8smonitor"
```

No `k8s/02-monitor.yaml`, altere a variável de ambiente:

```yaml
- name: DB_SSL
  value: "1"
```

---

## Login com Google OAuth (opcional)

O botão de login com Google aparece automaticamente quando `GOOGLE_CLIENT_ID` está configurado no Secret. É completamente opcional — o login por nome está sempre disponível.

### Configurar o Client ID

1. Acesse [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials
2. Crie um **OAuth 2.0 Client ID** do tipo "Web application"
3. Adicione em "Authorized JavaScript origins" o domínio de produção (ex: `https://k8s-monitor.mvps.com.br`)
4. Copie o Client ID no formato `*.apps.googleusercontent.com`
5. Adicione ao `k8s/04-secrets.yaml` na chave `google-client-id`

> O `GOOGLE_CLIENT_ID` é lido em **runtime** pelo FastAPI e repassado ao frontend via `/api/config`. Não é necessário recompilar a imagem.

---

## Estrutura de arquivos

```
monitoramento/
│
├── Dockerfile               # Multi-stage: node:20-alpine (React) → python:3.12-slim
├── VERSION                  # Versão base para o CI (ex: 1.0)
│
├── app/                     # Backend Python + assets React servidos pelo FastAPI
│   ├── main.py              # FastAPI: lifespan, rotas REST, SSE, SPA fallback
│   ├── config.py            # Todas as variáveis de ambiente com defaults
│   ├── db.py                # Postgres: ensure_schema, upsert_incident, load_incidents...
│   ├── k8s_watcher.py       # Watch de pods e stream de logs via kubernetes Python client
│   ├── monitor.py           # Orquestra detecção, análise e notificação
│   ├── error_detector.py    # Regex de erros + deduplicação por hash + rate limiting
│   ├── rule_analyzer.py     # Fallback de análise por regras (sem IA)
│   ├── ai_analyzer.py       # Integração google-genai → Gemini 2.0 Flash
│   ├── discord_notifier.py  # Embed Discord colorido por severidade
│   ├── postmortem_generator.py  # Gera postmortem .md e persiste no DB
│   ├── requirements.txt     # fastapi, uvicorn, google-genai, kubernetes, psycopg2-binary, requests
│   └── public/              # Gerado no build: dist do React copiado pelo Dockerfile
│
├── frontend/
│   ├── package.json         # React, Vite, @react-oauth/google, marked
│   ├── vite.config.js       # Proxy /api → localhost:8000 (dev)
│   ├── index.html
│   └── src/
│       ├── App.jsx           # Estado global, auth, tema, SSE, filtros, agrupamento
│       ├── App.css           # Tema claro + escuro (html[data-theme="dark"])
│       ├── api.js            # Funções fetch para a API
│       └── components/
│           ├── LoginScreen.jsx
│           ├── StatsBar.jsx
│           ├── Filters.jsx
│           ├── NamespaceGroup.jsx
│           ├── PodGroup.jsx
│           ├── IncidentCard.jsx
│           ├── PostmortemViewer.jsx
│           └── PostmortemsDrawer.jsx
│
└── k8s/
    ├── 00-postgres.yaml     # PVC 5Gi + Deployment postgres:16 + Service ClusterIP
    ├── 01-rbac.yaml         # Namespace + ServiceAccount + ClusterRole + Secret de token estático
    ├── 02-monitor.yaml      # Service + Ingress + Deployment da aplicação
    └── 04-secrets.yaml      # Credenciais reais — em .gitignore, NUNCA commitar
```

---

## CI/CD com GitHub Actions

O workflow `.github/workflows/docker-build-push.yml` executa a cada push na branch `main`:

1. **Gera a tag** no formato `v{VERSION}.{run_number}` (ex: `v1.0.25`)
2. **Build e push** da imagem `vinnioliver66/k8s-monitor:{tag}` e `:latest` para o Docker Hub
3. **Atualiza** a tag da imagem em `k8s/02-monitor.yaml` e faz commit automático no repositório
4. Se você usa ArgoCD, ele detecta a mudança e faz o deploy automaticamente

Configure os secrets no repositório GitHub:

| Secret | Descrição |
|--------|-----------|
| `DOCKERHUB_USERNAME` | Usuário do Docker Hub |
| `DOCKERHUB_TOKEN` | Access token do Docker Hub |

---

## Manutenção

### Ver logs da aplicação

```bash
kubectl logs -f deployment/k8s-monitor -n k8s-monitor
```

### Forçar rollout após atualização manual

```bash
kubectl rollout restart deployment/k8s-monitor -n k8s-monitor
kubectl rollout status deployment/k8s-monitor -n k8s-monitor
```

### Remover tudo do cluster

```bash
kubectl delete -f k8s/02-monitor.yaml
kubectl delete -f k8s/00-postgres.yaml
kubectl delete -f k8s/01-rbac.yaml
kubectl delete secret k8s-monitor-secrets -n k8s-monitor
```

> O PVC `postgres-k8s-monitor-pvc` **não** é deletado automaticamente para preservar os dados. Para remover também:
> ```bash
> kubectl delete pvc postgres-k8s-monitor-pvc -n k8s-monitor
> ```
