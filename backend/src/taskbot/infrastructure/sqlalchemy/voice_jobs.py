from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, RowMapping, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from taskbot.domain.limits import (
    LEASE_SECONDS,
    MAX_NOTIFY_ATTEMPTS,
    MAX_TRANSCRIPTION_ATTEMPTS,
    SWEEP_STALE_SECONDS,
    SWEEP_UNNOTIFIED_SECONDS,
)
from taskbot.domain.time import require_aware
from taskbot.domain.voice_jobs import (
    AddedVoiceJob,
    Claimed,
    ClaimOutcome,
    FailureKind,
    NewVoiceJob,
    NotificationOutcome,
    NotificationStart,
    NotificationStarted,
    TranscriptionProvider,
    UnnotifiedJob,
    VoiceJob,
    VoiceJobStatus,
    classify_claim_refusal,
    classify_notification_refusal,
)
from taskbot.infrastructure.sqlalchemy.mapping import voice_job_from_row
from taskbot.infrastructure.sqlalchemy.queries import many, one
from taskbot.infrastructure.sqlalchemy.tables import VoiceJobRow as Job
from taskbot.infrastructure.sqlalchemy.tables import concrete_table

TERMINAL = Job.status.in_((VoiceJobStatus.DONE, VoiceJobStatus.FAILED))
UNNOTIFIED = and_(TERMINAL, Job.notified_at.is_(None))


def claimable(now: datetime) -> ColumnElement[bool]:
    return or_(
        Job.status == VoiceJobStatus.QUEUED,
        and_(
            Job.status == VoiceJobStatus.PROCESSING,
            Job.claimed_at < now - timedelta(seconds=LEASE_SECONDS),
        ),
    )


def current_attempt(attempt: int) -> ColumnElement[bool]:
    return and_(Job.status == VoiceJobStatus.PROCESSING, Job.attempts == attempt)


def notification_due(now: datetime) -> ColumnElement[bool]:
    return or_(Job.notify_not_before.is_(None), Job.notify_not_before <= now)


class SqlAlchemyVoiceJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def change(
        self, job_id: int, now: datetime, guard: ColumnElement[bool], **values: object
    ) -> VoiceJob | None:
        require_aware(now)
        statement = update(concrete_table(Job)).where(Job.id == job_id, guard)
        row = await one(
            self._session, statement.values(updated_at=now, **values).returning(*Job.__table__.c)
        )
        return None if row is None else voice_job_from_row(row)

    async def get(self, job_id: int) -> VoiceJob | None:
        row = await one(self._session, select(*Job.__table__.c).where(Job.id == job_id))
        return None if row is None else voice_job_from_row(row)

    async def add_or_get(self, new: NewVoiceJob, now: datetime) -> AddedVoiceJob:
        require_aware(now)
        statement = (
            insert(concrete_table(Job))
            .values(
                user_id=new.user_id,
                telegram_chat_id=new.ref.chat_id,
                telegram_message_id=new.ref.message_id,
                file_id=new.file_id,
                file_unique_id=new.file_unique_id,
                duration_seconds=new.duration_seconds,
                file_size=new.file_size,
                status=VoiceJobStatus.QUEUED,
                attempts=0,
                notify_attempts=0,
                last_enqueued_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[Job.telegram_chat_id, Job.telegram_message_id])
        )
        row = await one(self._session, statement.returning(*Job.__table__.c))
        if row is not None:
            return AddedVoiceJob(voice_job_from_row(row), True)
        row = await one(
            self._session,
            select(*Job.__table__.c).where(
                Job.telegram_chat_id == new.ref.chat_id,
                Job.telegram_message_id == new.ref.message_id,
            ),
        )
        if row is None:
            raise RuntimeError("Conflicting voice job disappeared")
        return AddedVoiceJob(voice_job_from_row(row), False)

    async def set_ack_message_id(self, job_id: int, ack: int, now: datetime) -> bool:
        return (
            await self.change(job_id, now, Job.ack_message_id.is_(None), ack_message_id=ack)
            is not None
        )

    async def mark_enqueued(self, job_id: int, now: datetime) -> bool:
        guard = or_(Job.status.in_((VoiceJobStatus.QUEUED, VoiceJobStatus.PROCESSING)), UNNOTIFIED)
        return await self.change(job_id, now, guard, last_enqueued_at=now) is not None

    async def claim(self, job_id: int, now: datetime) -> ClaimOutcome:
        require_aware(now)
        job = await self.change(
            job_id,
            now,
            and_(claimable(now), Job.attempts < MAX_TRANSCRIPTION_ATTEMPTS),
            status=VoiceJobStatus.PROCESSING,
            attempts=Job.attempts + 1,
            claimed_at=now,
        )
        if job is not None:
            return Claimed(job)
        return classify_claim_refusal(await self.get(job_id), now)

    async def lock_attempt(self, job_id: int, attempt: int) -> VoiceJob | None:
        row = await one(
            self._session,
            select(*Job.__table__.c)
            .where(Job.id == job_id, current_attempt(attempt))
            .with_for_update(),
        )
        return None if row is None else voice_job_from_row(row)

    async def requeue(self, job_id: int, attempt: int, now: datetime) -> VoiceJob | None:
        return await self.change(
            job_id,
            now,
            and_(current_attempt(attempt), Job.attempts < MAX_TRANSCRIPTION_ATTEMPTS),
            status=VoiceJobStatus.QUEUED,
            last_enqueued_at=now,
        )

    async def complete(
        self,
        job_id: int,
        attempt: int,
        inbound_message_id: int,
        provider: TranscriptionProvider,
        now: datetime,
    ) -> VoiceJob | None:
        return await self.change(
            job_id,
            now,
            current_attempt(attempt),
            status=VoiceJobStatus.DONE,
            inbound_message_id=inbound_message_id,
            provider=provider,
            last_enqueued_at=now,
        )

    async def fail(
        self,
        job_id: int,
        attempt: int,
        kind: FailureKind,
        provider: TranscriptionProvider,
        now: datetime,
    ) -> VoiceJob | None:
        return await self.change(
            job_id,
            now,
            current_attempt(attempt),
            status=VoiceJobStatus.FAILED,
            failure_kind=kind,
            provider=provider,
            last_enqueued_at=now,
        )

    async def exhaust(self, job_id: int, now: datetime) -> VoiceJob | None:
        require_aware(now)
        return await self.change(
            job_id,
            now,
            and_(claimable(now), Job.attempts >= MAX_TRANSCRIPTION_ATTEMPTS),
            status=VoiceJobStatus.FAILED,
            failure_kind=FailureKind.STUCK,
            last_enqueued_at=now,
        )

    async def begin_notification(self, job_id: int, now: datetime) -> NotificationStart:
        require_aware(now)
        job = await self.change(
            job_id,
            now,
            and_(UNNOTIFIED, notification_due(now), Job.notify_attempts < MAX_NOTIFY_ATTEMPTS),
            notify_attempts=Job.notify_attempts + 1,
            last_enqueued_at=now,
        )
        if job is not None:
            return NotificationStarted(job)
        return classify_notification_refusal(await self.get(job_id), now)

    async def defer_notification(self, job_id: int, until: datetime, now: datetime) -> bool:
        require_aware(now)
        require_aware(until)
        if until < now:
            return False
        job = await self.change(
            job_id,
            now,
            UNNOTIFIED,
            last_enqueued_at=now,
            notify_not_before=func.greatest(func.coalesce(Job.notify_not_before, until), until),
        )
        return job is not None

    async def finish_notification(
        self, job_id: int, outcome: NotificationOutcome, now: datetime
    ) -> VoiceJob | None:
        return await self.change(
            job_id, now, UNNOTIFIED, notified_at=now, notification_outcome=outcome
        )

    async def stamp(self, now: datetime, guard: ColumnElement[bool]) -> list[RowMapping]:
        require_aware(now)
        candidates = select(Job.id).where(guard).with_for_update(skip_locked=True).limit(100)
        statement = update(concrete_table(Job)).where(Job.id.in_(candidates))
        return await many(
            self._session,
            statement.values(last_enqueued_at=now, updated_at=now).returning(Job.id, Job.status),
        )

    async def stamp_stale_processing(self, now: datetime) -> list[int]:
        cutoff = now - timedelta(seconds=SWEEP_STALE_SECONDS)
        rows = await self.stamp(
            now,
            and_(
                Job.status == VoiceJobStatus.PROCESSING,
                Job.claimed_at < cutoff,
                Job.last_enqueued_at < cutoff,
            ),
        )
        return [int(row["id"]) for row in rows]

    async def stamp_stale_queued(self, now: datetime) -> list[int]:
        cutoff = now - timedelta(seconds=SWEEP_STALE_SECONDS)
        rows = await self.stamp(
            now, and_(Job.status == VoiceJobStatus.QUEUED, Job.last_enqueued_at < cutoff)
        )
        return [int(row["id"]) for row in rows]

    async def stamp_unnotified(self, now: datetime) -> list[UnnotifiedJob]:
        rows = await self.stamp(
            now,
            and_(
                UNNOTIFIED,
                notification_due(now),
                Job.last_enqueued_at < now - timedelta(seconds=SWEEP_UNNOTIFIED_SECONDS),
            ),
        )
        return [UnnotifiedJob(int(row["id"]), VoiceJobStatus(row["status"])) for row in rows]
