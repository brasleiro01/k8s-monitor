import base64
import json as _json
import logging
import os
import threading
import time
from typing import Callable

import requests as _req
from kubernetes import client, config, watch

from config import EXCLUDE_NAMESPACES

logger = logging.getLogger(__name__)

_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

_k8s_host: str = ""
_k8s_port: str = "443"
_k8s_token: str = ""


def load_k8s_config():
    global _k8s_host, _k8s_port, _k8s_token

    host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")

    if host and os.path.exists(_TOKEN_PATH):
        _k8s_host = host
        _k8s_port = port
        _k8s_token = open(_TOKEN_PATH).read().strip()
        logger.info("[k8s] credentials loaded: host=%s:%s token=%d bytes", host, port, len(_k8s_token))
    else:
        try:
            config.load_incluster_config()
            logger.info("Using in-cluster Kubernetes config (auto)")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Using local kubeconfig")

    _diagnose()


def _new_api_client() -> client.ApiClient:
    """Cria ApiClient com Bearer token no default_headers, bypassando api_key."""
    if _k8s_token and _k8s_host:
        cfg = client.Configuration()
        cfg.host = f"https://{_k8s_host}:{_k8s_port}"
        cfg.verify_ssl = os.path.exists(_CA_PATH)
        if cfg.verify_ssl:
            cfg.ssl_ca_cert = _CA_PATH
        api = client.ApiClient(configuration=cfg)
        api.default_headers["Authorization"] = f"Bearer {_k8s_token}"
        return api
    return client.ApiClient()


def _diagnose():
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")

    logger.info("[k8s] API server: %s:%s", host, port)
    logger.info("[k8s] CA cert: %s", "existe" if os.path.exists(_CA_PATH) else "NÃO ENCONTRADO")

    if not os.path.exists(_TOKEN_PATH):
        logger.error("[k8s] token NÃO ENCONTRADO em %s", _TOKEN_PATH)
        return

    token = open(_TOKEN_PATH).read().strip()
    logger.info("[k8s] token: %d bytes", len(token))

    try:
        parts = token.split(".")
        if len(parts) == 3:
            pad = lambda s: s + "=" * (4 - len(s) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(pad(parts[1])))
            logger.info("[k8s] JWT iss=%s  sub=%s", payload.get("iss"), payload.get("sub"))
            exp = payload.get("exp")
            if exp:
                import datetime
                logger.info("[k8s] JWT expira: %s UTC", datetime.datetime.utcfromtimestamp(exp))
            else:
                logger.info("[k8s] JWT sem expiração (token estático)")
        else:
            logger.warning("[k8s] conteúdo do token não é um JWT (partes=%d)", len(parts))
    except Exception as e:
        logger.warning("[k8s] falha ao decodificar JWT: %s", e)

    if not host:
        return

    try:
        verify = _CA_PATH if os.path.exists(_CA_PATH) else False
        resp = _req.get(
            f"https://{host}:{port}/api/v1/namespaces",
            headers={"Authorization": f"Bearer {token}"},
            verify=verify,
            timeout=5,
        )
        if resp.status_code == 200:
            logger.info("[k8s] HTTP direto: 200 OK")
        elif resp.status_code == 401:
            logger.error("[k8s] HTTP direto: 401 — token REJEITADO pelo cluster")
        elif resp.status_code == 403:
            logger.info("[k8s] HTTP direto: 403 — token OK (sem permissão p/ namespaces, esperado)")
        else:
            logger.warning("[k8s] HTTP direto: %d %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("[k8s] HTTP direto FALHOU: %s", e)


class K8sLogWatcher:
    def __init__(self, namespaces: list[str], on_log_line: Callable[[str, str, str, str], None]):
        self._namespaces = namespaces
        self._on_log_line = on_log_line
        self._pod_threads: dict[str, threading.Thread] = {}
        self._pod_stop_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        if self._namespaces:
            for ns in self._namespaces:
                t = threading.Thread(target=self._watch_namespace, args=(ns,), daemon=True)
                t.start()
        else:
            t = threading.Thread(target=self._watch_namespace, args=(None,), daemon=True)
            t.start()

    def stop(self):
        self._running = False
        with self._lock:
            for stop_event in self._pod_stop_events.values():
                stop_event.set()

    def _watch_namespace(self, namespace: str):
        ns_label = namespace or "all namespaces"
        logger.info("Watching pods in %s", ns_label)

        while self._running:
            try:
                v1 = client.CoreV1Api(api_client=_new_api_client())
                w = watch.Watch()
                stream = w.stream(
                    v1.list_namespaced_pod if namespace else v1.list_pod_for_all_namespaces,
                    *([namespace] if namespace else []),
                    timeout_seconds=0,
                )
                for event in stream:
                    if not self._running:
                        break
                    pod = event["object"]
                    event_type = event["type"]
                    pod_name = pod.metadata.name
                    pod_ns = pod.metadata.namespace
                    pod_key = f"{pod_ns}/{pod_name}"

                    if event_type in ("ADDED", "MODIFIED"):
                        phase = pod.status.phase if pod.status else None
                        if phase == "Running" and pod_ns not in EXCLUDE_NAMESPACES:
                            self._ensure_pod_watched(pod_name, pod_ns, pod_key)
                    elif event_type == "DELETED":
                        self._stop_pod_watcher(pod_key)
            except Exception as e:
                logger.warning("Pod watch error in %s: %s — retrying in 5s", ns_label, e)
                time.sleep(5)

    def _ensure_pod_watched(self, pod_name: str, namespace: str, pod_key: str):
        with self._lock:
            if pod_key in self._pod_threads and self._pod_threads[pod_key].is_alive():
                return
            stop_event = threading.Event()
            self._pod_stop_events[pod_key] = stop_event
            t = threading.Thread(
                target=self._stream_pod_logs,
                args=(pod_name, namespace, pod_key, stop_event),
                daemon=True,
            )
            self._pod_threads[pod_key] = t
            t.start()
            logger.debug("Started log watcher for %s", pod_key)

    def _stop_pod_watcher(self, pod_key: str):
        with self._lock:
            stop_event = self._pod_stop_events.pop(pod_key, None)
            if stop_event:
                stop_event.set()
            self._pod_threads.pop(pod_key, None)
            logger.debug("Stopped log watcher for %s", pod_key)

    def _stream_pod_logs(self, pod_name: str, namespace: str, pod_key: str, stop_event: threading.Event):
        while not stop_event.is_set():
            try:
                v1 = client.CoreV1Api(api_client=_new_api_client())
                w = watch.Watch()
                stream = w.stream(
                    v1.read_namespaced_pod_log,
                    name=pod_name,
                    namespace=namespace,
                    follow=True,
                    tail_lines=100,
                    _request_timeout=60,
                )
                for line in stream:
                    if stop_event.is_set():
                        break
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    self._on_log_line(pod_name, namespace, pod_key, line)
            except Exception as e:
                if stop_event.is_set():
                    break
                logger.debug("Log stream error for %s: %s — retrying in 10s", pod_key, e)
                time.sleep(10)
