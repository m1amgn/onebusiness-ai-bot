from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict

from taskbot.domain.voice_jobs import UnnotifiedJob, VoiceJob, VoiceJobStatus


class TranscriptionJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    voice_job_id: int
    queue: ClassVar[str] = "transcription"
    name: ClassVar[str] = "transcribe_voice_job"


class NotificationJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    voice_job_id: int
    status: Literal["done", "failed"]
    queue: ClassVar[str] = "notification"
    name: ClassVar[str] = "notify_user"

    @classmethod
    def from_voice_job(cls, row: VoiceJob | UnnotifiedJob) -> Self:
        if row.status not in (VoiceJobStatus.DONE, VoiceJobStatus.FAILED):
            raise ValueError("Notification requires a terminal voice job")
        return cls(
            voice_job_id=row.id,
            status="done" if row.status == VoiceJobStatus.DONE else "failed",
        )


class SweepJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    queue: ClassVar[str] = "notification"
    name: ClassVar[str] = "recover_stuck_voice_jobs"


class TicketClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    user_id: int
    token_version: int
