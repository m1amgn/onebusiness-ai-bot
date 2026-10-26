from datetime import UTC, datetime

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User, Voice
from pydantic import PostgresDsn, SecretStr

from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.bot import BotSettings
from tests.fakes.messaging import FakeEventPublisher, FakeJobQueue
from tests.fakes.persistence import FakeStore
from tests.fakes.telegram import FakeUserNotifier
from tests.fakes.telegram_session import FakeTelegramSession


def message_update(
    number: int,
    text: str | None,
    *,
    group: bool = False,
    voice: bool = False,
    caption: str | None = None,
) -> Update:
    return Update(
        update_id=number,
        message=Message(
            message_id=number,
            date=datetime.now(UTC),
            chat=Chat(id=7, type="group" if group else "private"),
            from_user=User(id=7, is_bot=False, first_name="Synthetic"),
            text=text,
            caption=caption,
            voice=Voice(file_id="file", file_unique_id="unique", duration=35) if voice else None,
        ),
    )


async def test_private_text_preserves_source_and_publishes_after_reply() -> None:
    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    store = FakeStore()
    notifier = FakeUserNotifier(calls=store.calls)
    publisher = FakeEventPublisher(calls=store.calls)
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    ports = build_bot_ports(
        settings, store, HeuristicExtractor(), notifier, FakeJobQueue(), publisher
    )
    dispatcher = create_dispatcher(ports, InFlightUpdates())
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
    assert [reply.text for reply in notifier.replies] == ["Task created: Buy milk"]
    assert len(store.state.messages) == len(store.state.tasks) == len(publisher.events) == 1
    event = publisher.events[0]
    assert event.type == "task.created"
    assert event.task.source_text == "Buy milk"
    assert store.calls.items.index("notifier.send_reply") < store.calls.items.index(
        "events.publish"
    )
    assert dispatcher.resolve_used_update_types() == ["message"]


@pytest.mark.parametrize(
    ("text", "group", "expected"),
    [
        ("/dashboard", True, "I work only in a private chat."),
        ("/start", True, "I work only in a private chat."),
        ("Buy milk", True, None),
        ("🙂👍", False, "There is no text to make a task from."),
        (
            "/help",
            False,
            "Unknown command. Send a text or a voice message to create a task; "
            "/dashboard opens your tasks.",
        ),
    ],
)
async def test_refusals_do_not_write_tasks(text: str, group: bool, expected: str | None) -> None:
    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    store = FakeStore()
    notifier = FakeUserNotifier()
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    ports = build_bot_ports(
        settings, store, HeuristicExtractor(), notifier, FakeJobQueue(), FakeEventPublisher()
    )
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        await create_dispatcher(ports, InFlightUpdates()).feed_update(
            bot, message_update(1, text, group=group)
        )
    assert [reply.text for reply in notifier.replies] == ([] if expected is None else [expected])
    assert not store.state.tasks
    assert not store.state.users


async def test_voice_caption_does_not_rotate_the_dashboard_token() -> None:
    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    store = FakeStore()
    notifier = FakeUserNotifier()
    queue = FakeJobQueue()
    publisher = FakeEventPublisher()
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    ports = build_bot_ports(settings, store, HeuristicExtractor(), notifier, queue, publisher)
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        await create_dispatcher(ports, InFlightUpdates()).feed_update(
            bot, message_update(1, None, voice=True, caption="/dashboard")
        )
    assert [reply.text for reply in notifier.replies] == ["Transcribing your voice message…"]
    assert len(queue.jobs) == len(store.state.voice_jobs) == 1
    assert store.state.users[1].token_version == 0
    assert not publisher.events


async def test_duplicate_after_failed_reply_still_has_one_task() -> None:
    from taskbot.domain.errors import NotificationError
    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    store = FakeStore()
    notifier = FakeUserNotifier(failures={1: NotificationError("synthetic reply failure")})
    publisher = FakeEventPublisher()
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    dispatcher = create_dispatcher(
        build_bot_ports(settings, store, HeuristicExtractor(), notifier, FakeJobQueue(), publisher),
        InFlightUpdates(),
    )
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
    assert len(store.state.tasks) == len(store.state.messages) == len(publisher.events) == 1
    assert notifier.count == 1


async def test_error_hook_swallows_its_own_error_without_logging_chat_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    store = FakeStore(fail_on="users.upsert")
    notifier = FakeUserNotifier(failures={1: RuntimeError("private notifier text")})
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    dispatcher = create_dispatcher(
        build_bot_ports(
            settings, store, HeuristicExtractor(), notifier, FakeJobQueue(), FakeEventPublisher()
        ),
        InFlightUpdates(),
    )
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        await dispatcher.feed_update(bot, message_update(1, "private update text"))
    assert "private notifier text" not in caplog.text
    assert "private update text" not in caplog.text
    assert notifier.count == 1
    assert not store.state.tasks


async def test_one_word_reaches_extractor_after_a_stalled_typing_action() -> None:
    import asyncio

    from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates
    from tests.fakes.extraction import FakeExtractor

    store = FakeStore()
    notifier = FakeUserNotifier()
    extractor = FakeExtractor(
        Extraction(
            task=TaskDraft(title="Extracted"), extractor=ExtractorName.HEURISTIC, degraded=False
        ),
        store.calls,
    )
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    dispatcher = create_dispatcher(
        build_bot_ports(settings, store, extractor, notifier, FakeJobQueue(), FakeEventPublisher()),
        InFlightUpdates(),
    )
    session = FakeTelegramSession()
    session.request_gates["sendChatAction"] = asyncio.Event()
    async with Bot("123456:synthetic", session=session) as bot:
        async with asyncio.timeout(5):
            await dispatcher.feed_update(bot, message_update(1, "Milk"))
    assert notifier.replies[0].text == "Task created: Extracted"
    assert store.calls.items.count("extract") == 1
    assert session.timeouts[0] == 2


@pytest.mark.parametrize("kind", ["missing_sender", "bot_sender", "service", "group_voice"])
async def test_sender_guards_and_service_updates_create_no_work(kind: str) -> None:
    from aiogram.types import MessageAutoDeleteTimerChanged

    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    store = FakeStore()
    notifier = FakeUserNotifier()
    queue = FakeJobQueue()
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    update = message_update(
        1,
        None if kind in {"service", "group_voice"} else "Buy milk",
        group=kind == "group_voice",
        voice=kind == "group_voice",
    )
    assert update.message is not None
    item = update.message
    if kind == "missing_sender":
        item = item.model_copy(update={"from_user": None})
    elif kind == "bot_sender":
        item = item.model_copy(
            update={"from_user": User(id=8, is_bot=True, first_name="Synthetic")}
        )
    elif kind == "service":
        item = item.model_copy(
            update={
                "message_auto_delete_timer_changed": MessageAutoDeleteTimerChanged(
                    message_auto_delete_time=10
                )
            }
        )
    update = update.model_copy(update={"message": item})
    dispatcher = create_dispatcher(
        build_bot_ports(
            settings, store, HeuristicExtractor(), notifier, queue, FakeEventPublisher()
        ),
        InFlightUpdates(),
    )
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        await dispatcher.feed_update(bot, update)
    assert not notifier.replies
    assert not store.state.users
    assert not store.state.voice_jobs
    assert not queue.jobs


async def test_dashboard_publishes_revocation_before_returning_the_private_link() -> None:
    from taskbot.entrypoints.bot import build_bot_ports
    from taskbot.entrypoints.bot.handlers import create_dispatcher
    from taskbot.entrypoints.bot.runtime import InFlightUpdates

    store = FakeStore()
    notifier = FakeUserNotifier(calls=store.calls)
    publisher = FakeEventPublisher(calls=store.calls)
    settings = BotSettings(
        _env_file=None,
        database_url=PostgresDsn("postgresql+asyncpg://localhost/test"),
        telegram_bot_token=SecretStr("123456:synthetic"),
    )
    dispatcher = create_dispatcher(
        build_bot_ports(settings, store, HeuristicExtractor(), notifier, FakeJobQueue(), publisher),
        InFlightUpdates(),
    )
    async with Bot("123456:synthetic", session=FakeTelegramSession()) as bot:
        await dispatcher.feed_update(bot, message_update(1, "/dashboard"))
    assert notifier.replies[0].text.startswith("Your task board: http://localhost:5173/#token=")
    assert store.state.users[1].token_version == 1
    assert store.calls.items.index("events.publish") < store.calls.items.index(
        "notifier.send_reply"
    )
    assert publisher.events[0].type == "auth.revoked"
