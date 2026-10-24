from dataclasses import dataclass
from typing import Protocol

from taskbot.domain.voice_jobs import TranscriptionProvider


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    provider: TranscriptionProvider


class Transcriber(Protocol):
    @property
    def provider(self) -> TranscriptionProvider: ...

    async def transcribe(self, audio: bytes, *, timeout_seconds: int) -> Transcript: ...
