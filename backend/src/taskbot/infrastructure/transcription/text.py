from taskbot.domain.errors import EmptyMessageError, TranscriptionError
from taskbot.domain.ports.transcription import Transcript
from taskbot.domain.text import MessageText
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider


def transcript_from_text(raw: object, provider: TranscriptionProvider) -> Transcript:
    if not isinstance(raw, str):
        raise TranscriptionError(FailureKind.INTERNAL)
    try:
        text = MessageText.parse(raw)
    except EmptyMessageError:
        raise TranscriptionError(FailureKind.EMPTY_TRANSCRIPT) from None
    return Transcript(text.value, provider)
