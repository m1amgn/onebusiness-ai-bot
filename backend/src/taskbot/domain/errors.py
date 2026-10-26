from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from taskbot.domain.voice_jobs import FailureKind


class DomainError(Exception):
    pass


class EmptyMessageError(DomainError):
    pass


class TaskNotFoundError(DomainError):
    pass


class InvalidTokenError(DomainError):
    pass


class InvalidTicketError(DomainError):
    pass


class StaleAttemptError(DomainError):
    pass


class EnqueueError(DomainError):
    pass


class TranscriptionError(DomainError):
    def __init__(self, kind: FailureKind) -> None:
        self.kind = kind
        super().__init__(kind.value)


class VoiceFileTooLargeError(DomainError):
    pass


class VoiceFileUnavailableError(DomainError):
    pass


class RetryAfterError(DomainError):
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__(f"Retry after {seconds} seconds")


class RecipientBlockedError(DomainError):
    pass


class MessageGoneError(DomainError):
    pass


class NotificationError(DomainError):
    pass
