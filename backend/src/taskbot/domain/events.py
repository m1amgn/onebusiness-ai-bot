from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from taskbot.domain.tasks import TaskStatus


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
