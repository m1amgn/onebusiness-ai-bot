from datetime import UTC, datetime, timedelta

import pytest

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.application.create_tasks import (
    CreateTasksCommand,
    CreateTasksFromMessage,
    DuplicateMessage,
)
from taskbot.application.dashboard_token import AuthenticateToken, IssueDashboardToken
from taskbot.application.list_tasks import ListTasks
from taskbot.application.publishing import publish_events
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.errors import EmptyMessageError, InvalidTokenError, TaskNotFoundError
from taskbot.domain.events import AuthRevoked, DomainEvent, TaskView
from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.tasks import TaskStatus
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity
from tests.contract.helpers import all_tasks
from tests.fakes.calls import Calls
from tests.fakes.clock import FakeClock
from tests.fakes.extraction import FakeExtractor
from tests.fakes.persistence import FakeStore
from tests.fakes.tokens import FakeTokenGenerator, FakeTokenHasher


async def test_creation_duplicate_source_and_list_order() -> None:

    calls = Calls()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore(calls=calls)
    extractor = FakeExtractor(
        Extraction(
            task=TaskDraft(title="first"),
            extractor=ExtractorName.HEURISTIC,
            degraded=False,
        ),
        calls,
    )
    register = RegisterOrGetUser(store, clock)
    create = CreateTasksFromMessage(store, extractor, register, clock)
    command = CreateTasksCommand(
        TelegramIdentity(7, "A"), MessageRef(7, 1), MessageKind.TEXT, "one task"
    )
    first = await create.execute(command)
    assert not isinstance(first, DuplicateMessage)
    assert calls.items.index("extract") < calls.items.index("uow.open")
    assert len(first.events) == 1
    assert first.events[0].type == "task.created"
    assert first.events[0].task.source_text == "one task"
    assert [t.position for t in first.tasks] == [0]
    again = await create.execute(command)
    assert isinstance(again, DuplicateMessage)
    assert again.message_id == first.message.id
    assert not hasattr(again, "events")
    newer = await create.execute(
        CreateTasksCommand(command.identity, MessageRef(7, 2), command.kind, "new")
    )
    assert not isinstance(newer, DuplicateMessage)
    listing = await ListTasks(store).execute(first.user_id, status=None, after=None, limit=50)
    tasks = tuple(item.task for item in listing.items)
    assert [t.id for t in tasks] == [t.id for t in newer.tasks + first.tasks]


async def test_status_changes_keep_source_ownership_and_event_order() -> None:
    from tests.contract.helpers import create_message

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore()
    async with store() as uow:
        mid = await create_message(uow, clock.now())
        (initial,) = await uow.tasks.add_many(1, mid, (TaskDraft(title="Task"),), clock.now())
        await uow.commit()
    change = ChangeTaskStatus(store, clock)
    a = await change.execute(1, initial.id, TaskStatus.IN_PROGRESS)
    b = await change.execute(1, initial.id, TaskStatus.COMPLETED)
    assert a.changed
    assert b.changed
    assert len(a.events) == len(b.events) == 1
    assert a.task.updated_at == clock.now() + timedelta(microseconds=1)
    assert b.task.updated_at == clock.now() + timedelta(microseconds=2)
    same = await change.execute(1, b.task.id, TaskStatus.COMPLETED)
    assert a.view.source_text == same.view.source_text == "Call home"
    assert not same.changed
    assert same.events == ()
    assert same.task.updated_at == b.task.updated_at
    again_listing = await ListTasks(store).execute(1, status=None, after=None, limit=50)
    assert [item.task.id for item in again_listing.items] == [initial.id]
    assert (await ListTasks(store).execute(999, status=None, after=None, limit=50)).items == ()
    with pytest.raises(TaskNotFoundError):
        await change.execute(999, b.task.id, TaskStatus.PENDING)
    held = TaskView.from_task(initial, "Call home")
    for event in (b.events[0], a.events[0]):
        if event.type == "task.updated" and event.task.updated_at > held.updated_at:
            held = event.task
    assert held.status == TaskStatus.COMPLETED


async def test_status_change_reads_an_advanced_clock() -> None:
    from tests.contract.helpers import create_message

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore()
    async with store() as uow:
        mid = await create_message(uow, clock.now())
        tasks = await uow.tasks.add_many(1, mid, (TaskDraft(title="Call home"),), clock.now())
        await uow.commit()
    clock.advance(10)
    ahead = await ChangeTaskStatus(store, clock).execute(1, tasks[0].id, TaskStatus.COMPLETED)
    assert ahead.task.updated_at == clock.now()


async def test_disappearance_after_lock_is_an_invariant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskbot.domain.tasks import Task
    from tests.contract.helpers import create_message
    from tests.fakes.persistence import FakeTaskRepository

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore()
    async with store() as uow:
        mid = await create_message(uow, clock.now())
        tasks = await uow.tasks.add_many(1, mid, (TaskDraft(title="Call home"),), clock.now())
        await uow.commit()

    async def missing(self: FakeTaskRepository, task: Task) -> Task | None:
        return None

    monkeypatch.setattr(FakeTaskRepository, "save_status", missing)
    with pytest.raises(RuntimeError, match="disappeared while locked"):
        await ChangeTaskStatus(store, clock).execute(1, tasks[0].id, TaskStatus.COMPLETED)


async def test_empty_text_never_extracts_or_opens_transaction() -> None:

    calls = Calls()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore(calls=calls)
    extractor = FakeExtractor(
        Extraction(task=TaskDraft(title="x"), extractor=ExtractorName.HEURISTIC, degraded=False),
        calls,
    )
    create = CreateTasksFromMessage(store, extractor, RegisterOrGetUser(store, clock), clock)
    with pytest.raises(EmptyMessageError):
        await create.execute(
            CreateTasksCommand(TelegramIdentity(7, "A"), MessageRef(7, 1), MessageKind.TEXT, "👍")
        )
    assert calls.items == []


async def test_composed_creation_returns_no_events_and_rolls_back_on_failure() -> None:

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore()
    extraction = Extraction(
        task=TaskDraft(title="Call home"), extractor=ExtractorName.HEURISTIC, degraded=False
    )
    create = CreateTasksFromMessage(
        store, FakeExtractor(extraction, store.calls), RegisterOrGetUser(store, clock), clock
    )

    async def failing_unit() -> None:
        async with store() as uow:
            user = await uow.users.upsert(TelegramIdentity(7, "A"), clock.now())
            result = await create.within(
                uow,
                user_id=user.user.id,
                ref=MessageRef(7, 1),
                kind=MessageKind.VOICE,
                text=MessageText.parse("Call home"),
                extraction=extraction,
            )
            assert not hasattr(result, "events")
            raise RuntimeError("forced")

    with pytest.raises(RuntimeError, match="forced"):
        await failing_unit()
    async with store() as uow:
        assert await uow.messages.get_by_ref(MessageRef(7, 1)) is None
        assert await uow.users.get(1) is None


async def test_token_rotation_rejects_old_and_malformed_tokens() -> None:

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore()
    issue = IssueDashboardToken(
        store, RegisterOrGetUser(store, clock), FakeTokenGenerator(), FakeTokenHasher(), clock
    )
    authenticate = AuthenticateToken(store, FakeTokenHasher())
    first = await issue.execute(TelegramIdentity(7, "A"))
    assert (await authenticate.execute(first.token)).token_version == 1
    second = await issue.execute(TelegramIdentity(7, "A"))
    assert second.user_id == first.user_id
    assert second.token_version == 2
    assert first.token not in repr(first)
    assert [e.type for e in second.events] == ["auth.revoked"]
    with pytest.raises(InvalidTokenError):
        await authenticate.execute(first.token)
    assert (await authenticate.execute(second.token)).token_version == 2
    before = list(store.calls.items)

    class TrapHasher:
        def hash(self, token: str) -> str:
            raise AssertionError("malformed token reached the hasher")

    malformed = AuthenticateToken(store, TrapHasher())
    for bad in ("non-ascii-界", "x" * 129):
        with pytest.raises(InvalidTokenError):
            await malformed.execute(bad)
    assert store.calls.items == before


async def test_publishing_stops_after_first_failure(caplog: pytest.LogCaptureFixture) -> None:

    delivered: list[int] = []

    class Publisher:
        async def publish(self, event: DomainEvent) -> None:
            delivered.append(event.user_id)
            if event.user_id == 1:
                raise RuntimeError("publisher down")

    await publish_events(Publisher(), tuple(AuthRevoked(user_id=i) for i in (1, 2, 3)))
    assert delivered == [1]
    assert len(caplog.records) == 1
    assert "skipped 2" in caplog.records[0].getMessage()


@pytest.mark.parametrize("failure", ["messages.add", "tasks.add_many", "commit"])
async def test_standalone_creation_rolls_back_failed_writes(failure: str) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore(fail_on=failure)
    extraction = Extraction(
        task=TaskDraft(title="Call home"), extractor=ExtractorName.HEURISTIC, degraded=False
    )
    create = CreateTasksFromMessage(
        store, FakeExtractor(extraction, store.calls), RegisterOrGetUser(store, clock), clock
    )
    command = CreateTasksCommand(
        TelegramIdentity(7, "A"), MessageRef(7, 1), MessageKind.TEXT, "Call home"
    )
    with pytest.raises(RuntimeError, match="forced"):
        await create.execute(command)
    async with store() as uow:
        assert await uow.users.get(1) is None
        assert await uow.messages.get_by_ref(command.ref) is None
        assert await all_tasks(uow, 1) == ()
