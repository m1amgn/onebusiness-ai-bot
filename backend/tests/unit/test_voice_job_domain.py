from dataclasses import replace
from datetime import UTC, datetime, timedelta


def test_claim_leases_attempt_fences_and_ceiling() -> None:
    from taskbot.domain.messages import MessageRef
    from taskbot.domain.voice_jobs import (
        CeilingReached,
        Claimed,
        FailureKind,
        JobMissing,
        LeaseLive,
        TranscriptionProvider,
        VoiceJob,
        VoiceJobStatus,
        classify_claim_refusal,
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = VoiceJob(1, 2, MessageRef(2, 3), "file", "unique", 5, None, now, now, now)
    assert isinstance(classify_claim_refusal(None, now), JobMissing)
    for attempt in range(1, 6):
        claimed = job.claim(now)
        assert claimed is not None
        assert claimed.attempts == attempt
        assert isinstance(Claimed(claimed), Claimed)
        assert claimed.claim(now) is None
        outcome = classify_claim_refusal(claimed, now + timedelta(milliseconds=1))
        assert isinstance(outcome, LeaseLive)
        assert outcome.remaining_seconds == 450
        assert claimed.complete(attempt + 1, 9, TranscriptionProvider.FAKE, now) is None
        assert claimed.lock_attempt(attempt + 1) is None
        assert claimed.lock_attempt(attempt) == claimed
        if attempt == 5:
            assert claimed.exhaust(now) is None
            assert claimed.requeue(attempt, now) is None
        job = claimed
        now += timedelta(seconds=451)
    assert job.claim(now) is None
    assert isinstance(classify_claim_refusal(job, now), CeilingReached)
    exhausted = job.exhaust(now)
    assert exhausted is not None
    assert exhausted.status == VoiceJobStatus.FAILED
    assert exhausted.failure_kind == FailureKind.STUCK
    assert exhausted.claim(now) is None
    assert exhausted.exhaust(now) is None
    assert replace(job, status=VoiceJobStatus.QUEUED).exhaust(now) is not None


def test_retry_completion_ack_and_terminal_guards() -> None:
    from taskbot.domain.messages import MessageRef
    from taskbot.domain.voice_jobs import (
        AlreadyFinished,
        FailureKind,
        LeaseLive,
        TranscriptionProvider,
        VoiceJob,
        classify_claim_refusal,
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    initial = VoiceJob(1, 2, MessageRef(2, 3), "file", "unique", 5, None, now, now, now)
    ack = initial.set_ack_message_id(9, now)
    assert ack is not None
    assert ack.set_ack_message_id(10, now) is None
    first = ack.claim(now)
    assert first is not None
    queued = first.requeue(1, now)
    assert queued is not None
    assert isinstance(classify_claim_refusal(queued, now), LeaseLive)
    assert classify_claim_refusal(queued, now) == LeaseLive(0)
    second = queued.claim(now)
    assert second is not None
    assert second.attempts == 2
    assert second.fail(1, FailureKind.INTERNAL, TranscriptionProvider.FAKE, now) is None
    completed = second.complete(2, 10, TranscriptionProvider.FAKE, now)
    assert completed is not None
    assert isinstance(classify_claim_refusal(completed, now), AlreadyFinished)
    assert completed.complete(2, 10, TranscriptionProvider.FAKE, now) is None
    assert completed.requeue(2, now) is None
    failed = second.fail(2, FailureKind.INTERNAL, TranscriptionProvider.FAKE, now)
    assert failed is not None
    assert failed.claim(now) is None


def test_notification_deferral_and_fifth_attempt_crash_recovery() -> None:
    from taskbot.domain.messages import MessageRef
    from taskbot.domain.voice_jobs import (
        NothingToNotify,
        NotificationCeilingReached,
        NotificationNotDue,
        NotificationOutcome,
        VoiceJob,
        VoiceJobStatus,
        classify_notification_refusal,
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    job = VoiceJob(1, 2, MessageRef(2, 3), "file", "unique", 5, None, now, now, now)
    assert job.begin_notification(now) is None
    assert isinstance(classify_notification_refusal(job, now), NothingToNotify)
    job = replace(job, status=VoiceJobStatus.DONE, inbound_message_id=3)
    first = job.begin_notification(now)
    assert first is not None
    due = now + timedelta(seconds=902)
    deferred = first.defer_notification(due, now)
    assert deferred is not None
    assert deferred.begin_notification(now + timedelta(seconds=500)) is None
    assert classify_notification_refusal(deferred, now + timedelta(seconds=500)) == (
        NotificationNotDue(402)
    )
    shorter = deferred.defer_notification(now + timedelta(seconds=600), now)
    assert shorter is not None
    assert shorter.notify_not_before == due
    assert deferred.defer_notification(now - timedelta(seconds=1), now) is None
    job = deferred
    for attempt in range(2, 6):
        changed = job.begin_notification(due)
        assert changed is not None
        assert changed.notify_attempts == attempt
        job = changed
    assert job.begin_notification(due) is None
    assert isinstance(classify_notification_refusal(job, now), NotificationCeilingReached)
    finished = job.finish_notification(NotificationOutcome.GIVEN_UP, due)
    assert finished is not None
    assert finished.begin_notification(due) is None
    assert finished.finish_notification(NotificationOutcome.EDITED, due) is None
    assert finished.mark_enqueued(due) is None
