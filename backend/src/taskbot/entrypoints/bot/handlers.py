import logging
from contextlib import suppress
from typing import assert_never

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import ErrorEvent, Message

from taskbot.application import replies
from taskbot.application.accept_voice import AcceptVoiceCommand, VoiceRefused
from taskbot.application.create_tasks import CreateTasksCommand, DuplicateMessage
from taskbot.application.dashboard_token import dashboard_link
from taskbot.application.publishing import publish_events
from taskbot.domain.errors import NOTIFIER_ERRORS, EmptyMessageError
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.users import TelegramIdentity
from taskbot.entrypoints.bot import BotPorts
from taskbot.entrypoints.bot.runtime import InFlightUpdates

logger = logging.getLogger(__name__)


async def reply(message: Message, ports: BotPorts, text: str) -> None:
    try:
        await ports.notifier.send_reply(
            message.chat.id, text, reply_to_message_id=message.message_id
        )
    except NOTIFIER_ERRORS as error:
        logger.warning("Telegram reply failed (%s: %s)", type(error).__name__, error)


def identity(message: Message) -> TelegramIdentity:
    if message.from_user is None:
        raise ValueError("A private message must have a sender")
    return TelegramIdentity(message.from_user.id, message.from_user.full_name)


async def ignore(message: Message) -> None:
    return


def ignored_sender(message: Message) -> bool:
    return message.from_user is None or message.from_user.is_bot


async def handle_nonprivate(message: Message, ports: BotPorts, bot: Bot) -> None:
    if message.text is not None and message.text.startswith("/"):
        command = message.text.split(maxsplit=1)[0]
        if (
            "@" in command
            and command.rsplit("@", 1)[1].casefold() != ((await bot.me()).username or "").casefold()
        ):
            return
        await reply(message, ports, replies.NOT_PRIVATE)


async def handle_start(message: Message, ports: BotPorts) -> None:
    await reply(message, ports, ports.start_message)


async def handle_dashboard(message: Message, ports: BotPorts) -> None:
    issued = await ports.issue_token.execute(identity(message))
    await publish_events(ports.publisher, issued.events)
    await reply(
        message,
        ports,
        replies.DASHBOARD.format(link=dashboard_link(ports.web_base_url, issued.token)),
    )


async def handle_unknown(message: Message, ports: BotPorts) -> None:
    await reply(message, ports, replies.UNKNOWN_COMMAND)


async def handle_text(message: Message, ports: BotPorts, bot: Bot) -> None:
    with suppress(Exception):
        await bot.send_chat_action(message.chat.id, "typing", request_timeout=2)
    try:
        created = await ports.create_tasks.execute(
            CreateTasksCommand(
                identity(message),
                MessageRef(message.chat.id, message.message_id),
                MessageKind.TEXT,
                message.text or "",
            )
        )
    except EmptyMessageError:
        await reply(message, ports, replies.EMPTY_TEXT)
        return
    if isinstance(created, DuplicateMessage):
        return
    text = replies.task_created_text(created.tasks[0].title, created.message.degraded)
    await reply(message, ports, text)
    await publish_events(ports.publisher, created.events)


async def handle_voice(message: Message, ports: BotPorts) -> None:
    voice = message.voice
    if voice is None:
        return
    result = await ports.accept_voice.execute(
        AcceptVoiceCommand(
            identity(message),
            MessageRef(message.chat.id, message.message_id),
            voice.file_id,
            voice.file_unique_id,
            voice.duration,
            voice.file_size,
        )
    )
    if isinstance(result, VoiceRefused):
        match result.reason:
            case "too_long":
                text = replies.VOICE_TOO_LONG
            case "too_large":
                text = replies.VOICE_TOO_LARGE
            case _:
                assert_never(result.reason)
        await reply(message, ports, text)


async def handle_unsupported(message: Message, ports: BotPorts) -> None:
    await reply(message, ports, replies.UNSUPPORTED_MESSAGE)


async def handle_error(event: ErrorEvent, ports: BotPorts) -> bool:
    try:
        logger.error(
            "Telegram update %s failed (%s)", event.update.update_id, type(event.exception).__name__
        )
        message = event.update.message
        if message is not None and message.chat.type == ChatType.PRIVATE:
            await reply(message, ports, replies.SOMETHING_WENT_WRONG)
    except Exception as error:
        logger.error("Telegram error reply failed (%s)", type(error).__name__)
    return True


def create_dispatcher(ports: BotPorts, in_flight: InFlightUpdates) -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True, ports=ports)
    dispatcher.update.outer_middleware(in_flight)
    router = Router()
    router.message.register(ignore, ignored_sender)
    router.message.register(handle_nonprivate, F.chat.type != ChatType.PRIVATE)
    router.message.register(handle_start, F.text, Command("start"))
    router.message.register(handle_dashboard, F.text, Command("dashboard"))
    router.message.register(handle_unknown, F.text.startswith("/"))
    router.message.register(handle_text, F.text)
    router.message.register(handle_voice, F.voice)
    router.message.register(
        handle_unsupported,
        F.content_type.in_(
            {
                "photo",
                "video",
                "animation",
                "sticker",
                "video_note",
                "audio",
                "document",
                "location",
                "contact",
                "poll",
                "venue",
                "dice",
            }
        ),
    )
    router.errors.register(handle_error)
    dispatcher.include_router(router)
    return dispatcher
