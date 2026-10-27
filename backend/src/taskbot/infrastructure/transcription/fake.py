from taskbot.domain.errors import TranscriptionError
from taskbot.domain.ports.transcription import Transcript
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider

DEMO_TRANSCRIPT = (
    "[demo transcript] Please remind me to call the supplier tomorrow morning about the invoice."
)


class FakeTranscriber:
    @property
    def provider(self) -> TranscriptionProvider:
        return TranscriptionProvider.FAKE

    async def transcribe(self, audio: bytes) -> Transcript:
        return Transcript(DEMO_TRANSCRIPT, self.provider)


class UnavailableTranscriber:
    @property
    def provider(self) -> TranscriptionProvider:
        return TranscriptionProvider.OPENAI

    async def transcribe(self, audio: bytes) -> Transcript:
        raise TranscriptionError(FailureKind.PROVIDER_UNAVAILABLE)
