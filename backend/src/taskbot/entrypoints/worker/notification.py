import logging

from taskbot.domain.jobs import NotificationJob, SweepJob
from taskbot.entrypoints.worker import app, runtime

logger = logging.getLogger(__name__)


@app.task(name=NotificationJob.name, soft_time_limit=60, time_limit=90)
def notify_user(*args: object, **kwargs: object) -> None:
    try:
        if args:
            raise ValueError("Job payload must use keyword arguments")
        job = NotificationJob.model_validate(kwargs)
        use_case = runtime.current_container().notify
        if use_case is None:
            raise RuntimeError("Notification use case is unavailable")
        runtime.run_async(use_case.execute(job))
    except Exception as error:
        logger.warning("Notification job failed (%s)", type(error).__name__)


@app.task(name=SweepJob.name, soft_time_limit=60, time_limit=90)
def recover_stuck_voice_jobs(*args: object, **kwargs: object) -> None:
    try:
        if args:
            raise ValueError("Job payload must use keyword arguments")
        SweepJob.model_validate(kwargs)
        use_case = runtime.current_container().recover
        if use_case is None:
            raise RuntimeError("Recovery use case is unavailable")
        runtime.run_async(use_case.execute())
    except Exception as error:
        logger.warning("Voice recovery job failed (%s)", type(error).__name__)
