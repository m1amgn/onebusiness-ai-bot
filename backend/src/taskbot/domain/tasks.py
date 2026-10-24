from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from taskbot.domain.time import require_aware


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class Task:
    id: int
    user_id: int
    inbound_message_id: int
    position: int
    title: str
    description: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime

    def change_status(self, new: TaskStatus, now: datetime) -> Task | None:
        require_aware(now)
        if self.status == new:
            return None
        return replace(
            self, status=new, updated_at=max(now, self.updated_at + timedelta(microseconds=1))
        )
