from dataclasses import dataclass, field

from taskbot.application.create_tasks import (
    CreateTasksCommand,
    CreateTasksFromMessage,
    DuplicateMessage,
)
from taskbot.application.dashboard_token import IssueDashboardToken
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.events import DomainEvent
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.users import TelegramIdentity
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.extraction.factory import build_extractor
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.tokens import SecretsTokenGenerator, Sha256TokenHasher


@dataclass(frozen=True, slots=True)
class SeedPorts:
    create_tasks: CreateTasksFromMessage
    issue_token: IssueDashboardToken
    web_base_url: str


@dataclass(frozen=True, slots=True)
class SeedResult:
    titles: tuple[str, ...]
    token: str = field(repr=False)
    link: str = field(repr=False)
    duplicate: bool
    events: tuple[DomainEvent, ...]


def build_seed_ports(settings: SeedSettings, uows: UnitOfWorkFactory) -> SeedPorts:
    clock = SystemClock()
    register = RegisterOrGetUser(uows, clock)
    return SeedPorts(
        CreateTasksFromMessage(uows, build_extractor(settings), register, clock),
        IssueDashboardToken(uows, register, SecretsTokenGenerator(), Sha256TokenHasher(), clock),
        settings.web_base_url,
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
    return SeedResult(
        titles=titles,
        token=issued.token,
        link=f"{ports.web_base_url}/#token={issued.token}",
        duplicate=duplicate,
        events=events + issued.events,
    )
