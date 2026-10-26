from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from taskbot.domain.tasks import Task, TaskStatus


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: int
    inbound_message_id: int
    position: int
    title: str
    description: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_task(cls, task: Task) -> Self:
        return cls(
            id=task.id,
            inbound_message_id=task.inbound_message_id,
            position=task.position,
            title=task.title,
            description=task.description,
            status=task.status,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class TaskCreated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["task.created"] = "task.created"
    user_id: int = Field(exclude=True)
    task: TaskView


class TaskUpdated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["task.updated"] = "task.updated"
    user_id: int = Field(exclude=True)
    task: TaskView


class AuthRevoked(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["auth.revoked"] = "auth.revoked"
    user_id: int = Field(exclude=True)


type DomainEvent = Annotated[TaskCreated | TaskUpdated | AuthRevoked, Field(discriminator="type")]
