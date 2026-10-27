import asyncio
from collections.abc import Awaitable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from taskbot.application.create_tasks import CreateTasksCommand, CreateTasksFromMessage
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.replies import RETRYING
from taskbot.application.transcribe_voice import (
    AttemptProgress,
    Done,
    Dropped,
    Failed,
    Retrying,
    Stale,
    TranscribeVoiceJob,
    Waiting,
)
from taskbot.domain.errors import (
    EmptyMessageError,
    TranscriptionError,
    TransientTranscriptionError,
    VoiceFileTooLargeError,
    VoiceFileUnavailableError,
)
from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.ports.transcription import Transcript
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider, VoiceJobStatus
from tests.audio import synthetic_opus
from tests.contract.helpers import create_voice
from tests.fakes.clock import FakeClock
from tests.fakes.extraction import FakeExtractor
from tests.fakes.messaging import FakeEventPublisher, FakeJobQueue
from tests.fakes.persistence import FakeStore
from tests.fakes.telegram import FakeUserNotifier, FakeVoiceFileSource
from tests.fakes.transcription import ScriptedTranscriber


async def fixture(
    *, transcript: str = "Call home", failure: BaseException | None = None, attempts: int = 0
) -> tuple[TranscribeVoiceJob, FakeStore, FakeClock, FakeJobQueue, FakeUserNotifier]:
    store = FakeStore()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    queue = FakeJobQueue(calls=store.calls)
    notifier = FakeUserNotifier(calls=store.calls)
    async with store() as uow:
        row = await create_voice(uow, clock.now())
        await uow.voice_jobs.set_ack_message_id(row.id, 99, clock.now())
        await uow.commit()
    store.state.voice_jobs[1] = replace(store.state.voice_jobs[1], attempts=attempts)
    extraction = Extraction(
        task=TaskDraft(title="Call home"), extractor=ExtractorName.HEURISTIC, degraded=False
    )
    extractor = FakeExtractor(extraction, store.calls)
    transcriber = ScriptedTranscriber(
        [failure if failure is not None else Transcript(transcript, TranscriptionProvider.FAKE)],
        calls=store.calls,
    )
    create = CreateTasksFromMessage(store, extractor, RegisterOrGetUser(store, clock), clock)
    use_case = TranscribeVoiceJob(
        store,
        create,
        FakeVoiceFileSource(synthetic_opus(1), calls=store.calls),
        transcriber,
        extractor,
        notifier,
        queue,
        FakeEventPublisher(calls=store.calls),
        clock,
    )
    store.calls.items.clear()
    return use_case, store, clock, queue, notifier


async def test_success_commits_claim_before_slow_calls_and_publishes_after_complete() -> None:
    use_case, store, _, queue, _ = await fixture()
    progress = AttemptProgress()
    assert isinstance(await use_case.execute(TranscriptionJob(voice_job_id=1), progress), Done)
    assert progress.attempt == 1
    assert store.calls.items == [
        "uow.open",
        "voice_jobs.claim",
        "uow.commit",
        "uow.close",
        "files.fetch",
        "transcriber.transcribe",
        "extract",
        "uow.open",
        "voice_jobs.lock_attempt",
        "messages.add",
        "tasks.add_many",
        "voice_jobs.complete",
        "uow.commit",
        "uow.close",
        "events.publish",
        "queue.enqueue",
    ]
    assert store.state.voice_jobs[1].status == VoiceJobStatus.DONE
    assert queue.jobs == [(NotificationJob(voice_job_id=1, status="done"), 0)]
    assert len(store.state.messages) == len(store.state.tasks) == 1


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (TranscriptionError(FailureKind.PROVIDER_UNAVAILABLE), FailureKind.PROVIDER_UNAVAILABLE),
        (TranscriptionError(FailureKind.TRANSCRIPTION_TIMEOUT), FailureKind.TRANSCRIPTION_TIMEOUT),
        (VoiceFileTooLargeError("private"), FailureKind.TOO_LARGE),
        (EmptyMessageError("private"), FailureKind.EMPTY_TRANSCRIPT),
        (TimeoutError("private"), FailureKind.INTERNAL),
        (RuntimeError("private"), FailureKind.INTERNAL),
    ],
)
async def test_final_failure_mapping_logs_no_input(
    failure: Exception, kind: FailureKind, caplog: pytest.LogCaptureFixture
) -> None:
    use_case, store, _, queue, _ = await fixture(failure=failure)
    result = await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert isinstance(result, Failed)
    assert store.state.voice_jobs[1].failure_kind == kind
    assert queue.jobs == [(NotificationJob(voice_job_id=1, status="failed"), 0)]
    assert "private" not in caplog.text
    assert not store.state.messages


@pytest.mark.parametrize("attempt", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("ack", [None, 99])
async def test_transient_attempts_and_retry_edit_order(attempt: int, ack: int | None) -> None:
    use_case, store, _, queue, notifier = await fixture(
        failure=TransientTranscriptionError(), attempts=attempt - 1
    )
    store.state.voice_jobs[1] = replace(store.state.voice_jobs[1], ack_message_id=ack)
    result = await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    row = store.state.voice_jobs[1]
    if attempt == 5:
        assert isinstance(result, Failed)
        assert row.failure_kind == FailureKind.TRANSIENT_EXHAUSTED
        assert queue.jobs == [(NotificationJob(voice_job_id=1, status="failed"), 0)]
    else:
        assert isinstance(result, Retrying)
        assert row.status == VoiceJobStatus.QUEUED
        assert queue.jobs == [(TranscriptionJob(voice_job_id=1), 2**attempt)]
    expected_edits = [(7, 99, RETRYING)] if attempt == 1 and ack is not None else []
    assert notifier.edits == expected_edits
    assert notifier.count == len(expected_edits)
    if expected_edits:
        assert store.calls.items.index("notifier.edit_message") < store.calls.items.index(
            "voice_jobs.requeue"
        )


@pytest.mark.parametrize("ack", [None, 99])
async def test_download_unavailable_is_transient_and_retry_edit_errors_are_safe(
    ack: int | None, caplog: pytest.LogCaptureFixture
) -> None:
    use_case, store, _, queue, notifier = await fixture()
    store.state.voice_jobs[1] = replace(store.state.voice_jobs[1], ack_message_id=ack)
    notifier.failures = {1: RuntimeError("private")}
    use_case._files = FakeVoiceFileSource(failures={1: VoiceFileUnavailableError("private")})
    result = await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert isinstance(result, Retrying)
    assert queue.jobs[0][1] == 2
    assert notifier.count == (1 if ack is not None else 0)
    assert not notifier.edits
    assert "private" not in caplog.text


@pytest.mark.parametrize(("seconds", "kind"), [(121, None), (121.02, FailureKind.TOO_LONG)])
async def test_packet_duration_boundary_precedes_provider_call(
    seconds: float, kind: FailureKind | None
) -> None:
    use_case, store, _, _, _ = await fixture()
    use_case._files = FakeVoiceFileSource(synthetic_opus(seconds), calls=store.calls)
    await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert store.state.voice_jobs[1].failure_kind == kind
    assert ("transcriber.transcribe" in store.calls.items) == (kind is None)


async def test_unreadable_audio_and_blank_transcript_are_final() -> None:
    use_case, store, _, _, _ = await fixture()
    use_case._files = FakeVoiceFileSource(b"bad")
    await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert store.state.voice_jobs[1].failure_kind == FailureKind.UNSUPPORTED_AUDIO
    use_case, store, _, _, _ = await fixture(transcript=" \n ")
    await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert store.state.voice_jobs[1].failure_kind == FailureKind.EMPTY_TRANSCRIPT


async def test_transcript_is_bounded_before_extraction(caplog: pytest.LogCaptureFixture) -> None:
    transcript = "private words " * 400
    use_case, store, _, queue, _ = await fixture(transcript=transcript)
    with caplog.at_level("INFO"):
        result = await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert isinstance(result, Done)
    assert store.state.messages[1].text == transcript[:4096].rsplit(" ", 1)[0]
    assert len(store.state.tasks) == 1
    assert store.state.tasks[1].inbound_message_id == 1
    assert store.state.tasks[1].title == "Call home"
    assert store.state.voice_jobs[1].status == VoiceJobStatus.DONE
    assert queue.jobs == [(NotificationJob(voice_job_id=1, status="done"), 0)]
    assert "truncat" in caplog.text.lower()
    assert "private words" not in caplog.text


async def test_stale_attempt_cannot_create_messages_or_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_case, store, clock, _, _ = await fixture()

    async def newer_claim(audio: bytes) -> Transcript:
        clock.advance(451)
        async with store() as uow:
            await uow.voice_jobs.claim(1, clock.now())
            await uow.commit()
        return Transcript("Call home", TranscriptionProvider.FAKE)

    monkeypatch.setattr(use_case._transcriber, "transcribe", newer_claim)
    result = await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert isinstance(result, Stale)
    assert not store.state.messages
    assert not store.state.tasks
    assert store.state.voice_jobs[1].attempts == 2


async def test_uow_b_error_escapes_mapping_and_leaves_processing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    use_case, store, _, queue, _ = await fixture()
    store.fail_on = "tasks.add_many"
    with pytest.raises(RuntimeError, match="forced failure"):
        await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert store.state.voice_jobs[1].status == VoiceJobStatus.PROCESSING
    assert not store.state.messages
    assert not queue.jobs
    assert not caplog.records


async def test_own_budget_expiry_is_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    use_case, store, _, _, _ = await fixture()

    async def block(audio: bytes) -> Transcript:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(use_case._transcriber, "transcribe", block)
    monkeypatch.setattr("taskbot.application.transcribe_voice.ATTEMPT_BUDGET_SECONDS", 0.01)
    await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert store.state.voice_jobs[1].failure_kind == FailureKind.TRANSCRIPTION_TIMEOUT


async def test_injected_interrupt_propagates_and_soft_timeout_is_guarded() -> None:
    class InterruptError(Exception):
        pass

    error = InterruptError("private")
    use_case, store, _, queue, _ = await fixture(failure=error)
    use_case._interrupts = (InterruptError,)
    progress = AttemptProgress()
    with pytest.raises(InterruptError) as caught:
        await use_case.execute(TranscriptionJob(voice_job_id=1), progress)
    assert caught.value is error
    assert progress.attempt == 1
    assert isinstance(await use_case.time_out(1, 2), Stale)
    assert isinstance(await use_case.time_out(1, 1), Failed)
    assert store.state.voice_jobs[1].failure_kind == FailureKind.TRANSCRIPTION_TIMEOUT
    assert len(queue.jobs) == 1


async def test_live_lease_waits_without_spending_an_attempt_then_finished_drops() -> None:
    use_case, store, clock, queue, _ = await fixture()
    async with store() as uow:
        await uow.voice_jobs.claim(1, clock.now())
        await uow.commit()
    clock.advance(450)
    progress = AttemptProgress()
    result = await use_case.execute(TranscriptionJob(voice_job_id=1), progress)
    assert isinstance(result, Waiting)
    assert queue.jobs == [(TranscriptionJob(voice_job_id=1), 1)]
    assert progress.attempt is None
    clock.advance(1)
    await use_case.execute(TranscriptionJob(voice_job_id=1), progress)
    assert isinstance(await use_case.execute(TranscriptionJob(voice_job_id=1), progress), Dropped)
    assert isinstance(await use_case.execute(TranscriptionJob(voice_job_id=99), progress), Dropped)


async def test_attempt_ceiling_exhausts_once_without_slow_io() -> None:
    use_case, store, _, queue, _ = await fixture(attempts=5)
    result = await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    assert isinstance(result, Failed)
    assert store.state.voice_jobs[1].failure_kind == FailureKind.STUCK
    assert queue.jobs == [(NotificationJob(voice_job_id=1, status="failed"), 0)]
    assert "files.fetch" not in store.calls.items


async def test_enqueue_failure_preserves_committed_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    use_case, store, _, queue, _ = await fixture()
    queue.fail = True
    assert isinstance(
        await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress()), Done
    )
    assert store.state.voice_jobs[1].status == VoiceJobStatus.DONE
    assert "EnqueueError" in caplog.text


@pytest.mark.parametrize("port", ["download", "transcribe", "extract"])
async def test_slow_port_fakes_refuse_an_open_unit_of_work(port: str) -> None:
    use_case, store, _, _, _ = await fixture()
    pending: Awaitable[object]
    if port == "download":
        pending = use_case._files.fetch("file", max_bytes=10_000_000)
    elif port == "transcribe":
        pending = use_case._transcriber.transcribe(b"audio")
    else:
        pending = use_case._extractor.extract(MessageText.parse("Call home"))
    async with store():
        with pytest.raises(AssertionError, match="inside a unit of work"):
            await pending


async def test_claim_commit_failure_does_not_record_attempt_or_call_slow_ports() -> None:
    use_case, store, _, _, _ = await fixture()
    store.fail_on = "commit"
    progress = AttemptProgress()
    with pytest.raises(RuntimeError, match="commit"):
        await use_case.execute(TranscriptionJob(voice_job_id=1), progress)
    assert progress.attempt is None
    assert store.state.voice_jobs[1].status == VoiceJobStatus.QUEUED
    assert "files.fetch" not in store.calls.items


async def test_duplicate_source_completes_without_recreating_tasks() -> None:
    use_case, store, _, _, _ = await fixture()
    await use_case._create.execute(
        CreateTasksCommand(
            TelegramIdentity(7, "A"), MessageRef(7, 10), MessageKind.TEXT, "Previous text"
        )
    )
    original_messages = store.state.messages.copy()
    original_tasks = store.state.tasks.copy()
    assert isinstance(
        await use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress()), Done
    )
    assert store.state.messages == original_messages
    assert store.state.tasks == original_tasks
    assert store.state.voice_jobs[1].inbound_message_id == 1


async def test_cancellation_propagates_without_a_terminal_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_case, store, _, queue, _ = await fixture()
    entered = asyncio.Event()

    async def block(audio: bytes) -> Transcript:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(use_case._transcriber, "transcribe", block)
    task = asyncio.create_task(
        use_case.execute(TranscriptionJob(voice_job_id=1), AttemptProgress())
    )
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert store.state.voice_jobs[1].status == VoiceJobStatus.PROCESSING
        assert not queue.jobs
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
