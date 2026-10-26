import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from taskbot.application.accept_voice import (
    VoiceAccepted,
    VoiceAlreadyAccepted,
    VoiceRefused,
)
from taskbot.application.dashboard_token import dashboard_link
from taskbot.domain.errors import NotificationError
from taskbot.domain.voice_jobs import VoiceJobStatus
from tests.bot import voice_command, voice_fixture, voice_use_case
from tests.fakes.telegram import FakeUserNotifier


async def test_voice_commits_acknowledges_then_enqueues_without_an_open_transaction() -> None:
    store, clock, queue = voice_fixture()
    notifier = FakeUserNotifier(calls=store.calls)
    command = voice_command(35, 100)
    result = await voice_use_case(store, clock, queue, notifier).execute(command)
    assert isinstance(result, VoiceAccepted)
    assert result.acknowledged
    assert result.enqueued
    job = store.state.voice_jobs[result.job_id]
    assert job.ack_message_id == 1
    assert job.last_enqueued_at == clock.now()
    assert notifier.replies[0].text == "Transcribing your voice message…"
    assert notifier.replies[0].reply_to_message_id == 1
    assert queue.jobs[0][0].voice_job_id == result.job_id
    assert store.calls.items == [
        "uow.open",
        "users.upsert",
        "voice_jobs.add_or_get",
        "uow.commit",
        "uow.close",
        "notifier.send_reply",
        "uow.open",
        "voice_jobs.set_ack",
        "uow.commit",
        "uow.close",
        "queue.enqueue",
        "uow.open",
        "voice_jobs.mark_enqueued",
        "uow.commit",
        "uow.close",
    ]


@pytest.mark.parametrize(
    ("duration", "size", "reason"),
    [(121, 1, "too_long"), (1, 10_000_001, "too_large"), (120, 10_000_000, None), (1, None, None)],
)
async def test_voice_limits_refuse_before_any_io(
    duration: int, size: int | None, reason: str | None
) -> None:
    store, clock, queue = voice_fixture()
    command = voice_command(duration, size)
    result = await voice_use_case(store, clock, queue, FakeUserNotifier()).execute(command)
    if reason:
        assert isinstance(result, VoiceRefused)
        assert result.reason == reason
        assert not store.state.voice_jobs
        assert not queue.jobs
        assert not store.calls.items
    else:
        assert len(store.state.voice_jobs) == len(queue.jobs) == 1


async def test_failed_enqueue_keeps_ack_and_is_eligible_for_the_recovery_sweep() -> None:
    store, clock, queue = voice_fixture()
    queue.fail = True
    result = await voice_use_case(store, clock, queue, FakeUserNotifier()).execute(
        voice_command(1, None)
    )
    assert isinstance(result, VoiceAccepted)
    assert not result.enqueued
    assert result.acknowledged
    assert store.state.voice_jobs[result.job_id].status == VoiceJobStatus.QUEUED
    async with store() as uow:
        assert await uow.voice_jobs.stamp_stale_queued(clock.now() + timedelta(seconds=461)) == [
            result.job_id
        ]


async def test_failed_ack_still_enqueues_and_duplicate_resumes_only_until_ack_is_stored() -> None:
    store, clock, queue = voice_fixture()
    notifier = FakeUserNotifier(failures={1: NotificationError("synthetic")})
    use_case = voice_use_case(store, clock, queue, notifier)
    command = voice_command(1, None)
    await use_case.execute(command)
    assert store.state.voice_jobs[1].ack_message_id is None
    assert len(queue.jobs) == 1
    await use_case.execute(command)
    assert store.state.voice_jobs[1].ack_message_id is not None
    assert len(queue.jobs) == 2
    assert isinstance(await use_case.execute(command), VoiceAlreadyAccepted)
    assert len(notifier.replies) == 1
    assert len(queue.jobs) == 2


@pytest.mark.parametrize("operation", ["ack", "enqueue"])
async def test_unexpected_adapter_error_after_acceptance_is_contained(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, clock, queue = voice_fixture()
    notifier = FakeUserNotifier(
        failures={1: RuntimeError("private response")} if operation == "ack" else {}
    )
    if operation == "enqueue":

        async def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("private broker response")

        monkeypatch.setattr(queue, "enqueue", fail)
    result = await voice_use_case(store, clock, queue, notifier).execute(voice_command(1, None))
    assert isinstance(result, VoiceAccepted)
    assert result.acknowledged == (operation != "ack")
    assert result.enqueued == (operation != "enqueue")
    assert len(queue.jobs) == (operation != "enqueue")


@pytest.mark.parametrize("operation", ["ack", "enqueue"])
async def test_cancellation_after_acceptance_propagates(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, clock, queue = voice_fixture()
    notifier = FakeUserNotifier()
    entered = asyncio.Event()

    async def gated(*args: object, **kwargs: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    if operation == "ack":
        monkeypatch.setattr(notifier, "send_reply", gated)
    else:
        monkeypatch.setattr(queue, "enqueue", gated)
    task = asyncio.create_task(
        voice_use_case(store, clock, queue, notifier).execute(voice_command(1, None))
    )
    try:
        async with asyncio.timeout(2):
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(store.state.voice_jobs) == 1
        assert not queue.jobs
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "status", [VoiceJobStatus.PROCESSING, VoiceJobStatus.DONE, VoiceJobStatus.FAILED]
)
async def test_duplicate_without_ack_does_not_resume_processing_or_terminal_job(
    status: VoiceJobStatus,
) -> None:
    store, clock, queue = voice_fixture()
    notifier = FakeUserNotifier(failures={1: NotificationError("synthetic")})
    use_case = voice_use_case(store, clock, queue, notifier)
    command = voice_command(1, None)
    await use_case.execute(command)
    store.state.voice_jobs[1] = replace(store.state.voice_jobs[1], status=status)
    assert isinstance(await use_case.execute(command), VoiceAlreadyAccepted)
    assert not notifier.replies
    assert len(queue.jobs) == 1


@pytest.mark.parametrize("failure", ["voice_jobs.set_ack", "voice_jobs.mark_enqueued"])
async def test_metadata_failure_after_acceptance_still_enqueues_and_returns_normally(
    failure: str,
) -> None:
    store, clock, queue = voice_fixture()
    store.fail_on = failure
    result = await voice_use_case(store, clock, queue, FakeUserNotifier()).execute(
        voice_command(1, None)
    )
    assert isinstance(result, VoiceAccepted)
    assert result.enqueued
    assert len(store.state.voice_jobs) == len(queue.jobs) == 1


def test_dashboard_link_uses_the_fragment_route() -> None:
    assert (
        dashboard_link("https://example.invalid", "synthetic")
        == "https://example.invalid/#token=synthetic"
    )
