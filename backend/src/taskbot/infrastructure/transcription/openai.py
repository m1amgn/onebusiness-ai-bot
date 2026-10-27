from __future__ import annotations

import asyncio
from json import JSONDecodeError
from typing import TYPE_CHECKING

from openai import APIConnectionError, APIStatusError, OpenAIError
from openai.types.audio import Transcription

from taskbot.domain.errors import TranscriptionError, TransientTranscriptionError
from taskbot.domain.ports.transcription import Transcript
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider
from taskbot.infrastructure.transcription.text import transcript_from_text

if TYPE_CHECKING:
    from openai import AsyncOpenAI

OPENAI_TIMEOUT_SECONDS = 30.0


def _status_error(error: APIStatusError) -> TranscriptionError | TransientTranscriptionError:
    status = error.status_code
    if (
        status in {401, 403, 404}
        or (status == 400 and error.param == "model")
        or (status == 429 and "insufficient_quota" in (error.code, error.type))
    ):
        return TranscriptionError(FailureKind.PROVIDER_UNAVAILABLE)
    if status in {400, 413, 415}:
        return TranscriptionError(FailureKind.UNSUPPORTED_AUDIO)
    if status in {408, 429} or status >= 500:
        return TransientTranscriptionError()
    return TranscriptionError(FailureKind.INTERNAL)


class OpenAITranscriber:
    def __init__(self, client: AsyncOpenAI, *, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def provider(self) -> TranscriptionProvider:
        return TranscriptionProvider.OPENAI

    async def transcribe(self, audio: bytes) -> Transcript:
        deadline = asyncio.timeout(OPENAI_TIMEOUT_SECONDS)
        try:
            async with deadline:
                result = await self._client.audio.transcriptions.create(
                    file=("voice.ogg", audio), model=self._model
                )
        except TimeoutError:
            if not deadline.expired():
                raise
            mapped: TranscriptionError | TransientTranscriptionError = TransientTranscriptionError()
        except APIConnectionError:
            mapped = TransientTranscriptionError()
        except APIStatusError as error:
            mapped = _status_error(error)
        except OpenAIError, JSONDecodeError:
            mapped = TranscriptionError(FailureKind.INTERNAL)
        else:
            if not isinstance(result, Transcription):
                raise TranscriptionError(FailureKind.INTERNAL)
            return transcript_from_text(getattr(result, "text", None), self.provider)
        raise mapped
