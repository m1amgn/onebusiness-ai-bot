import logging
import os
import sys
from urllib.parse import urlsplit, urlunsplit

from celery import signals

from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.entrypoints.settings import load_settings
from taskbot.entrypoints.worker import runtime
from taskbot.infrastructure.settings.worker import WorkerSettings

logger = logging.getLogger(__name__)


def worker_init(sender: object = None, **kwargs: object) -> None:
    os.environ["TASKBOT_WORKER_MAIN_PID"] = str(os.getpid())
    load_settings(WorkerSettings)
    pool = getattr(sender, "pool_cls", None)
    if (
        sys.platform == "darwin"
        and "prefork" in getattr(pool, "__module__", "")
        and not os.environ.get("FORKED_BY_MULTIPROCESSING")
    ):
        print(
            "Run the worker through python -m taskbot.entrypoints.worker on macOS", file=sys.stderr
        )
        raise SystemExit(2)


def _broker_display(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is not None:
        login, host = parts.netloc.rsplit("@", 1)
        parts = parts._replace(netloc=login.split(":", 1)[0] + ":***@" + host)
    return urlunsplit(parts)


def worker_ready(sender: object = None, **kwargs: object) -> None:
    from taskbot.entrypoints.worker import app

    settings = load_settings(WorkerSettings)
    job = TranscriptionJob if settings.worker_role == "transcribe" else NotificationJob
    logger.info(
        "Worker ready role=%s queue=%s node=%s broker=%s namespace=%s",
        settings.worker_role,
        job.queue,
        getattr(sender, "hostname", "?"),
        _broker_display(settings.broker_url),
        settings.redis_namespace,
    )
    if settings.telegram_bot_token is None:
        logger.warning("TELEGRAM_BOT_TOKEN is not set: Telegram calls will fail")
    if job.name not in app.tasks:
        logger.warning(
            "No job handler is registered for queue %s: its jobs will be discarded", job.queue
        )


def worker_process_init(**kwargs: object) -> None:
    runtime.start_child()


def worker_process_shutdown(**kwargs: object) -> None:
    runtime.close_child()


signals.worker_init.connect(worker_init, weak=False)
signals.worker_ready.connect(worker_ready, weak=False)
signals.worker_process_init.connect(worker_process_init, weak=False)
signals.worker_process_shutdown.connect(worker_process_shutdown, weak=False)
