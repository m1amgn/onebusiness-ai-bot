"""Canonical plain-text replies shared by bot and worker use cases."""

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
