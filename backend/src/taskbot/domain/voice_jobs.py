import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from taskbot.domain.limits import LEASE_SECONDS, MAX_NOTIFY_ATTEMPTS, MAX_TRANSCRIPTION_ATTEMPTS
from taskbot.domain.messages import MessageRef
from taskbot.domain.time import require_aware


class VoiceJobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class FailureKind(StrEnum):
    TOO_LARGE = "too_large"
    UNSUPPORTED_AUDIO = "unsupported_audio"
    EMPTY_TRANSCRIPT = "empty_transcript"
    TRANSCRIPTION_TIMEOUT = "transcription_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TRANSIENT_EXHAUSTED = "transient_exhausted"
    INTERNAL = "internal"
    STUCK = "stuck"


class NotificationOutcome(StrEnum):
    EDITED = "edited"
    REPLIED = "replied"
    BLOCKED = "blocked"
    GIVEN_UP = "given_up"


class TranscriptionProvider(StrEnum):
    FAKE = "fake"
    OPENAI = "openai"
    WHISPER_SERVER = "whisper_server"


@dataclass(frozen=True, slots=True)
class NewVoiceJob:
    user_id: int
    ref: MessageRef
    file_id: str
    file_unique_id: str
    duration_seconds: int
    file_size: int | None


@dataclass(frozen=True, slots=True)
class VoiceJob:
    id: int
    user_id: int
    ref: MessageRef
    file_id: str
    file_unique_id: str
    duration_seconds: int
    file_size: int | None
    created_at: datetime
    updated_at: datetime
    last_enqueued_at: datetime
    status: VoiceJobStatus = VoiceJobStatus.QUEUED
    attempts: int = 0
    claimed_at: datetime | None = None
    inbound_message_id: int | None = None
    ack_message_id: int | None = None
    failure_kind: FailureKind | None = None
    provider: TranscriptionProvider | None = None
    notify_attempts: int = 0
    notified_at: datetime | None = None
    notification_outcome: NotificationOutcome | None = None
    notify_not_before: datetime | None = None

    @property
    def terminal(self) -> bool:
        return self.status in (VoiceJobStatus.DONE, VoiceJobStatus.FAILED)

    def lease_live(self, now: datetime) -> bool:
        return (
            self.status == VoiceJobStatus.PROCESSING
            and self.claimed_at is not None
            and self.claimed_at >= now - timedelta(seconds=LEASE_SECONDS)
        )

    def _stamp(self, now: datetime) -> VoiceJob:
        require_aware(now)
        return replace(self, updated_at=now, last_enqueued_at=now)

    def set_ack_message_id(self, ack: int, now: datetime) -> VoiceJob | None:
        require_aware(now)
        if self.ack_message_id is not None:
            return None
        return replace(self, ack_message_id=ack, updated_at=now)

    def mark_enqueued(self, now: datetime) -> VoiceJob | None:
        require_aware(now)
        if self.terminal and self.notified_at is not None:
            return None
        return self._stamp(now)

    def claim(self, now: datetime) -> VoiceJob | None:
        require_aware(now)
        if self.terminal or self.attempts >= MAX_TRANSCRIPTION_ATTEMPTS or self.lease_live(now):
            return None
        if self.status == VoiceJobStatus.PROCESSING and self.claimed_at is None:
            return None
        return replace(
            self,
            status=VoiceJobStatus.PROCESSING,
            attempts=self.attempts + 1,
            claimed_at=now,
            updated_at=now,
        )

    def lock_attempt(self, attempt: int) -> VoiceJob | None:
        return (
            self if self.status == VoiceJobStatus.PROCESSING and self.attempts == attempt else None
        )

    def requeue(self, attempt: int, now: datetime) -> VoiceJob | None:
        require_aware(now)
        if self.lock_attempt(attempt) is None or attempt >= MAX_TRANSCRIPTION_ATTEMPTS:
            return None
        return replace(self._stamp(now), status=VoiceJobStatus.QUEUED)

    def complete(
        self, attempt: int, inbound_message_id: int, provider: TranscriptionProvider, now: datetime
    ) -> VoiceJob | None:
        require_aware(now)
        if self.lock_attempt(attempt) is None:
            return None
        return replace(
            self._stamp(now),
            status=VoiceJobStatus.DONE,
            inbound_message_id=inbound_message_id,
            provider=provider,
        )

    def fail(
        self, attempt: int, kind: FailureKind, provider: TranscriptionProvider, now: datetime
    ) -> VoiceJob | None:
        require_aware(now)
        if self.lock_attempt(attempt) is None:
            return None
        return replace(
            self._stamp(now), status=VoiceJobStatus.FAILED, failure_kind=kind, provider=provider
        )

    def exhaust(self, now: datetime) -> VoiceJob | None:
        require_aware(now)
        if self.terminal or self.attempts < MAX_TRANSCRIPTION_ATTEMPTS or self.lease_live(now):
            return None
        if self.status == VoiceJobStatus.PROCESSING and self.claimed_at is None:
            return None
        return replace(
            self._stamp(now), status=VoiceJobStatus.FAILED, failure_kind=FailureKind.STUCK
        )

    def begin_notification(self, now: datetime) -> VoiceJob | None:
        require_aware(now)
        if (
            not self.terminal
            or self.notified_at is not None
            or self.notify_attempts >= MAX_NOTIFY_ATTEMPTS
        ):
            return None
        if self.notify_not_before is not None and self.notify_not_before > now:
            return None
        return replace(self._stamp(now), notify_attempts=self.notify_attempts + 1)

    def defer_notification(self, until: datetime, now: datetime) -> VoiceJob | None:
        require_aware(now)
        require_aware(until)
        if not self.terminal or self.notified_at is not None or until < now:
            return None
        return replace(
            self._stamp(now), notify_not_before=max(self.notify_not_before or until, until)
        )

    def finish_notification(self, outcome: NotificationOutcome, now: datetime) -> VoiceJob | None:
        require_aware(now)
        if not self.terminal or self.notified_at is not None:
            return None
        return replace(self, notified_at=now, notification_outcome=outcome, updated_at=now)


@dataclass(frozen=True, slots=True)
class AddedVoiceJob:
    job: VoiceJob
    created: bool


@dataclass(frozen=True, slots=True)
class Claimed:
    job: VoiceJob


@dataclass(frozen=True, slots=True)
class AlreadyFinished:
    job: VoiceJob


@dataclass(frozen=True, slots=True)
class LeaseLive:
    remaining_seconds: int


@dataclass(frozen=True, slots=True)
class CeilingReached:
    job: VoiceJob


@dataclass(frozen=True, slots=True)
class JobMissing:
    pass


type ClaimOutcome = Claimed | AlreadyFinished | LeaseLive | CeilingReached | JobMissing


def classify_claim_refusal(job: VoiceJob | None, now: datetime) -> ClaimOutcome:
    require_aware(now)
    if job is None:
        return JobMissing()
    if job.terminal:
        return AlreadyFinished(job)
    if job.lease_live(now) and job.claimed_at is not None:
        remaining = job.claimed_at + timedelta(seconds=LEASE_SECONDS) - now
        return LeaseLive(max(0, math.ceil(remaining.total_seconds())))
    if job.attempts >= MAX_TRANSCRIPTION_ATTEMPTS:
        return CeilingReached(job)
    return LeaseLive(0)


@dataclass(frozen=True, slots=True)
class NotificationStarted:
    job: VoiceJob


@dataclass(frozen=True, slots=True)
class NotificationNotDue:
    remaining_seconds: int


@dataclass(frozen=True, slots=True)
class NotificationCeilingReached:
    job: VoiceJob


@dataclass(frozen=True, slots=True)
class NothingToNotify:
    pass


type NotificationStart = (
    NotificationStarted | NotificationNotDue | NotificationCeilingReached | NothingToNotify
)


def classify_notification_refusal(job: VoiceJob | None, now: datetime) -> NotificationStart:
    require_aware(now)
    if job is None or not job.terminal or job.notified_at is not None:
        return NothingToNotify()
    if job.notify_attempts >= MAX_NOTIFY_ATTEMPTS:
        return NotificationCeilingReached(job)
    if job.notify_not_before is not None and job.notify_not_before > now:
        return NotificationNotDue(math.ceil((job.notify_not_before - now).total_seconds()))
    return NotificationNotDue(0)


@dataclass(frozen=True, slots=True)
class UnnotifiedJob:
    id: int
    status: VoiceJobStatus
