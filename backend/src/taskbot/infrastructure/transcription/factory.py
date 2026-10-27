from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from taskbot.domain.ports.transcription import Transcriber
from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.infrastructure.settings.transcription import openai_transcription_unavailable
from taskbot.infrastructure.transcription.fake import FakeTranscriber, UnavailableTranscriber

if TYPE_CHECKING:
    from taskbot.infrastructure.settings.transcription import TranscriberSettingsMixin

logger = logging.getLogger(__name__)


def _quiet_http_loggers() -> None:
    # SDK import can apply OPENAI_LOG; keep these levels after each selected import.
    for name in ("openai", "httpx2", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


@asynccontextmanager
async def open_transcriber(settings: TranscriberSettingsMixin) -> AsyncIterator[Transcriber]:
    if settings.transcription_provider == TranscriptionProvider.FAKE:
        yield FakeTranscriber()
        return
    if openai_transcription_unavailable(settings):
        logger.warning("OpenAI transcription is unavailable: missing API key")
        yield UnavailableTranscriber()
        return
    if settings.transcription_provider == TranscriptionProvider.OPENAI:
        import openai

        from taskbot.infrastructure.transcription.openai import (
            OPENAI_TIMEOUT_SECONDS,
            OpenAITranscriber,
        )

        _quiet_http_loggers()
        assert settings.openai_api_key is not None
        async with openai.AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value().strip(),
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=OPENAI_TIMEOUT_SECONDS,
            http_client=openai.DefaultAsyncHttpxClient(follow_redirects=False),
        ) as openai_client:
            yield OpenAITranscriber(openai_client, model=settings.openai_transcription_model)
        return

    import httpx

    from taskbot.infrastructure.transcription.whisper_server import (
        WHISPER_TIMEOUT_SECONDS,
        WhisperServerTranscriber,
    )

    _quiet_http_loggers()
    async with httpx.AsyncClient(
        base_url=str(settings.whisper_server_url),
        timeout=httpx.Timeout(connect=5, read=WHISPER_TIMEOUT_SECONDS, write=60, pool=5),
        follow_redirects=False,
    ) as whisper_client:
        yield WhisperServerTranscriber(whisper_client)
