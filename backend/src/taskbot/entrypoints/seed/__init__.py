from dataclasses import dataclass, field

from taskbot.application.create_tasks import (
    CreateTasksCommand,
    CreateTasksFromMessage,
    DuplicateMessage,
)
from taskbot.application.dashboard_token import IssueDashboardToken, dashboard_link
from taskbot.application.publishing import publish_events
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.errors import DomainError
from taskbot.domain.events import DomainEvent
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.ports.messaging import EventPublisher
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import TaskExtractor
from taskbot.domain.users import TelegramIdentity
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.tokens import SecretsTokenGenerator, Sha256TokenHasher


@dataclass(frozen=True, slots=True)
class SeedPorts:
    create_tasks: CreateTasksFromMessage
    issue_token: IssueDashboardToken
    web_base_url: str
    publisher: EventPublisher
    register: RegisterOrGetUser
    uows: UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class SeedResult:
    user_id: int
    titles: tuple[str, ...]
    token: str = field(repr=False)
    link: str = field(repr=False)
    duplicate: bool
    events: tuple[DomainEvent, ...]


@dataclass(frozen=True, slots=True)
class KeptTokenSeedResult:
    user_id: int
    titles: tuple[str, ...]
    duplicate: bool
    events: tuple[DomainEvent, ...]


def build_seed_ports(
    settings: SeedSettings,
    uows: UnitOfWorkFactory,
    extractor: TaskExtractor,
    *,
    publisher: EventPublisher,
) -> SeedPorts:
    clock = SystemClock()
    register = RegisterOrGetUser(uows, clock)
    return SeedPorts(
        CreateTasksFromMessage(uows, extractor, register, clock),
        IssueDashboardToken(uows, register, SecretsTokenGenerator(), Sha256TokenHasher(), clock),
        settings.web_base_url,
        publisher,
        register,
        uows,
    )


async def seed(ports: SeedPorts, telegram_user_id: int, text: str, message_id: int) -> SeedResult:
    identity = TelegramIdentity(telegram_user_id, "Seed user")
    created = await ports.create_tasks.execute(
        CreateTasksCommand(
            identity, MessageRef(telegram_user_id, message_id), MessageKind.TEXT, text
        )
    )
    issued = await ports.issue_token.execute(identity)
    duplicate = isinstance(created, DuplicateMessage)
    titles = () if isinstance(created, DuplicateMessage) else tuple(t.title for t in created.tasks)
    events = () if isinstance(created, DuplicateMessage) else created.events
    committed_events = events + issued.events
    await publish_events(ports.publisher, committed_events)
    return SeedResult(
        user_id=issued.user_id,
        titles=titles,
        token=issued.token,
        link=dashboard_link(ports.web_base_url, issued.token),
        duplicate=duplicate,
        events=committed_events,
    )


async def seed_keep_token(
    ports: SeedPorts, telegram_user_id: int, text: str, message_id: int
) -> KeptTokenSeedResult:
    identity = TelegramIdentity(telegram_user_id, "Seed user")
    registered = await ports.register.execute(identity)
    user_id = registered.user.id
    async with ports.uows() as uow:
        token_version = await uow.users.get_token_version(user_id)
    if token_version is None:
        raise RuntimeError("Registered user disappeared")
    if token_version == 0:
        raise DomainError("This user has no dashboard link yet; run without --keep-token first.")
    created = await ports.create_tasks.execute(
        CreateTasksCommand(
            identity, MessageRef(telegram_user_id, message_id), MessageKind.TEXT, text
        )
    )
    if isinstance(created, DuplicateMessage):
        return KeptTokenSeedResult(user_id, (), True, ())
    await publish_events(ports.publisher, created.events)
    return KeptTokenSeedResult(
        user_id, tuple(task.title for task in created.tasks), False, created.events
    )
