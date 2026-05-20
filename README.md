# K8s Monitor

Aplicação de monitoramento de logs para Kubernetes que detecta erros automaticamente, analisa a causa raiz usando inteligência artificial (Claude da Anthropic), envia notificações no Discord com solução proposta, gera arquivos de postmortem e exibe tudo em um dashboard web em tempo real.

---

## Sumário

- [Como funciona](#como-funciona)
- [Arquitetura](#arquitetura)
- [Pré-requisitos](#pré-requisitos)
- [Configuração inicial](#configuração-inicial)
  - [1. Chave da API Anthropic](#1-chave-da-api-anthropic)
  - [2. Webhook do Discord](#2-webhook-do-discord)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Rodando localmente (desenvolvimento)](#rodando-localmente-desenvolvimento)
- [Build das imagens Docker](#build-das-imagens-docker)
- [Enviando imagens para o Docker Hub](#enviando-imagens-para-o-docker-hub)
- [Deploy no Kubernetes](#deploy-no-kubernetes)
- [Acessando o dashboard](#acessando-o-dashboard)
- [Estrutura de arquivos](#estrutura-de-arquivos)

---

## Como funciona

```
Pods K8s → Logs → Detector de erros → Claude AI → Discord + Postmortem .md → Dashboard web
```

1. **Monitor Python** conecta via API do Kubernetes e abre um stream de logs para cada pod em execução (uma thread por pod).
2. **Detector de erros** analisa cada linha usando expressões regulares. Erros iguais dentro de 5 minutos são ignorados para não gerar spam.
3. **Analisador IA** envia o erro e o contexto de log para o Claude (Anthropic) e recebe em JSON: causa raiz, severidade, ações imediatas e medidas de prevenção.
4. **Notificador Discord** envia um embed colorido por severidade com toda a análise e anexa o arquivo `.md` de postmortem.
5. **Gerador de postmortem** salva dois arquivos por incidente: `postmortem_*.md` (relatório legível) e `incident_*.json` (dados estruturados para o dashboard).
6. **API Node.js** lê os arquivos JSON, expõe endpoints REST e envia atualizações em tempo real via Server-Sent Events (SSE).
7. **Dashboard React** exibe os incidentes, permite filtrar por severidade/namespace/status e marcar como resolvido.

### Severidades detectadas

| Severidade | Cor | Exemplos de padrão |
|------------|-----|--------------------|
| Critical   | 🔴  | `FATAL`, `panic:`, `OOMKilled`, `CrashLoopBackOff` |
| High       | 🟠  | `ERROR`, `EXCEPTION`, `java.lang.*Exception` |
| Medium     | 🟡  | `CRITICAL`, `SEVERE`, `ImagePullBackOff` |
| Low        | 🟢  | Outros padrões configurados |

---

## Arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                    Namespace: monitoring                 │
│                                                         │
│  ┌──────────────────┐        ┌────────────────────────┐ │
│  │   k8s-monitor    │        │   k8s-monitor-ui       │ │
│  │  (Python)        │        │   (Node.js + React)    │ │
│  │                  │        │                        │ │
│  │ monitor.py       │        │ api/server.js          │ │
│  │ k8s_watcher.py   │        │ frontend/dist/         │ │
│  │ error_detector   │        │                        │ │
│  │ ai_analyzer      │        │  Port 3001             │ │
│  │ discord_notifier │        └──────────┬─────────────┘ │
│  │ postmortem_gen   │                   │               │
│  └────────┬─────────┘        ┌──────────▼─────────────┐ │
│           │                  │   PersistentVolumeClaim │ │
│           └─────────────────►│   /postmortems          │ │
│                              │   incident_*.json       │ │
│                              │   postmortem_*.md       │ │
│                              └────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
    Discord Webhook           Browser (dashboard)
```

---

## Pré-requisitos

| Ferramenta | Versão mínima | Para quê |
|------------|---------------|----------|
| Docker | 24+ | Build das imagens |
| kubectl | 1.28+ | Deploy no cluster |
| Python | 3.11+ | Rodar o monitor localmente |
| Node.js | 20+ | Rodar o dashboard localmente |
| Conta Docker Hub | — | Armazenar as imagens |
| Chave API Anthropic | — | Análise de IA |
| Webhook Discord | — | Notificações |

---

## Configuração inicial

### 1. Chave da API Anthropic

1. Acesse [console.anthropic.com](https://console.anthropic.com)
2. Vá em **API Keys** → **Create Key**
3. Copie a chave no formato `sk-ant-...`
4. Guarde — ela só é exibida uma vez

### 2. Webhook do Discord

1. Abra o Discord e vá até o **servidor** onde quer receber as notificações
2. Clique com o botão direito no **canal** desejado → **Edit Channel**
3. Vá em **Integrations** → **Webhooks** → **New Webhook**
4. Dê um nome (ex: `K8s Monitor`), escolha o canal e clique em **Copy Webhook URL**
5. O URL terá o formato: `https://discord.com/api/webhooks/ID/TOKEN`

> **Segurança:** nunca exponha o URL do webhook publicamente. Trate-o como uma senha.

---

## Variáveis de ambiente

| Variável | Obrigatório | Padrão | Descrição |
|----------|-------------|--------|-----------|
| `ANTHROPIC_API_KEY` | ✅ | — | Chave da API Anthropic |
| `DISCORD_WEBHOOK_URL` | ✅ | — | URL do webhook do Discord |
| `NAMESPACES` | — | *(todos)* | Namespaces a monitorar, separados por vírgula. Ex: `default,production` |
| `CLAUDE_MODEL` | — | `claude-sonnet-4-6` | Modelo Claude a usar |
| `ERROR_COOLDOWN_SECONDS` | — | `300` | Tempo mínimo (em segundos) entre alertas do mesmo erro |
| `CONTEXT_LINES` | — | `10` | Linhas de log capturadas antes de cada erro |
| `LOG_LINES_TAIL` | — | `100` | Linhas lidas ao conectar num pod pela primeira vez |
| `POSTMORTEM_DIR` | — | `./postmortems` | Diretório onde os arquivos são salvos |
| `LOG_LEVEL` | — | `INFO` | Nível de log: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Copie o arquivo de exemplo para começar:

```bash
cp .env.example .env
# edite o .env com suas chaves
```

---

## Rodando localmente (desenvolvimento)

### Monitor Python

```bash
# Instalar dependências
pip install -r requirements.txt

# Configurar variáveis (edite o .env antes)
export $(cat .env | xargs)

# O monitor usará automaticamente o kubeconfig local (~/.kube/config)
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

O Vite está configurado para fazer proxy de `/api` para `localhost:3001`, então o frontend e a API funcionam juntos automaticamente em desenvolvimento.

---

## Build das imagens Docker

O projeto tem **duas imagens**:

| Imagem | Diretório | Descrição |
|--------|-----------|-----------|
| `k8s-monitor` | raiz `/` | Monitor Python |
| `k8s-monitor-ui` | `frontend/` | Dashboard React + API Node.js |

### Substituindo o nome do repositório

Nos comandos abaixo, substitua `SEU_USUARIO` pelo seu usuário do Docker Hub.

### Imagem do monitor Python

```bash
# Construir
docker build -t SEU_USUARIO/k8s-monitor:latest .

# Testar localmente (opcional)
docker run --rm \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \
  -v ~/.kube:/root/.kube:ro \
  SEU_USUARIO/k8s-monitor:latest
```

### Imagem do dashboard (React + API)

A imagem usa build multi-stage: primeiro compila o React com Vite, depois copia os arquivos estáticos para a imagem Node.js final.

```bash
# O Dockerfile está dentro de frontend/, mas precisa do contexto da raiz
docker build -f frontend/Dockerfile -t SEU_USUARIO/k8s-monitor-ui:latest .
```

> **Por que o contexto é a raiz?** O Dockerfile copia `frontend/` e `api/` — ambos precisam estar acessíveis durante o build.

### Build com versão específica (recomendado para produção)

```bash
VERSION=1.0.0

docker build -t SEU_USUARIO/k8s-monitor:${VERSION} -t SEU_USUARIO/k8s-monitor:latest .
docker build -f frontend/Dockerfile \
  -t SEU_USUARIO/k8s-monitor-ui:${VERSION} \
  -t SEU_USUARIO/k8s-monitor-ui:latest .
```

---

## Enviando imagens para o Docker Hub

### Login

```bash
docker login
# informe seu usuário e senha/token do Docker Hub
```

> **Dica de segurança:** prefira usar um **Access Token** no lugar da senha.
> Docker Hub → Account Settings → Security → New Access Token.

### Push das imagens

```bash
# Monitor Python
docker push SEU_USUARIO/k8s-monitor:latest
docker push SEU_USUARIO/k8s-monitor:1.0.0   # se usou versão

# Dashboard
docker push SEU_USUARIO/k8s-monitor-ui:latest
docker push SEU_USUARIO/k8s-monitor-ui:1.0.0
```

### Build + push em um único comando (alternativa)

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --push \
  -t SEU_USUARIO/k8s-monitor:latest .

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --push \
  -f frontend/Dockerfile \
  -t SEU_USUARIO/k8s-monitor-ui:latest .
```

> O flag `--platform linux/amd64,linux/arm64` gera uma imagem multi-arquitetura (útil se seu cluster usa nós ARM, como AWS Graviton).

---

## Deploy no Kubernetes

### 1. Atualizar o nome da imagem nos manifests

Edite `k8s/deployment.yaml` e `k8s/frontend.yaml`, substituindo `your-registry` pelo seu usuário Docker Hub:

```yaml
# k8s/deployment.yaml
image: SEU_USUARIO/k8s-monitor:latest

# k8s/frontend.yaml
image: SEU_USUARIO/k8s-monitor-ui:latest
```

### 2. Criar o namespace e o RBAC

```bash
kubectl apply -f k8s/rbac.yaml
```

Isso cria:
- Namespace `monitoring`
- ServiceAccount `k8s-monitor`
- ClusterRole com permissão de leitura apenas em `pods` e `pods/log`
- ClusterRoleBinding vinculando o ServiceAccount ao ClusterRole

### 3. Criar o Secret com as credenciais

```bash
kubectl create secret generic k8s-monitor-secrets \
  --namespace monitoring \
  --from-literal=anthropic-api-key=sk-ant-... \
  --from-literal=discord-webhook-url=https://discord.com/api/webhooks/...
```

Verificar se foi criado:

```bash
kubectl get secret k8s-monitor-secrets -n monitoring
```

### 4. Deploy do monitor Python

```bash
kubectl apply -f k8s/deployment.yaml
```

Verificar se está rodando:

```bash
kubectl get pods -n monitoring
kubectl logs -f deployment/k8s-monitor -n monitoring
```

### 5. Deploy do dashboard

```bash
kubectl apply -f k8s/frontend.yaml
```

### 6. Verificar tudo

```bash
kubectl get all -n monitoring
```

Saída esperada:

```
NAME                                   READY   STATUS    RESTARTS
pod/k8s-monitor-xxxx                   1/1     Running   0
pod/k8s-monitor-ui-xxxx                1/1     Running   0

NAME                       TYPE        CLUSTER-IP    PORT(S)
service/k8s-monitor-ui     ClusterIP   10.x.x.x      80/TCP

NAME                              READY   UP-TO-DATE
deployment.apps/k8s-monitor       1/1     1
deployment.apps/k8s-monitor-ui    1/1     1
```

---

## Acessando o dashboard

### Opção A — Port-forward (acesso rápido sem expor externamente)

```bash
kubectl port-forward service/k8s-monitor-ui 8080:80 -n monitoring
```

Abra o browser em `http://localhost:8080`.

### Opção B — Ingress (acesso externo permanente)

Descomente e edite o bloco de `Ingress` no arquivo `k8s/frontend.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: k8s-monitor-ui
  namespace: monitoring
spec:
  rules:
    - host: monitor.seu-dominio.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: k8s-monitor-ui
                port:
                  number: 80
```

```bash
kubectl apply -f k8s/frontend.yaml
```

### O que você verá no dashboard

| Elemento | Descrição |
|----------|-----------|
| Barra de stats | Total de incidentes, abertos, critical, high, medium |
| Filtros | Por severidade, status (aberto/resolvido), namespace e busca por texto |
| Cards de incidente | Pod, namespace, severidade, horário, erro detectado |
| Análise expandida | Causa raiz, impacto estimado, ações imediatas, medidas de prevenção |
| Log context | Linhas anteriores ao erro (expansível) |
| Ações | Marcar como resolvido / Reabrir |
| Tempo real | Novos incidentes aparecem automaticamente via SSE sem precisar recarregar |

---

## Estrutura de arquivos

```
monitoramento/
│
├── monitor.py               # Ponto de entrada — orquestra todos os componentes
├── config.py                # Todas as configurações via variáveis de ambiente
├── k8s_watcher.py           # Conexão com Kubernetes — watch de pods e stream de logs
├── error_detector.py        # Detecção por regex + deduplicação por hash MD5
├── ai_analyzer.py           # Integração com Claude API (com prompt caching)
├── discord_notifier.py      # Notificações Discord com embed + upload do .md
├── postmortem_generator.py  # Gera postmortem_*.md e incident_*.json por incidente
│
├── requirements.txt         # Dependências Python
├── Dockerfile               # Imagem do monitor Python
├── .env.example             # Exemplo de variáveis de ambiente
│
├── api/
│   ├── package.json
│   └── server.js            # Express REST API + SSE + file watcher (chokidar)
│
├── frontend/
│   ├── Dockerfile           # Build multi-stage: React → Node.js
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── App.jsx           # Estado global, SSE, filtros
│       ├── App.css           # Dark theme
│       ├── api.js            # Funções fetch para a API
│       └── components/
│           ├── StatsBar.jsx  # Contadores por severidade
│           ├── Filters.jsx   # Barra de filtros
│           └── IncidentCard.jsx  # Card expansível com análise da IA
│
└── k8s/
    ├── rbac.yaml            # Namespace + ServiceAccount + ClusterRole
    ├── deployment.yaml      # Deployment do monitor Python + PVC
    └── frontend.yaml        # Deployment + Service do dashboard
```

---

## Atualizando a aplicação

```bash
# 1. Fazer as alterações no código

# 2. Rebuild e push
docker build -t SEU_USUARIO/k8s-monitor:latest .
docker push SEU_USUARIO/k8s-monitor:latest

# 3. Forçar o Kubernetes a puxar a nova imagem
kubectl rollout restart deployment/k8s-monitor -n monitoring

# Acompanhar o rollout
kubectl rollout status deployment/k8s-monitor -n monitoring
```

## Removendo tudo do cluster

```bash
kubectl delete -f k8s/frontend.yaml
kubectl delete -f k8s/deployment.yaml
kubectl delete -f k8s/rbac.yaml
kubectl delete secret k8s-monitor-secrets -n monitoring
```

> O PersistentVolumeClaim (`postmortems-pvc`) **não** é deletado automaticamente para preservar os postmortems. Para deletar também: `kubectl delete pvc postmortems-pvc -n monitoring`.
