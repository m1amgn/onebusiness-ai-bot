MAX_TASKS_PER_MESSAGE = 10
TITLE_MAX = 120
DESCRIPTION_MAX = 2000
MAX_TRANSCRIPTION_ATTEMPTS = 5
MAX_NOTIFY_ATTEMPTS = 5
LEASE_SECONDS = 450
SWEEP_STALE_SECONDS = 460
SWEEP_UNNOTIFIED_SECONDS = 120
MAX_TOKEN_LENGTH = 128


def retry_delay_seconds(attempt: int) -> int:
    if attempt < 1:
        raise ValueError("attempt must be positive")
    return 2 << (attempt - 1)
