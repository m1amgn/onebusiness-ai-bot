import asyncio
from collections.abc import Buffer
from io import BytesIO

from aiogram import Bot
from aiogram.exceptions import AiogramError, TelegramBadRequest
from aiohttp import ClientError

from taskbot.domain.errors import VoiceFileTooLargeError, VoiceFileUnavailableError


class CappedBuffer(BytesIO):
    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self._max_bytes = max_bytes

    def write(self, data: Buffer, /) -> int:
        if self.tell() + memoryview(data).nbytes > self._max_bytes:
            raise VoiceFileTooLargeError("Voice file exceeds the byte limit")
        return super().write(data)


class AiogramVoiceFileSource:
    def __init__(self, bot: Bot, *, deadline_seconds: float = 30) -> None:
        self._bot = bot
        self._deadline_seconds = deadline_seconds

    async def fetch(self, file_id: str, *, max_bytes: int) -> bytes:
        try:
            async with asyncio.timeout(self._deadline_seconds):
                file = await self._bot.get_file(file_id, request_timeout=10)
                if file.file_size is not None and file.file_size > max_bytes:
                    raise VoiceFileTooLargeError("Voice file exceeds the byte limit")
                if file.file_path is None:
                    raise VoiceFileUnavailableError("Missing file path")
                with CappedBuffer(max_bytes) as buffer:
                    await self._bot.download_file(file.file_path, destination=buffer)
                    return buffer.getvalue()
        except TelegramBadRequest as error:
            if "file is too big" in error.message.lower():
                raise VoiceFileTooLargeError(type(error).__name__) from None
            raise VoiceFileUnavailableError(type(error).__name__) from None
        except (AiogramError, TimeoutError, ClientError) as error:
            raise VoiceFileUnavailableError(type(error).__name__) from None
