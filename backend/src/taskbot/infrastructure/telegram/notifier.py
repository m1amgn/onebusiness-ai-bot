from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import ReplyParameters

from taskbot.domain.errors import (
    DomainError,
    MessageGoneError,
    NotificationError,
    RecipientBlockedError,
    RetryAfterError,
)


def notification_error(error: Exception) -> DomainError:
    if isinstance(error, TelegramRetryAfter):
        return RetryAfterError(error.retry_after)
    if isinstance(error, TelegramForbiddenError):
        return RecipientBlockedError(type(error).__name__)
    return NotificationError(type(error).__name__)


class AiogramUserNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_reply(
        self, chat_id: int, text: str, *, reply_to_message_id: int | None = None
    ) -> int:
        reply = (
            ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
            if reply_to_message_id is not None
            else None
        )
        try:
            message = await self._bot.send_message(
                chat_id, text, reply_parameters=reply, parse_mode=None, request_timeout=10
            )
        except Exception as error:
            mapped = notification_error(error)
        else:
            return message.message_id
        raise mapped

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, parse_mode=None, request_timeout=10
            )
        except TelegramBadRequest as error:
            description = error.message.lower()
            if "message is not modified" in description:
                return
            if any(
                phrase in description
                for phrase in ("message to edit not found", "message can't be edited")
            ):
                mapped: DomainError = MessageGoneError(type(error).__name__)
            else:
                mapped = NotificationError(type(error).__name__)
        except Exception as error:
            mapped = notification_error(error)
        else:
            return
        raise mapped
