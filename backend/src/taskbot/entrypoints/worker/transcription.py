import logging

from billiard.exceptions import SoftTimeLimitExceeded

from taskbot.application.transcribe_voice import AttemptProgress, TranscribeVoiceJob
from taskbot.domain.jobs import TranscriptionJob
from taskbot.entrypoints.worker import app, runtime

logger = logging.getLogger(__name__)


@app.task(name=TranscriptionJob.name, soft_time_limit=420, time_limit=450)
def transcribe_voice_job(*args: object, **kwargs: object) -> None:
    progress = AttemptProgress()
    job: TranscriptionJob | None = None
    use_case: TranscribeVoiceJob | None = None
    try:
        if args:
            raise ValueError("Job payload must use keyword arguments")
        job = TranscriptionJob.model_validate(kwargs)
        use_case = runtime.current_container().transcribe
        if use_case is None:
            raise RuntimeError("Transcription use case is unavailable")
        runtime.run_async(use_case.execute(job, progress))
    except SoftTimeLimitExceeded:
        logger.warning("Transcription job interrupted (SoftTimeLimitExceeded)")
        if job is not None and use_case is not None and progress.attempt is not None:
            try:
                runtime.run_async(use_case.time_out(job.voice_job_id, progress.attempt))
            except Exception as error:
                logger.warning(
                    "Transcription timeout persistence failed (%s)", type(error).__name__
                )
    except Exception as error:
        # SQL errors can include source text even when engine parameters are hidden.
        logger.warning("Transcription job failed (%s)", type(error).__name__)
