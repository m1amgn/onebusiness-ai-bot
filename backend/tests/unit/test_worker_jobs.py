import asyncio
import logging
from types import SimpleNamespace

import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from taskbot.application.transcribe_voice import AttemptProgress
from taskbot.domain.jobs import NotificationJob, SweepJob, TranscriptionJob
from taskbot.entrypoints.worker import app, notification, runtime, transcription
from taskbot.infrastructure.celery.app import create_celery_app


class TranscriptionUseCase:
    def __init__(self, *, attempt: int | None = 2, failure: Exception | None = None) -> None:
        self.attempt = attempt
        self.failure = failure
        self.calls: list[object] = []
        self.timeout_failure: Exception | None = None

    async def execute(self, job: TranscriptionJob, progress: AttemptProgress) -> None:
        self.calls.append(job)
        progress.attempt = self.attempt
        if self.failure is not None:
            raise self.failure

    async def time_out(self, voice_job_id: int, attempt: int) -> None:
        self.calls.append((voice_job_id, attempt))
        if self.timeout_failure is not None:
            raise self.timeout_failure


class NotificationUseCase:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls: list[NotificationJob] = []
        self.failure = failure

    async def execute(self, job: NotificationJob) -> None:
        self.calls.append(job)
        if self.failure is not None:
            raise self.failure


class RecoveryUseCase:
    def __init__(self, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    async def execute(self) -> None:
        self.calls += 1
        if self.failure is not None:
            raise self.failure


@pytest.fixture
def use_cases(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    container = SimpleNamespace(
        transcribe=TranscriptionUseCase(), notify=NotificationUseCase(), recover=RecoveryUseCase()
    )
    monkeypatch.setattr(runtime, "current_container", lambda: container)
    monkeypatch.setattr(runtime, "run_async", asyncio.run)
    return container


def test_worker_registers_job_limits_routes_and_schedule() -> None:
    for job, soft, hard in (
        (TranscriptionJob, 420, 450),
        (NotificationJob, 60, 90),
        (SweepJob, 60, 90),
    ):
        handler = app.tasks[job.name]
        assert handler.soft_time_limit == soft
        assert handler.time_limit == hard
        assert app.conf.task_routes[job.name] == {"queue": job.queue}
    assert app.conf.beat_schedule == {
        "recover-stuck-voice-jobs": {
            "task": SweepJob.name,
            "schedule": 60,
            "options": {"expires": 60},
        }
    }
    reconstructed = create_celery_app(broker_url="redis://localhost:1/1", namespace="")
    assert not reconstructed.conf.beat_schedule
    assert {
        TranscriptionJob.name,
        NotificationJob.name,
        SweepJob.name,
    } <= reconstructed.tasks.keys()


def test_job_handlers_validate_and_dispatch(use_cases: SimpleNamespace) -> None:
    transcription.transcribe_voice_job.run(voice_job_id=12)
    notification.notify_user.run(voice_job_id=12, status="done")
    notification.recover_stuck_voice_jobs.run()
    assert use_cases.transcribe.calls == [TranscriptionJob(voice_job_id=12)]
    assert use_cases.notify.calls == [NotificationJob(voice_job_id=12, status="done")]
    assert use_cases.recover.calls == 1


@pytest.mark.parametrize("task_name", [TranscriptionJob.name, NotificationJob.name, SweepJob.name])
@pytest.mark.parametrize("positional", [False, True])
def test_invalid_payload_is_dropped_without_echoing_input(
    task_name: str,
    positional: bool,
    use_cases: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    task = app.tasks[task_name]
    with caplog.at_level(logging.WARNING):
        if positional:
            task.run("private transcript")
        else:
            task.run(unexpected="private transcript")
    assert not use_cases.transcribe.calls
    assert not use_cases.notify.calls
    assert use_cases.recover.calls == 0
    assert caplog.records
    assert "private transcript" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_transcription_failure_acknowledges_without_source_text(
    use_cases: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    use_cases.transcribe.failure = RuntimeError("private transcript in SQL parameters")
    transcription.transcribe_voice_job.run(voice_job_id=12)
    assert "RuntimeError" in caplog.text
    assert "private transcript" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("attempt", [None, 3])
def test_transcription_soft_limit_uses_only_the_known_attempt(
    attempt: int | None, use_cases: SimpleNamespace
) -> None:
    use_cases.transcribe = TranscriptionUseCase(attempt=attempt, failure=SoftTimeLimitExceeded())
    transcription.transcribe_voice_job.run(voice_job_id=12)
    expected: list[object] = [TranscriptionJob(voice_job_id=12)]
    if attempt is not None:
        expected.append((12, attempt))
    assert use_cases.transcribe.calls == expected


def test_timeout_persistence_failure_is_safe_and_acknowledged(
    use_cases: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    use_cases.transcribe.failure = SoftTimeLimitExceeded()
    use_cases.transcribe.timeout_failure = RuntimeError("private transcript")
    transcription.transcribe_voice_job.run(voice_job_id=12)
    assert use_cases.transcribe.calls[-1] == (12, 2)
    assert "RuntimeError" in caplog.text
    assert "private transcript" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("soft_limit", [False, True])
def test_notification_and_sweep_failures_are_safe_and_acknowledged(
    soft_limit: bool, use_cases: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    error = (
        SoftTimeLimitExceeded("private transcript")
        if soft_limit
        else RuntimeError("private transcript")
    )
    use_cases.notify.failure = error
    use_cases.recover.failure = error
    notification.notify_user.run(voice_job_id=12, status="failed")
    notification.recover_stuck_voice_jobs.run()
    assert type(error).__name__ in caplog.text
    assert "private transcript" not in caplog.text
    assert len(caplog.records) == 2
    assert all(record.exc_info is None for record in caplog.records)
