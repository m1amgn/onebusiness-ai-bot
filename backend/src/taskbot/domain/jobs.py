from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict


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


class SweepJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    queue: ClassVar[str] = "notification"
    name: ClassVar[str] = "recover_stuck_voice_jobs"


class TicketClaims(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    user_id: int
    token_version: int
