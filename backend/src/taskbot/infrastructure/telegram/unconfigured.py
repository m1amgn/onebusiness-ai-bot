from taskbot.domain.errors import NotificationError, VoiceFileUnavailableError


class UnconfiguredNotifier:
    async def send_reply(
        self, chat_id: int, text: str, *, reply_to_message_id: int | None = None
    ) -> int:
        raise NotificationError("Telegram is not configured")

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        raise NotificationError("Telegram is not configured")


class UnconfiguredVoiceSource:
    async def fetch(self, file_id: str, *, max_bytes: int) -> bytes:
        raise VoiceFileUnavailableError("Telegram is not configured")
