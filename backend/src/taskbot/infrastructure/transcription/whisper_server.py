from __future__ import annotations

import asyncio
import re
from json import JSONDecodeError

import httpx

from taskbot.domain.errors import TranscriptionError, TransientTranscriptionError
from taskbot.domain.ports.transcription import Transcript
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider
from taskbot.infrastructure.transcription.text import transcript_from_text

WHISPER_TIMEOUT_SECONDS = 300.0
_NON_SPEECH_SPANS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_MISSING_FILE_REASONS = ("file not found", "file is required", "missing file", "file required")
_TRANSIENT_ERRORS = (
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.WriteTimeout,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ProtocolError,
)


def _status_error(response: httpx.Response) -> TranscriptionError | TransientTranscriptionError:
    status = response.status_code
    body = response.text.lower()
    if status == 400 and any(reason in body for reason in _MISSING_FILE_REASONS):
        return TranscriptionError(FailureKind.INTERNAL)
    if status in {400, 413, 415} or (status == 500 and "ffmpeg conversion failed" in body):
        return TranscriptionError(FailureKind.UNSUPPORTED_AUDIO)
    if status == 499 or status >= 500:
        return TransientTranscriptionError()
    return TranscriptionError(FailureKind.INTERNAL)


def _transcript(response: httpx.Response) -> Transcript:
    try:
        body: object = response.json()
    except JSONDecodeError, UnicodeDecodeError:
        raise TranscriptionError(FailureKind.INTERNAL) from None
    text = body.get("text") if isinstance(body, dict) else None
    transcript = transcript_from_text(text, TranscriptionProvider.WHISPER_SERVER)
    if not any(character.isalnum() for character in _NON_SPEECH_SPANS.sub("", transcript.text)):
        raise TranscriptionError(FailureKind.EMPTY_TRANSCRIPT)
    return transcript


class WhisperServerTranscriber:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def provider(self) -> TranscriptionProvider:
        return TranscriptionProvider.WHISPER_SERVER

    async def transcribe(self, audio: bytes) -> Transcript:
        deadline = asyncio.timeout(WHISPER_TIMEOUT_SECONDS)
        try:
            async with deadline:
                response = await self._client.post(
                    "/inference",
                    files={"file": ("voice.ogg", audio, "audio/ogg")},
                    data={"response_format": "json", "temperature": "0"},
                )
        except TimeoutError:
            if not deadline.expired():
                raise
            mapped: TranscriptionError | TransientTranscriptionError = TranscriptionError(
                FailureKind.TRANSCRIPTION_TIMEOUT
            )
        except httpx.ReadTimeout:
            mapped = TranscriptionError(FailureKind.TRANSCRIPTION_TIMEOUT)
        except _TRANSIENT_ERRORS:
            mapped = TransientTranscriptionError()
        except httpx.HTTPError:
            mapped = TranscriptionError(FailureKind.INTERNAL)
        else:
            if response.status_code == 200:
                return _transcript(response)
            mapped = _status_error(response)
        raise mapped
