from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from taskbot.domain.limits import DESCRIPTION_MAX, TITLE_MAX
from taskbot.domain.tasks import Task, TaskStatus


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: int
    inbound_message_id: int
    position: int
    title: str = Field(max_length=TITLE_MAX)
    description: str = Field(max_length=DESCRIPTION_MAX)
    source_text: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_task(cls, task: Task, source_text: str) -> Self:
        return cls(
            id=task.id,
            inbound_message_id=task.inbound_message_id,
            position=task.position,
            title=task.title,
            description=task.description,
            source_text=source_text,
            status=task.status,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class TaskCreated(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )
    type: Literal["task.created"] = "task.created"
    user_id: int = Field(exclude=True)
    task: TaskView


class TaskUpdated(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )
    type: Literal["task.updated"] = "task.updated"
    user_id: int = Field(exclude=True)
    task: TaskView


class AuthRevoked(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )
    type: Literal["auth.revoked"] = "auth.revoked"
    user_id: int = Field(exclude=True)


class TaskDeleted(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )
    type: Literal["task.deleted"] = "task.deleted"
    user_id: int = Field(exclude=True)
    task_id: int


type DomainEvent = Annotated[
    TaskCreated | TaskUpdated | TaskDeleted | AuthRevoked, Field(discriminator="type")
]
