import logging
import threading

from config import NAMESPACES
from ai_analyzer import AIAnalyzer
from discord_notifier import DiscordNotifier
from error_detector import ErrorDetector
from k8s_watcher import K8sLogWatcher
from postmortem_generator import PostmortemGenerator

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self):
        self._detector = ErrorDetector()
        self._analyzer = AIAnalyzer()
        self._notifier = DiscordNotifier()
        self._incident_store = PostmortemGenerator()
        self._ai_lock = threading.Lock()
        self._watcher = K8sLogWatcher(
            namespaces=NAMESPACES,
            on_log_line=self._on_log_line,
        )

    def start(self):
        logger.info("Iniciando k8s log monitor | namespaces=%s", NAMESPACES or "todos")
        self._watcher.start()

    def stop(self):
        logger.info("Encerrando monitor...")
        self._watcher.stop()

    def _on_log_line(self, pod_name: str, namespace: str, pod_key: str, line: str):
        error_info = self._detector.process_line(pod_key, line)
        if error_info is None:
            return

        logger.warning("Erro detectado em %s: %s", pod_key, error_info["error_line"][:120])

        t = threading.Thread(
            target=self._handle_error,
            args=(pod_name, namespace, error_info),
            daemon=True,
        )
        t.start()

    def _handle_error(self, pod_name: str, namespace: str, error_info: dict):
        with self._ai_lock:
            analysis = self._analyzer.analyze(pod_name, namespace, error_info)

        if analysis is None:
            logger.error("Análise de IA retornou None para %s/%s", namespace, pod_name)
            return

        self._incident_store.save_incident_json(pod_name, namespace, error_info, analysis)
        self._notifier.notify(pod_name, namespace, error_info, analysis)
