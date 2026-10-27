from dataclasses import dataclass

from taskbot.application.accept_voice import AcceptVoiceMessage
from taskbot.application.create_tasks import CreateTasksFromMessage
from taskbot.application.dashboard_token import IssueDashboardToken
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.replies import START
from taskbot.domain.ports.messaging import EventPublisher, JobQueue
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import TaskExtractor
from taskbot.domain.ports.telegram import UserNotifier
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.extraction.factory import has_anthropic_key
from taskbot.infrastructure.settings.bot import BotSettings
from taskbot.infrastructure.settings.transcription import openai_transcription_unavailable
from taskbot.infrastructure.tokens import SecretsTokenGenerator, Sha256TokenHasher


@dataclass(frozen=True, slots=True)
class BotPorts:
    create_tasks: CreateTasksFromMessage
    accept_voice: AcceptVoiceMessage
    issue_token: IssueDashboardToken
    notifier: UserNotifier
    publisher: EventPublisher
    web_base_url: str
    start_message: str


def start_text(settings: BotSettings) -> str:
    provider = str(settings.transcription_provider)
    if openai_transcription_unavailable(settings):
        provider = "openai (no API key, voice messages are unavailable)"
    extractor: str = settings.task_extractor
    if extractor == "claude" and not has_anthropic_key(settings):
        extractor = "heuristic (no Anthropic API key)"
    return START.format(provider=provider, extractor=extractor)


def build_bot_ports(
    settings: BotSettings,
    uows: UnitOfWorkFactory,
    extractor: TaskExtractor,
    notifier: UserNotifier,
    queue: JobQueue,
    publisher: EventPublisher,
) -> BotPorts:
    clock = SystemClock()
    register = RegisterOrGetUser(uows, clock)
    return BotPorts(
        CreateTasksFromMessage(uows, extractor, register, clock),
        AcceptVoiceMessage(uows, register, notifier, queue, clock),
        IssueDashboardToken(uows, register, SecretsTokenGenerator(), Sha256TokenHasher(), clock),
        notifier,
        publisher,
        settings.web_base_url,
        start_text(settings),
    )
