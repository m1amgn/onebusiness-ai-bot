import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace

import asyncpg
import httpx
import pytest
import pytest_asyncio
from pydantic import PostgresDsn, RedisDsn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.application.create_tasks import (
    CreateTasksCommand,
    CreateTasksFromMessage,
    DuplicateMessage,
)
from taskbot.application.dashboard_token import IssueDashboardToken
from taskbot.application.list_tasks import ListTasks
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.events import DomainEvent, TaskDeleted
from taskbot.domain.extraction import TaskDraft
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.ports.messaging import EventPublisher
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import Task, TaskStatus
from taskbot.domain.users import TelegramIdentity
from taskbot.entrypoints.api.app import create_app
from taskbot.entrypoints.api.container import ApiServices, open_api_services
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.subscription import subscribe_user_events
from taskbot.infrastructure.settings.api import ApiSettings
from taskbot.infrastructure.sqlalchemy.tasks import SqlAlchemyTaskRepository
from taskbot.infrastructure.tokens import SecretsTokenGenerator, Sha256TokenHasher
from tests.databases import raw_url
from tests.redis_sessions import RedisSession

IDENTITY = TelegramIdentity(8101, "Synthetic deletion user")


@dataclass(frozen=True)
class DeletionApi:
    services: ApiServices
    create: CreateTasksFromMessage
    command: CreateTasksCommand
    issue: IssueDashboardToken
    token: str
    user_id: int
    task_id: int
    message_id: int
    namespace: str


@pytest_asyncio.fixture
async def deletion_api(
    postgres_uows: UnitOfWorkFactory, database_url: str, redis_session: RedisSession
) -> AsyncIterator[DeletionApi]:
    clock = SystemClock()
    register = RegisterOrGetUser(postgres_uows, clock)
    create = CreateTasksFromMessage(postgres_uows, HeuristicExtractor(), register, clock)
    command = CreateTasksCommand(IDENTITY, MessageRef(8101, 1), MessageKind.TEXT, "Call home")
    created = await create.execute(command)
    assert not isinstance(created, DuplicateMessage)
    issue = IssueDashboardToken(
        postgres_uows, register, SecretsTokenGenerator(), Sha256TokenHasher(), clock
    )
    issued = await issue.execute(IDENTITY)
    namespace = redis_session.child()
    settings = ApiSettings(
        _env_file=None,
        database_url=PostgresDsn(database_url),
        redis_url=RedisDsn(redis_session.redis_url),
        redis_namespace=namespace,
    )
    async with open_api_services(settings) as services:
        yield DeletionApi(
            services,
            create,
            command,
            issue,
            issued.token,
            created.user_id,
            created.tasks[0].id,
            created.message.id,
            namespace,
        )


@asynccontextmanager
async def deletion_client(
    api: DeletionApi, services: ApiServices | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(services or api.services)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {api.token}"},
    ) as client:
        yield client


@dataclass
class CommittedDeletionPublisher:
    uows: UnitOfWorkFactory
    delegate: EventPublisher
    message_id: int
    events: list[DomainEvent] = field(default_factory=list)

    async def publish(self, event: DomainEvent) -> None:
        async with self.uows() as uow:
            assert await uow.tasks.list_for_message(self.message_id) == ()
            message = await uow.messages.get(self.message_id)
            assert message is not None
            assert message.text is None
        self.events.append(event)
        await self.delegate.publish(event)


async def test_delete_is_committed_before_redis_delivery_and_preserves_duplicate_guard(
    deletion_api: DeletionApi, postgres_uows: UnitOfWorkFactory, redis_session: RedisSession
) -> None:
    api = deletion_api
    observer = CommittedDeletionPublisher(postgres_uows, api.services.publisher, api.message_id)
    async with (
        create_redis_client(redis_session.redis_url) as reader,
        subscribe_user_events(reader, api.namespace, api.user_id) as subscription,
        deletion_client(api, replace(api.services, publisher=observer)) as client,
    ):
        async with asyncio.timeout(3):
            response = await client.delete(f"/api/tasks/{api.task_id}")
            assert response.status_code == 204
            assert response.content == b""
            assert "content-type" not in response.headers
            event = TaskDeleted(user_id=api.user_id, task_id=api.task_id)
            assert await subscription.next(1) == event.model_dump_json()
        assert (await client.get("/api/tasks")).json() == {"items": [], "next_cursor": None}
        assert (await client.delete(f"/api/tasks/{api.task_id}")).status_code == 404
    assert observer.events == [event]
    duplicate = await api.create.execute(api.command)
    assert isinstance(duplicate, DuplicateMessage)
    assert duplicate.message_id == api.message_id
    async with postgres_uows() as uow:
        assert await uow.tasks.list_for_message(api.message_id) == ()
        message = await uow.messages.get_by_ref(api.command.ref)
        assert message is not None
        assert message.text is None


async def test_foreign_delete_keeps_real_rows_and_text(deletion_api: DeletionApi) -> None:
    api = deletion_api
    foreign = await api.issue.execute(TelegramIdentity(8102, "Synthetic other user"))
    async with deletion_client(api) as client:
        response = await client.delete(
            f"/api/tasks/{api.task_id}", headers={"Authorization": f"Bearer {foreign.token}"}
        )
        assert response.status_code == 404
        page = (await client.get("/api/tasks")).json()
    assert page["items"][0]["id"] == api.task_id
    assert page["items"][0]["source_text"] == "Call home"


async def test_deleting_legacy_positions_erases_only_the_last_source(
    deletion_api: DeletionApi, postgres_uows: UnitOfWorkFactory
) -> None:
    api = deletion_api
    created = await api.create.execute(
        replace(api.command, ref=MessageRef(8101, 2), text="Legacy shared source")
    )
    assert not isinstance(created, DuplicateMessage)
    async with postgres_uows() as uow:
        await uow.tasks.delete(created.tasks[0].id)
        first, second = await uow.tasks.add_many(
            api.user_id,
            created.message.id,
            (TaskDraft(title="First"), TaskDraft(title="Second")),
            SystemClock().now(),
        )
        await uow.commit()
    async with deletion_client(api) as client:
        assert (await client.delete(f"/api/tasks/{first.id}")).status_code == 204
        async with postgres_uows() as uow:
            message = await uow.messages.get(created.message.id)
            assert message is not None
            assert message.text == "Legacy shared source"
            assert await uow.tasks.list_for_message(created.message.id) == (second,)
        assert (await client.delete(f"/api/tasks/{second.id}")).status_code == 204
        page = (await client.get("/api/tasks")).json()
    assert [task["id"] for task in page["items"]] == [api.task_id]
    assert page["items"][0]["source_text"] == "Call home"
    async with postgres_uows() as uow:
        message = await uow.messages.get(created.message.id)
        assert message is not None
        assert message.text is None


def pause_task_write(
    monkeypatch: pytest.MonkeyPatch, first: str, held: asyncio.Event, release: asyncio.Event
) -> None:
    original_delete = SqlAlchemyTaskRepository.delete
    original_save = SqlAlchemyTaskRepository.save_status

    async def held_delete(self: SqlAlchemyTaskRepository, task_id: int) -> None:
        await original_delete(self, task_id)
        held.set()
        await release.wait()

    async def held_save(self: SqlAlchemyTaskRepository, task: Task) -> Task | None:
        saved = await original_save(self, task)
        held.set()
        await release.wait()
        return saved

    if first == "delete":
        monkeypatch.setattr(SqlAlchemyTaskRepository, "delete", held_delete)
    else:
        monkeypatch.setattr(SqlAlchemyTaskRepository, "save_status", held_save)


async def wait_for_task_lock(
    monitor: asyncpg.Connection, waiting: asyncio.Task[httpx.Response]
) -> None:
    async with asyncio.timeout(2):
        while not await monitor.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
            "WHERE datname=current_database() AND wait_event_type='Lock')"
        ):
            assert not waiting.done()
            await asyncio.sleep(0.01)
    assert not waiting.done()


@pytest.mark.parametrize("first", ["delete", "patch"])
async def test_patch_and_delete_serialize_on_the_same_task(
    deletion_api: DeletionApi,
    database_url: str,
    postgres_uows: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    api = deletion_api
    held = asyncio.Event()
    release = asyncio.Event()
    pause_task_write(monkeypatch, first, held, release)
    monitor = await asyncpg.connect(raw_url(database_url))
    try:
        async with deletion_client(api) as client:
            operations = {
                "delete": lambda: client.delete(f"/api/tasks/{api.task_id}"),
                "patch": lambda: client.patch(
                    f"/api/tasks/{api.task_id}", json={"status": "completed"}
                ),
            }
            async with asyncio.timeout(5), asyncio.TaskGroup() as group:
                initial = group.create_task(operations[first]())
                try:
                    await held.wait()
                    second = "patch" if first == "delete" else "delete"
                    waiting = group.create_task(operations[second]())
                    await wait_for_task_lock(monitor, waiting)
                finally:
                    release.set()
            assert initial.result().status_code == (204 if first == "delete" else 200)
            assert waiting.result().status_code == (404 if first == "delete" else 204)
            assert (await client.get("/api/tasks")).json()["items"] == []
    finally:
        release.set()
        await monitor.close()
    async with postgres_uows() as uow:
        message = await uow.messages.get(api.message_id)
        assert message is not None
        assert message.text is None


async def test_sql_listing_and_status_refuse_erased_text_while_task_exists(
    deletion_api: DeletionApi,
    postgres_uows: UnitOfWorkFactory,
    sql_sessions: async_sessionmaker[AsyncSession],
) -> None:
    api = deletion_api
    async with sql_sessions() as session:
        await session.execute(
            text("UPDATE inbound_messages SET text=NULL WHERE id=:id"), {"id": api.message_id}
        )
        await session.commit()
    with pytest.raises(RuntimeError, match="Task has no source message text"):
        await ListTasks(postgres_uows).execute(api.user_id, status=None, after=None, limit=50)
    with pytest.raises(RuntimeError, match="Task has no source message text"):
        await ChangeTaskStatus(postgres_uows, SystemClock()).execute(
            api.user_id, api.task_id, TaskStatus.COMPLETED
        )
