import re
import logging

logger = logging.getLogger(__name__)

# Cada regra: pattern de match, extractor opcional, e função que monta a análise
_RULES = [
    # ------------------------------------------------------------------ #
    # Coletor Prometheus desabilitado (baixo impacto — feature disabled) #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(
            r"disabling\s+\w+\s+(?:device\s+)?properties|"
            r"failed to open directory.*disabling|"
            r"disabling\s+collector|"
            r"collector=\S+\s+msg=\"Failed to open",
            re.I
        ),
        "extract": re.compile(r"path=([/\w\-\.]+)", re.I),
        "severity": "low",
        "build": lambda path, pod, ns: {
            "root_cause": (
                f"node_exporter não consegue acessar '{path or '/run/udev/data'}' dentro do contêiner — "
                "o coletor foi desabilitado automaticamente por falta de permissão de acesso ao socket do host"
            ),
            "severity": "low",
            "immediate_action": [
                f"Suprimir o coletor via arg: adicionar --no-collector.udev no deployment do node_exporter em {ns}",
                f"OU montar o socket do host: adicionar hostPath volume '/run/udev' no pod {pod}",
                f"Verificar quais coletores estão ativos: kubectl logs {pod} -n {ns} | grep 'collector='",
            ],
            "prevention": [
                "Usar --collector.disable-defaults e habilitar explicitamente apenas os coletores necessários",
                f"Adicionar toleração no deployment para garantir que o node_exporter rode no nó correto com acesso ao host",
            ],
            "estimated_impact": (
                f"Apenas métricas relacionadas a udev/disco indisponíveis — "
                "monitoramento principal do node_exporter não afetado"
            ),
            "summary": (
                f"node_exporter desabilitou coletor por falta de acesso a '{path or '/run/udev/data'}' — "
                "baixo impacto; suprimir o coletor ou montar o hostPath"
            ),
        },
    },
    # ------------------------------------------------------------------ #
    # Arquivo / diretório não encontrado (inclui "failed to open")       #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"no such file or directory|not found|does not exist|failed to open|cannot open|unable to open", re.I),
        "extract": re.compile(r'(?:stat|lstat|open|read|path=)["\s]+([/][\w/\-\.]+)', re.I),
        "severity": "high",
        "build": lambda path, pod, ns: {
            "root_cause": f"O caminho '{path}' não existe no contêiner — volume não montado, PVC ausente ou configuração de provisionamento incorreta",
            "severity": "high",
            "immediate_action": [
                f"Verificar se o volumeMount está configurado: kubectl describe pod {pod} -n {ns} | grep -A10 'Mounts:'",
                f"Criar o diretório temporariamente: kubectl exec -n {ns} {pod} -- mkdir -p {path}",
                f"Listar PVCs do namespace: kubectl get pvc -n {ns}",
            ],
            "prevention": [
                f"Adicionar um initContainer que cria '{path}' antes da aplicação iniciar",
                "Garantir que o PVC ou ConfigMap que provê o diretório existe antes do deploy",
                "Usar readinessProbe para bloquear tráfego até o volume estar disponível",
            ],
            "estimated_impact": f"Funcionalidade que depende de '{path}' indisponível — configurações ou dados não carregados",
            "summary": f"Diretório '{path}' ausente no pod {pod} — volume provavelmente não montado ou PVC inexistente",
        },
    },
    # ------------------------------------------------------------------ #
    # OOM / memória                                                        #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"OOMKilled|out of memory|cannot allocate memory|malloc.*fail", re.I),
        "extract": None,
        "severity": "critical",
        "build": lambda path, pod, ns: {
            "root_cause": f"Pod {pod} encerrado pelo kernel por exceder o limite de memória (OOMKilled)",
            "severity": "critical",
            "immediate_action": [
                f"Ver consumo atual: kubectl top pod -n {ns} | grep {pod}",
                f"Aumentar o limite de memória: kubectl patch deployment $(kubectl get pod {pod} -n {ns} -o jsonpath='{{.metadata.labels.app}}') -n {ns} --type=json -p '[{{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/memory\",\"value\":\"512Mi\"}}]'",
                f"Reiniciar o pod após o ajuste: kubectl rollout restart deployment -n {ns} -l app=$(kubectl get pod {pod} -n {ns} -o jsonpath='{{.metadata.labels.app}}')",
            ],
            "prevention": [
                "Configurar HPA para escalar horizontalmente antes de atingir o limite de memória",
                "Adicionar alerta no Prometheus quando uso de memória ultrapassar 80% do limite",
                "Revisar leaks de memória na aplicação com profiler",
            ],
            "estimated_impact": "Pod reiniciando ciclicamente — downtime intermitente para usuários finais",
            "summary": f"Pod {pod} encerrado por OOMKilled — aumentar limits.memory no deployment",
        },
    },
    # ------------------------------------------------------------------ #
    # CrashLoopBackOff                                                    #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"CrashLoopBackOff|back-off restarting", re.I),
        "extract": None,
        "severity": "critical",
        "build": lambda path, pod, ns: {
            "root_cause": f"Pod {pod} em CrashLoopBackOff — a aplicação está encerrando logo após iniciar",
            "severity": "critical",
            "immediate_action": [
                f"Ver logs da execução anterior: kubectl logs {pod} -n {ns} --previous",
                f"Descrever o pod para ver o exit code: kubectl describe pod {pod} -n {ns} | grep -A5 'Last State'",
                f"Verificar variáveis de ambiente e secrets: kubectl get pod {pod} -n {ns} -o yaml | grep -A20 env",
            ],
            "prevention": [
                "Adicionar startupProbe para dar tempo à aplicação inicializar antes dos health checks",
                "Configurar backoffLimit adequado no deployment para evitar restart em loop",
            ],
            "estimated_impact": "Serviço completamente indisponível enquanto o pod não estabilizar",
            "summary": f"Pod {pod} em CrashLoopBackOff — ver logs anteriores com --previous para identificar a causa raiz",
        },
    },
    # ------------------------------------------------------------------ #
    # Conexão recusada / timeout                                          #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"connection refused|ECONNREFUSED|connection reset|EOF|dial tcp.*refused", re.I),
        "extract": re.compile(r"(?:dial tcp|connect to|connecting to|host)\s+([\w\.\-]+(?::\d+)?)", re.I),
        "severity": "high",
        "build": lambda path, pod, ns: {
            "root_cause": f"Conexão recusada para '{path or 'serviço dependente'}' — serviço de destino fora do ar ou porta incorreta",
            "severity": "high",
            "immediate_action": [
                f"Verificar se o serviço de destino está rodando: kubectl get pods -n {ns} | grep {path.split(':')[0].split('.')[-1] if path else 'svc'}",
                f"Testar conectividade de dentro do pod: kubectl exec -n {ns} {pod} -- nc -zv {path or 'HOST PORT'}",
                f"Verificar o Service do Kubernetes: kubectl get svc -n {ns}",
            ],
            "prevention": [
                "Adicionar retry com backoff exponencial nas chamadas entre serviços",
                "Usar circuit breaker (ex: Istio, Resilience4j) para evitar cascata de falhas",
                "Configurar readinessProbe no serviço de destino para remover do balanceador quando indisponível",
            ],
            "estimated_impact": "Funcionalidade que depende do serviço externo indisponível",
            "summary": f"Pod {pod} não consegue conectar em '{path or 'dependência'}' — verificar se o serviço está ativo e acessível",
        },
    },
    # ------------------------------------------------------------------ #
    # Permissão negada                                                    #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"permission denied|access denied|EACCES|forbidden", re.I),
        "extract": re.compile(r'(?:open|stat|access|path=)["\s]+([/][\w/\-\.]+)', re.I),
        "severity": "high",
        "build": lambda path, pod, ns: {
            "root_cause": f"Permissão negada para acessar '{path or 'recurso'}' — usuário do contêiner não tem acesso ou RBAC insuficiente",
            "severity": "high",
            "immediate_action": [
                f"Verificar o usuário do contêiner: kubectl exec -n {ns} {pod} -- id",
                f"Ver as permissões do arquivo: kubectl exec -n {ns} {pod} -- ls -la {path or '/caminho'}",
                f"Ajustar permissões via initContainer ou securityContext no deployment",
            ],
            "prevention": [
                "Definir fsGroup e runAsUser no securityContext do pod para alinhar permissões do volume",
                "Usar initContainers com root para ajustar permissões antes da aplicação iniciar",
            ],
            "estimated_impact": "Aplicação incapaz de ler/gravar arquivos necessários — funcionalidade parcial ou total indisponível",
            "summary": f"Permissão negada em '{path or 'arquivo'}' no pod {pod} — ajustar securityContext ou permissões do volume",
        },
    },
    # ------------------------------------------------------------------ #
    # ImagePullBackOff                                                    #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"ImagePullBackOff|ErrImagePull|image.*not found|pull.*denied", re.I),
        "extract": re.compile(r'(?:image|pulling)["\s]+([\w\./\-:]+)', re.I),
        "severity": "critical",
        "build": lambda path, pod, ns: {
            "root_cause": f"Kubernetes não conseguiu baixar a imagem '{path or 'imagem desconhecida'}' — imagem inexistente, tag errada ou credenciais inválidas",
            "severity": "critical",
            "immediate_action": [
                f"Verificar se a imagem existe no registry: docker pull {path or 'IMAGEM:TAG'}",
                f"Checar imagePullSecrets do pod: kubectl describe pod {pod} -n {ns} | grep -i secret",
                f"Criar ou atualizar o secret de registry: kubectl create secret docker-registry regcred --docker-server=REGISTRY --docker-username=USER --docker-password=TOKEN -n {ns}",
            ],
            "prevention": [
                "Usar tags imutáveis (digest SHA) em vez de 'latest' para evitar inconsistências",
                "Validar a existência da imagem na pipeline de CI antes do deploy",
            ],
            "estimated_impact": "Pod não inicializa — serviço completamente indisponível",
            "summary": f"Falha ao baixar imagem '{path or 'imagem'}' — verificar tag, registry e credenciais",
        },
    },
    # ------------------------------------------------------------------ #
    # Timeout / deadline                                                  #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"deadline exceeded|context deadline|timed? ?out|timeout", re.I),
        "extract": re.compile(r"(?:connecting to|waiting for|calling)\s+([\w\.\-:]+)", re.I),
        "severity": "high",
        "build": lambda path, pod, ns: {
            "root_cause": f"Operação excedeu o tempo limite ao conectar/aguardar '{path or 'dependência'}' — sobrecarga, lentidão ou falha no serviço de destino",
            "severity": "high",
            "immediate_action": [
                f"Verificar uso de CPU/memória: kubectl top pod -n {ns}",
                f"Ver eventos recentes: kubectl get events -n {ns} --sort-by=.lastTimestamp | tail -20",
                f"Checar se o serviço de destino está saudável: kubectl get pods -n {ns}",
            ],
            "prevention": [
                "Aumentar o timeout configurado na aplicação se o serviço é lento por natureza",
                "Adicionar circuit breaker para falhar rápido e evitar acúmulo de requisições pendentes",
            ],
            "estimated_impact": "Requisições lentas ou com falha — experiência degradada para usuários",
            "summary": f"Timeout ao aguardar '{path or 'dependência'}' no pod {pod} — verificar saúde dos serviços dependentes",
        },
    },
    # ------------------------------------------------------------------ #
    # Panic / fatal crash                                                 #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"\bpanic\b|fatal error:|runtime error:|goroutine.*panic", re.I),
        "extract": re.compile(r"(?:panic:|error:)\s+(.{10,80})", re.I),
        "severity": "critical",
        "build": lambda path, pod, ns: {
            "root_cause": f"Aplicação encerrou com panic/crash fatal: '{path or 'erro não identificado'}' — ponteiro nulo, índice fora dos limites ou condição inesperada",
            "severity": "critical",
            "immediate_action": [
                f"Ver stack trace completo: kubectl logs {pod} -n {ns} --previous | tail -50",
                f"Verificar se o pod está em CrashLoopBackOff: kubectl get pod {pod} -n {ns}",
                f"Ver últimos eventos: kubectl describe pod {pod} -n {ns} | tail -20",
            ],
            "prevention": [
                "Adicionar recover() nos goroutines críticos (Go) para capturar panics e logar antes de encerrar",
                "Implementar testes de integração que cubram os casos limites que causam o panic",
                "Configurar readinessProbe para remover o pod do balanceador durante reinicializações",
            ],
            "estimated_impact": "Pod reiniciando — downtime até o Kubernetes reiniciar o contêiner",
            "summary": f"Panic fatal no pod {pod} — ver stack trace com --previous para identificar a linha exata",
        },
    },
    # ------------------------------------------------------------------ #
    # Certificado TLS / x509                                             #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(r"x509|certificate.*expired|tls.*handshake|ssl.*error|certificate.*invalid|certificate signed by unknown", re.I),
        "extract": re.compile(r"(?:host|server|peer|for)\s+([\w\.\-:]+)", re.I),
        "severity": "high",
        "build": lambda path, pod, ns: {
            "root_cause": f"Falha de certificado TLS ao conectar em '{path or 'serviço'}' — certificado expirado, autoassinado sem CA confiável, ou hostname não confere",
            "severity": "high",
            "immediate_action": [
                f"Verificar validade do certificado: kubectl exec -n {ns} {pod} -- openssl s_client -connect {path or 'HOST:443'} -showcerts 2>&1 | grep -E 'NotBefore|NotAfter'",
                f"Checar se há secret de TLS configurado: kubectl get secret -n {ns} | grep tls",
                f"Renovar o certificado ou atualizar o CA bundle no contêiner",
            ],
            "prevention": [
                "Usar cert-manager para renovação automática de certificados no Kubernetes",
                "Configurar alerta de monitoramento para certificados com menos de 30 dias de validade",
                "Usar --insecure-skip-tls-verify APENAS em ambientes de dev, nunca em produção",
            ],
            "estimated_impact": "Comunicação segura bloqueada — funcionalidades que dependem de HTTPS indisponíveis",
            "summary": f"Erro de certificado TLS ao conectar em '{path or 'serviço'}' no pod {pod} — verificar validade e CA bundle",
        },
    },
    # ------------------------------------------------------------------ #
    # Banco de dados (SQL / Postgres / MySQL / MongoDB / Redis)          #
    # ------------------------------------------------------------------ #
    {
        "match": re.compile(
            r"pq:|mysql:|mongo:|redis:|"
            r"database.*error|db.*connection|"
            r"too many connections|max.*connections|"
            r"relation.*does not exist|table.*not found|"
            r"FATAL.*database|ERROR.*syntax",
            re.I
        ),
        "extract": re.compile(r"(?:database|db|host|dsn)[=:\s]+[\"']?([\w\.\-:@/]+)", re.I),
        "severity": "high",
        "build": lambda path, pod, ns: {
            "root_cause": f"Erro de banco de dados no pod {pod} — conexão recusada, schema desatualizado ou limite de conexões atingido",
            "severity": "high",
            "immediate_action": [
                f"Verificar se o banco está acessível: kubectl exec -n {ns} {pod} -- nc -zv {path.split('/')[0] if path else 'DB_HOST DB_PORT'}",
                f"Checar secret com credenciais do banco: kubectl get secret -n {ns} | grep -i db",
                f"Ver número atual de conexões abertas no banco e comparar com o pool configurado na aplicação",
            ],
            "prevention": [
                "Configurar connection pool adequado (PgBouncer para Postgres, por exemplo)",
                "Adicionar retry com backoff nas queries críticas",
                "Usar migrations versionadas (Flyway/Liquibase) para manter o schema sincronizado",
            ],
            "estimated_impact": "Serviço incapaz de persistir ou ler dados — indisponibilidade total ou parcial para usuários",
            "summary": f"Erro de banco de dados no pod {pod} — verificar conectividade, credenciais e estado do schema",
        },
    },
]


def analyze(pod_name: str, namespace: str, error_info: dict) -> dict | None:
    """
    Tenta identificar o tipo de erro por regras e retorna análise contextual.
    Retorna None se nenhuma regra coincidir.
    """
    error_line = error_info.get("error_line", "")
    context_text = "\n".join(error_info.get("context", []))
    full_text = f"{error_line}\n{context_text}"

    for rule in _RULES:
        if not rule["match"].search(full_text):
            continue

        # Tenta extrair um caminho/host relevante do texto
        extracted = ""
        if rule.get("extract"):
            m = rule["extract"].search(full_text)
            extracted = m.group(1) if m else ""

        logger.info(
            "Regra '%s' aplicada para %s/%s — extraído: '%s'",
            rule["match"].pattern[:40], namespace, pod_name, extracted
        )

        result = rule["build"](extracted, pod_name, namespace)
        result["_source"] = "rule_based"
        return result

    return None
