from typing import Protocol


class UserNotifier(Protocol):
    async def send_reply(
        self, chat_id: int, text: str, *, reply_to_message_id: int | None = None
    ) -> int: ...

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None: ...


class VoiceFileSource(Protocol):
    async def fetch(self, file_id: str, *, max_bytes: int) -> bytes: ...
