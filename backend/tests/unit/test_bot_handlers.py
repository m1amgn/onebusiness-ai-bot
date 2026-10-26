import asyncio

import pytest
from aiogram.types import ErrorEvent, MessageAutoDeleteTimerChanged, User

from taskbot.application import replies
from taskbot.domain.errors import NotificationError
from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.entrypoints.bot.handlers import create_dispatcher, handle_error
from taskbot.entrypoints.bot.runtime import InFlightUpdates
from tests.bot import HandlerHarness, message_update
from tests.fakes.extraction import FakeExtractor
from tests.fakes.telegram_session import FakeTelegramSession


async def test_private_text_preserves_source_and_publishes_after_reply() -> None:
    h = HandlerHarness()
    dispatcher = h.dispatcher()
    async with h.bot() as bot:
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
    assert [reply.text for reply in h.notifier.replies] == ["Task created: Buy milk"]
    assert h.notifier.replies[0].reply_to_message_id == 1
    assert len(h.store.state.messages) == len(h.store.state.tasks) == len(h.publisher.events) == 1
    event = h.publisher.events[0]
    assert event.type == "task.created"
    assert event.task.source_text == "Buy milk"
    assert h.store.calls.items.index("notifier.send_reply") < h.store.calls.items.index(
        "events.publish"
    )
    assert dispatcher.resolve_used_update_types() == ["message"]


@pytest.mark.parametrize(
    ("text", "group", "expected"),
    [
        ("/dashboard", True, replies.NOT_PRIVATE),
        ("/start", True, replies.NOT_PRIVATE),
        ("/start@synthetic_bot", True, replies.NOT_PRIVATE),
        ("/dashboard@Synthetic_Bot", True, replies.NOT_PRIVATE),
        ("/start@other_bot", True, None),
        ("Buy milk", True, None),
        ("🙂👍", False, replies.EMPTY_TEXT),
        ("/help", False, replies.UNKNOWN_COMMAND),
    ],
)
async def test_refusals_do_not_write_tasks(text: str, group: bool, expected: str | None) -> None:
    h = HandlerHarness()
    async with h.bot() as bot:
        await h.dispatcher().feed_update(bot, message_update(1, text, group=group))
    assert [item.text for item in h.notifier.replies] == ([] if expected is None else [expected])
    if h.notifier.replies:
        assert h.notifier.replies[0].reply_to_message_id == 1
    assert not h.store.state.tasks
    assert not h.store.state.users


async def test_voice_caption_does_not_rotate_the_dashboard_token() -> None:
    h = HandlerHarness()
    async with h.bot() as bot:
        await h.dispatcher().feed_update(bot, message_update(1, voice=True, caption="/dashboard"))
    assert [item.text for item in h.notifier.replies] == [replies.VOICE_ACK]
    assert h.notifier.replies[0].reply_to_message_id == 1
    assert len(h.queue.jobs) == len(h.store.state.voice_jobs) == 1
    assert h.store.state.users[1].token_version == 0
    assert not h.publisher.events


async def test_duplicate_after_failed_reply_still_has_one_task() -> None:
    h = HandlerHarness()
    h.notifier.failures[1] = NotificationError("synthetic reply failure")
    dispatcher = h.dispatcher()
    async with h.bot() as bot:
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
        await dispatcher.feed_update(bot, message_update(1, "Buy milk"))
    assert len(h.store.state.tasks) == len(h.store.state.messages) == len(h.publisher.events) == 1
    assert h.notifier.count == 1


async def test_error_hook_swallows_its_own_error_without_logging_chat_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = HandlerHarness()
    h.store.fail_on = "users.upsert"
    h.notifier.failures[1] = RuntimeError("private notifier text")
    async with h.bot() as bot:
        await h.dispatcher().feed_update(bot, message_update(1, "private update text"))
    assert "private notifier text" not in caplog.text
    assert "private update text" not in caplog.text
    assert h.notifier.count == 1
    assert not h.store.state.tasks


@pytest.mark.parametrize("group", [False, True])
async def test_error_hook_replies_safely_without_a_traceback(
    group: bool, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = HandlerHarness()
    content = "private storage and update text"

    async def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError(content)

    # Inject a failing standalone use case while keeping the real dispatcher/error hook.
    ports = h.ports()
    monkeypatch.setattr(ports.create_tasks, "execute", fail)
    update = message_update(1, content, group=group)
    if group:
        try:
            raise RuntimeError(content)
        except RuntimeError as error:
            await handle_error(ErrorEvent(update=update, exception=error), ports)
    else:
        async with h.bot() as bot:
            await create_dispatcher(ports, InFlightUpdates()).feed_update(bot, update)
    assert [item.text for item in h.notifier.replies] == (
        [] if group else ["Something went wrong. Please try again."]
    )
    assert content not in caplog.text
    assert "Traceback" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


async def test_one_word_reaches_extractor_after_a_stalled_typing_action() -> None:
    h = HandlerHarness()
    h.extractor = FakeExtractor(
        Extraction(
            task=TaskDraft(title="Extracted"), extractor=ExtractorName.HEURISTIC, degraded=False
        ),
        h.store.calls,
    )
    session = FakeTelegramSession()
    session.request_gates["sendChatAction"] = asyncio.Event()
    async with h.bot(session) as bot:
        async with asyncio.timeout(5):
            await h.dispatcher().feed_update(bot, message_update(1, "Milk"))
    assert h.notifier.replies[0].text == "Task created: Extracted"
    assert h.store.calls.items.count("extract") == 1
    assert session.timeouts[0] == 2


async def test_typing_decode_failure_still_creates_the_task() -> None:
    h = HandlerHarness()
    session = FakeTelegramSession()
    session.request_errors["sendChatAction"] = UnicodeDecodeError(
        "utf-8", b"\xff", 0, 1, "synthetic response"
    )
    async with h.bot(session) as bot:
        await h.dispatcher().feed_update(bot, message_update(1, "Buy milk"))
    assert len(h.store.state.tasks) == len(h.publisher.events) == 1
    assert [item.text for item in h.notifier.replies] == ["Task created: Buy milk"]


async def test_cancelling_the_typing_action_still_propagates() -> None:
    h = HandlerHarness()
    session = FakeTelegramSession()
    session.request_gates["sendChatAction"] = asyncio.Event()
    async with h.bot(session) as bot:
        task = asyncio.create_task(h.dispatcher().feed_update(bot, message_update(1, "Buy milk")))
        try:
            async with asyncio.timeout(2):
                await session.started.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert not h.store.state.tasks
            assert not h.notifier.replies
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("kind", ["missing_sender", "bot_sender", "service", "group_voice"])
async def test_sender_guards_and_service_updates_create_no_work(kind: str) -> None:
    h = HandlerHarness()
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
    async with h.bot() as bot:
        await h.dispatcher().feed_update(bot, update.model_copy(update={"message": item}))
    assert not h.notifier.replies
    assert not h.store.state.users
    assert not h.store.state.voice_jobs
    assert not h.queue.jobs


async def test_dashboard_publishes_revocation_before_returning_the_private_link() -> None:
    h = HandlerHarness()
    async with h.bot() as bot:
        await h.dispatcher().feed_update(bot, message_update(1, "/dashboard"))
    assert h.notifier.replies[0].text.startswith("Your task board: http://localhost:5173/#token=")
    assert h.notifier.replies[0].reply_to_message_id == 1
    assert h.store.state.users[1].token_version == 1
    assert h.store.calls.items.index("events.publish") < h.store.calls.items.index(
        "notifier.send_reply"
    )
    assert h.publisher.events[0].type == "auth.revoked"
