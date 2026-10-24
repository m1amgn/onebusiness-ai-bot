from datetime import UTC, datetime

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
from taskbot.domain.events import AuthRevoked, DomainEvent
from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.tasks import TaskStatus
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity
from tests.fakes.calls import Calls
from tests.fakes.clock import FakeClock
from tests.fakes.extraction import FakeExtractor
from tests.fakes.persistence import FakeStore
from tests.fakes.tokens import FakeTokenGenerator, FakeTokenHasher


async def test_create_duplicate_order_status_and_ownership() -> None:

    calls = Calls()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore(calls=calls)
    extractor = FakeExtractor(
        Extraction(
            tasks=(TaskDraft(title="first"), TaskDraft(title="second")),
            extractor=ExtractorName.HEURISTIC,
            degraded=False,
        ),
        calls,
    )
    register = RegisterOrGetUser(store, clock)
    create = CreateTasksFromMessage(store, extractor, register, clock)
    command = CreateTasksCommand(
        TelegramIdentity(7, "A"), MessageRef(7, 1), MessageKind.TEXT, "two tasks"
    )
    first = await create.execute(command)
    assert not isinstance(first, DuplicateMessage)
    assert calls.items.index("extract") < calls.items.index("uow.open")
    assert len(first.events) == 2
    assert [t.position for t in first.tasks] == [0, 1]
    again = await create.execute(command)
    assert isinstance(again, DuplicateMessage)
    assert again.message_id == first.message.id
    assert not hasattr(again, "events")
    newer = await create.execute(
        CreateTasksCommand(command.identity, MessageRef(7, 2), command.kind, "new")
    )
    assert not isinstance(newer, DuplicateMessage)
    tasks = await ListTasks(store).execute(first.user_id)
    assert [t.id for t in tasks] == [t.id for t in newer.tasks + first.tasks]
    change = ChangeTaskStatus(store, clock)
    a = await change.execute(first.user_id, first.tasks[0].id, TaskStatus.IN_PROGRESS)
    b = await change.execute(first.user_id, first.tasks[0].id, TaskStatus.COMPLETED)
    assert a.changed
    assert b.changed
    assert len(a.events) == len(b.events) == 1
    assert b.task.updated_at > a.task.updated_at
    same = await change.execute(first.user_id, b.task.id, TaskStatus.COMPLETED)
    assert not same.changed
    assert same.events == ()
    assert same.task.updated_at == b.task.updated_at
    assert [t.id for t in await ListTasks(store).execute(first.user_id)] == [t.id for t in tasks]
    assert await ListTasks(store).execute(999) == ()
    with pytest.raises(TaskNotFoundError):
        await change.execute(999, b.task.id, TaskStatus.PENDING)
    held = first.tasks[0].to_view()
    for event in (b.events[0], a.events[0]):
        if event.type == "task.updated" and event.task.updated_at > held.updated_at:
            held = event.task
    assert held.status == TaskStatus.COMPLETED


async def test_empty_text_never_extracts_or_opens_transaction() -> None:

    calls = Calls()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore(calls=calls)
    extractor = FakeExtractor(
        Extraction(
            tasks=(TaskDraft(title="x"),), extractor=ExtractorName.HEURISTIC, degraded=False
        ),
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
        tasks=(TaskDraft(title="Call home"),), extractor=ExtractorName.HEURISTIC, degraded=False
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


async def test_publishing_continues_after_failure() -> None:

    delivered: list[int] = []

    class Publisher:
        async def publish(self, event: DomainEvent) -> None:
            delivered.append(event.user_id)
            if event.user_id == 1:
                raise RuntimeError("publisher down")

    await publish_events(Publisher(), (AuthRevoked(user_id=1), AuthRevoked(user_id=2)))
    assert delivered == [1, 2]


@pytest.mark.parametrize("failure", ["messages.add", "tasks.add_many", "commit"])
async def test_standalone_creation_rolls_back_failed_writes(failure: str) -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    store = FakeStore(fail_on=failure)
    extraction = Extraction(
        tasks=(TaskDraft(title="Call home"),), extractor=ExtractorName.HEURISTIC, degraded=False
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
        assert await uow.tasks.list_for_user(1) == ()
