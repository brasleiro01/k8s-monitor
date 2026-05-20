import logging
import signal
import sys
import threading

from config import (
    ANTHROPIC_API_KEY,
    DISCORD_WEBHOOK_URL,
    LOG_LEVEL,
    NAMESPACES,
    POSTMORTEM_DIR,
)
from ai_analyzer import AIAnalyzer
from discord_notifier import DiscordNotifier
from error_detector import ErrorDetector
from k8s_watcher import K8sLogWatcher, load_k8s_config
from postmortem_generator import PostmortemGenerator

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self):
        self._detector = ErrorDetector()
        self._analyzer = AIAnalyzer()
        self._notifier = DiscordNotifier()
        self._postmortem = PostmortemGenerator()
        self._ai_lock = threading.Lock()
        self._watcher = K8sLogWatcher(
            namespaces=NAMESPACES,
            on_log_line=self._on_log_line,
        )

    def start(self):
        logger.info(
            "Starting k8s log monitor | namespaces=%s | postmortem_dir=%s",
            NAMESPACES or "all",
            POSTMORTEM_DIR,
        )
        self._watcher.start()

    def stop(self):
        logger.info("Stopping monitor...")
        self._watcher.stop()

    def _on_log_line(self, pod_name: str, namespace: str, pod_key: str, line: str):
        error_info = self._detector.process_line(pod_key, line)
        if error_info is None:
            return

        logger.warning("Error detected in %s: %s", pod_key, error_info["error_line"][:120])

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
            logger.error("AI analysis returned None for %s/%s", namespace, pod_name)
            return

        postmortem_path = self._postmortem.generate(pod_name, namespace, error_info, analysis)
        self._notifier.notify(pod_name, namespace, error_info, analysis, postmortem_path)


def main():
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not DISCORD_WEBHOOK_URL:
        missing.append("DISCORD_WEBHOOK_URL")
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)

    load_k8s_config()

    monitor = Monitor()
    monitor.start()

    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        monitor.stop()
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    stop_event.wait()
    logger.info("Monitor stopped.")


if __name__ == "__main__":
    main()
