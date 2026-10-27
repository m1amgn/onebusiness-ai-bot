"""Shared synthetic bot inputs and handler/voice fixtures; no external I/O."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, MessageOriginHiddenUser, Sticker, Update, User, Voice
from pydantic import PostgresDsn, SecretStr

from taskbot.application.accept_voice import AcceptVoiceCommand, AcceptVoiceMessage
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.messages import MessageRef
from taskbot.domain.ports.services import TaskExtractor
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.entrypoints.bot import BotPorts, build_bot_ports
from taskbot.entrypoints.bot.handlers import create_dispatcher
from taskbot.entrypoints.bot.runtime import InFlightUpdates
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.bot import BotSettings
from tests.fakes.clock import FakeClock
from tests.fakes.messaging import FakeEventPublisher, FakeJobQueue
from tests.fakes.persistence import FakeStore
from tests.fakes.telegram import FakeUserNotifier
from tests.fakes.telegram_session import FakeTelegramSession

TOKEN = "123456:synthetic"


def bot_settings(*, keyless: bool = False) -> BotSettings:
    return BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/synthetic"),
        telegram_bot_token=SecretStr(TOKEN),
        transcription_provider=TranscriptionProvider.OPENAI
        if keyless
        else TranscriptionProvider.WHISPER_SERVER,
        task_extractor="claude" if keyless else "heuristic",
        anthropic_api_key=None,
        openai_api_key=None,
        web_base_url="http://localhost:5173",
    )


def message_update(
    number: int,
    text: str | None = None,
    *,
    group: bool = False,
    voice: bool = False,
    duration: int | None = None,
    size: int | None = 100,
    caption: str | None = None,
    sticker: bool = False,
    forwarded: bool = False,
) -> Update:
    return Update(
        update_id=number,
        message=Message(
            message_id=number,
            date=datetime(2026, 1, 1, tzinfo=UTC),
            chat=Chat(id=7, type="group" if group else "private"),
            from_user=User(id=7, is_bot=False, first_name="Synthetic"),
            text=text,
            caption=caption,
            voice=Voice(
                file_id="file",
                file_unique_id="unique",
                duration=duration if duration is not None else 35,
                file_size=size,
            )
            if voice or duration is not None
            else None,
            sticker=Sticker(
                file_id="sticker",
                file_unique_id="unique",
                type="regular",
                width=10,
                height=10,
                is_animated=False,
                is_video=False,
            )
            if sticker
            else None,
            forward_origin=MessageOriginHiddenUser(
                date=datetime(2026, 1, 1, tzinfo=UTC), sender_user_name="Synthetic"
            )
            if forwarded
            else None,
        ),
    )


def voice_fixture() -> tuple[FakeStore, FakeClock, FakeJobQueue]:
    store = FakeStore()
    return store, FakeClock(datetime(2026, 1, 1, tzinfo=UTC)), FakeJobQueue(store.calls)


@dataclass
class HandlerHarness:
    settings: BotSettings = field(default_factory=bot_settings)
    store: FakeStore = field(default_factory=FakeStore)
    extractor: TaskExtractor = field(default_factory=HeuristicExtractor)
    notifier: FakeUserNotifier = field(init=False)
    queue: FakeJobQueue = field(init=False)
    publisher: FakeEventPublisher = field(init=False)

    def __post_init__(self) -> None:
        self.notifier = FakeUserNotifier(calls=self.store.calls)
        self.queue = FakeJobQueue(calls=self.store.calls)
        self.publisher = FakeEventPublisher(calls=self.store.calls)

    def bot(self, session: FakeTelegramSession | None = None) -> Bot:
        return Bot(TOKEN, session=session if session is not None else FakeTelegramSession())

    def ports(self) -> BotPorts:
        return build_bot_ports(
            self.settings, self.store, self.extractor, self.notifier, self.queue, self.publisher
        )

    def dispatcher(self) -> Dispatcher:
        return create_dispatcher(self.ports(), InFlightUpdates())


def voice_use_case(
    store: FakeStore, clock: FakeClock, queue: FakeJobQueue, notifier: FakeUserNotifier
) -> AcceptVoiceMessage:
    return AcceptVoiceMessage(store, RegisterOrGetUser(store, clock), notifier, queue, clock)


def voice_command(duration: int = 1, size: int | None = None) -> AcceptVoiceCommand:
    return AcceptVoiceCommand(
        TelegramIdentity(7, "Synthetic"), MessageRef(7, 1), "file", "unique", duration, size
    )
