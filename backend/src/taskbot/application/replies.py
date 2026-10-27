"""Canonical plain-text replies shared by bot and worker use cases."""

from taskbot.domain.messages import InboundMessage
from taskbot.domain.tasks import Task
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider, VoiceJob, VoiceJobStatus

START = (
    "Send me a text or a voice message and I will turn it into a task.\n"
    "/dashboard – a private link to your task board.\n"  # noqa: RUF001 - Owner-approved copy.
    "Transcription: {provider}. Task extraction: {extractor}."
)
TASK_CREATED = "Task created: {title}"
DEGRADED_LINE = "The language model was unavailable, so the title is taken from your text."
EMPTY_TEXT = "There is no text to make a task from."
VOICE_ACK = "Transcribing your voice message…"
VOICE_TOO_LONG = "This voice message is longer than 2 minutes. Please send a shorter one."
VOICE_TOO_LARGE = "This voice message is larger than 10 MB. Please send a shorter one."
DASHBOARD = (
    "Your task board: {link}\n"
    "Keep this link private. A new /dashboard link signs out other devices."
)
NOT_PRIVATE = "I work only in a private chat."
UNKNOWN_COMMAND = (
    "Unknown command. Send a text or a voice message to create a task; /dashboard opens your tasks."
)
UNSUPPORTED_MESSAGE = "I understand text and voice messages only."
SOMETHING_WENT_WRONG = "Something went wrong. Please try again."
TASK_CREATED_NO_TITLE = "Task created."
RETRYING = "Taking longer than usual, retrying…"
VOICE_UNSUPPORTED = "I could not read this audio. Please send the voice message again."
VOICE_EMPTY = (
    "I could not hear any words in this voice message. "
    "Please speak closer to the microphone or type your task."
)
VOICE_TIMEOUT = "This recording took too long to process. Please send a shorter voice message."
VOICE_UNAVAILABLE = (
    "Transcription is unavailable right now. Your message was not lost: please type it as text."
)
VOICE_EXHAUSTED = (
    "I could not transcribe this voice message after several attempts. Please send it again later."
)
VOICE_FAILED = "Something went wrong while processing this voice message. Please send it again."
VOICE_FAILURE_TEXT = {
    FailureKind.TOO_LARGE: VOICE_TOO_LARGE,
    FailureKind.TOO_LONG: VOICE_TOO_LONG,
    FailureKind.UNSUPPORTED_AUDIO: VOICE_UNSUPPORTED,
    FailureKind.EMPTY_TRANSCRIPT: VOICE_EMPTY,
    FailureKind.TRANSCRIPTION_TIMEOUT: VOICE_TIMEOUT,
    FailureKind.PROVIDER_UNAVAILABLE: VOICE_UNAVAILABLE,
    FailureKind.TRANSIENT_EXHAUSTED: VOICE_EXHAUSTED,
    FailureKind.INTERNAL: VOICE_FAILED,
    FailureKind.STUCK: VOICE_FAILED,
}


def task_created_text(title: str, degraded: bool) -> str:
    text = TASK_CREATED.format(title=title)
    return text + "\n" + DEGRADED_LINE if degraded else text


def notification_text(row: VoiceJob, message: InboundMessage | None, task: Task | None) -> str:
    if row.status == VoiceJobStatus.FAILED and row.failure_kind is not None:
        return VOICE_FAILURE_TEXT[row.failure_kind]
    if row.status != VoiceJobStatus.DONE or message is None:
        raise ValueError("Notification requires a terminal job and its success message")
    if task is None:
        return TASK_CREATED_NO_TITLE + ("\n" + DEGRADED_LINE if message.degraded else "")
    title = task.title + (" (demo mode)" if row.provider == TranscriptionProvider.FAKE else "")
    return task_created_text(title, message.degraded)
