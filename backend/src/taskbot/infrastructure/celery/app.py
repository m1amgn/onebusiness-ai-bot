from celery import Celery

from taskbot.domain.jobs import NotificationJob, SweepJob, TranscriptionJob


def create_celery_app(*, broker_url: str, namespace: str) -> Celery:
    """Build shared producer/worker configuration without opening a connection."""
    transport_options: dict[str, object] = {
        "visibility_timeout": 3600,
        "socket_connect_timeout": 2,
        "socket_timeout": 5,
    }
    if namespace:
        transport_options["global_keyprefix"] = namespace
    app = Celery("taskbot", broker=broker_url, set_as_current=False)
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        task_routes={
            job.name: {"queue": job.queue} for job in (TranscriptionJob, NotificationJob, SweepJob)
        },
        worker_hijack_root_logger=False,
        worker_proc_alive_timeout=30,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=4,
        task_publish_retry_policy={
            "max_retries": 2,
            "interval_start": 0,
            "interval_step": 0.2,
            "interval_max": 0.5,
        },
        broker_transport_options=transport_options,
    )
    return app
