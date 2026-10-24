import json
from datetime import UTC, datetime, timedelta
from itertools import permutations, product

import pytest
from pydantic import ValidationError


def test_all_task_status_pairs_and_strict_clock_order() -> None:
    from taskbot.domain.tasks import Task, TaskStatus

    now = datetime(2026, 1, 1, tzinfo=UTC)
    for old, new in product(TaskStatus, repeat=2):
        task = Task(1, 2, 3, 0, "Call home", "", old, now, now)
        changed = task.change_status(new, now - timedelta(seconds=5))
        if old == new:
            assert changed is None
        else:
            assert changed is not None
            assert changed.status == new
            assert changed.updated_at == now + timedelta(microseconds=1)
            assert task.status == old
    task = Task(1, 2, 3, 0, "Call home", "", TaskStatus.PENDING, now, now)
    first = task.change_status(TaskStatus.IN_PROGRESS, now)
    assert first is not None
    second = first.change_status(TaskStatus.COMPLETED, now)
    assert second is not None
    for events in permutations((first, second)):
        held = task
        for event in events:
            if event.updated_at > held.updated_at:
                held = event
        assert held == second


def test_events_exclude_routing_identity_and_use_one_task_shape() -> None:
    from taskbot.domain.events import AuthRevoked, TaskCreated, TaskUpdated
    from taskbot.domain.tasks import Task, TaskStatus

    now = datetime(2026, 1, 1, tzinfo=UTC)
    task = Task(1, 2, 3, 0, "Call home", "", TaskStatus.PENDING, now, now)
    for cls, kind in ((TaskCreated, "task.created"), (TaskUpdated, "task.updated")):
        value = json.loads(cls(user_id=2, task=task.to_view()).model_dump_json())
        assert set(value) == {"type", "task"}
        assert value["type"] == kind
        assert set(value["task"]) == {
            "id",
            "inbound_message_id",
            "position",
            "title",
            "description",
            "status",
            "created_at",
            "updated_at",
        }
    assert json.loads(AuthRevoked(user_id=2).model_dump_json()) == {"type": "auth.revoked"}


def test_jobs_reject_unknown_payload_fields() -> None:
    from taskbot.domain.jobs import NotificationJob, TicketClaims, TranscriptionJob

    assert TranscriptionJob(voice_job_id=1).model_dump() == {"voice_job_id": 1}
    assert NotificationJob(voice_job_id=1, status="done").queue == "notification"
    assert TicketClaims(user_id=1, token_version=2).token_version == 2
    with pytest.raises(ValidationError, match="extra"):
        TranscriptionJob.model_validate({"voice_job_id": 1, "secret": "forbidden"})


def test_naive_task_clock_is_refused() -> None:
    from taskbot.domain.tasks import Task, TaskStatus

    now = datetime(2026, 1, 1, tzinfo=UTC)
    task = Task(1, 2, 3, 0, "Call home", "", TaskStatus.PENDING, now, now)
    with pytest.raises(ValueError, match="timezone-aware"):
        task.change_status(TaskStatus.COMPLETED, now.replace(tzinfo=None))


def test_retry_schedule_rejects_invalid_attempts() -> None:
    from taskbot.domain.limits import retry_delay_seconds

    assert [retry_delay_seconds(n) for n in (1, 2, 3, 4)] == [2, 4, 8, 16]
    with pytest.raises(ValueError, match="positive"):
        retry_delay_seconds(0)
