import logging
import threading
import time
from typing import Callable

from kubernetes import client, config, watch

logger = logging.getLogger(__name__)


def load_k8s_config():
    try:
        config.load_incluster_config()
        logger.info("Using in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Using local kubeconfig")


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
        v1 = client.CoreV1Api()
        w = watch.Watch()
        ns_label = namespace or "all namespaces"
        logger.info("Watching pods in %s", ns_label)

        while self._running:
            try:
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
                        if phase == "Running":
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
        v1 = client.CoreV1Api()
        w = watch.Watch()

        while not stop_event.is_set():
            try:
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
