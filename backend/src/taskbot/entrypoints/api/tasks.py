import re
from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict

from taskbot.application.publishing import publish_events
from taskbot.domain.events import TaskView
from taskbot.domain.tasks import TaskCursor, TaskStatus
from taskbot.entrypoints.api.auth import CurrentUser, Services

router = APIRouter(prefix="/api/tasks")
type TaskId = Annotated[int, Path(ge=1, le=2_147_483_647)]


class TaskPage(BaseModel):
    items: list[TaskView]
    next_cursor: str | None


class TaskStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: TaskStatus


def parse_cursor(cursor: str | None) -> TaskCursor | None:
    if cursor is None:
        return None
    if re.fullmatch(r"[0-9]{1,10}\.[0-9]{1,2}", cursor):
        message, position = (int(part) for part in cursor.split("."))
        if message <= 2_147_483_647 and position <= 2_147_483_647:
            return TaskCursor(message, position)
    raise RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("query", "cursor"),
                "msg": "Invalid task cursor",
                "input": cursor,
            }
        ]
    )


@router.get("")
async def list_tasks(
    user: CurrentUser,
    services: Services,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    status: TaskStatus | None = None,
) -> TaskPage:
    result = await services.list_tasks.execute(
        user.user_id, status=status, after=parse_cursor(cursor), limit=limit
    )
    after = result.next
    return TaskPage(
        items=[TaskView.from_task(item.task, item.source_text) for item in result.items],
        next_cursor=None if after is None else f"{after.inbound_message_id}.{after.position}",
    )


@router.patch("/{task_id}")
async def change_task_status(
    task_id: TaskId, body: TaskStatusChange, user: CurrentUser, services: Services
) -> TaskView:
    result = await services.change_task_status.execute(user.user_id, task_id, body.status)
    await publish_events(services.publisher, result.events)
    return result.view
