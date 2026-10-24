from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from taskbot.domain.extraction import TaskDraft
from taskbot.domain.tasks import Task, TaskStatus
from taskbot.domain.time import require_aware
from taskbot.infrastructure.sqlalchemy.mapping import task_from_row
from taskbot.infrastructure.sqlalchemy.queries import many, one
from taskbot.infrastructure.sqlalchemy.tables import TaskRow, concrete_table


class SqlAlchemyTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(
        self, user_id: int, inbound_message_id: int, drafts: Sequence[TaskDraft], now: datetime
    ) -> tuple[Task, ...]:
        require_aware(now)
        if not drafts:
            return ()
        values = [
            dict(
                user_id=user_id,
                inbound_message_id=inbound_message_id,
                position=position,
                title=draft.title,
                description=draft.description,
                status=TaskStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            for position, draft in enumerate(drafts)
        ]
        rows = await many(
            self._session,
            insert(concrete_table(TaskRow)).values(values).returning(*TaskRow.__table__.c),
        )
        return tuple(sorted((task_from_row(row) for row in rows), key=lambda task: task.position))

    async def get_for_update(self, user_id: int, task_id: int) -> Task | None:
        row = await one(
            self._session,
            select(*TaskRow.__table__.c)
            .where(TaskRow.user_id == user_id, TaskRow.id == task_id)
            .with_for_update(),
        )
        return None if row is None else task_from_row(row)

    async def save_status(self, task: Task) -> Task | None:
        require_aware(task.updated_at)
        row = await one(
            self._session,
            update(concrete_table(TaskRow))
            .where(TaskRow.id == task.id, TaskRow.user_id == task.user_id)
            .values(status=task.status, updated_at=task.updated_at)
            .returning(*TaskRow.__table__.c),
        )
        return None if row is None else task_from_row(row)

    async def list_for_user(self, user_id: int) -> tuple[Task, ...]:
        rows = await many(
            self._session,
            select(*TaskRow.__table__.c)
            .where(TaskRow.user_id == user_id)
            .order_by(TaskRow.inbound_message_id.desc(), TaskRow.position),
        )
        return tuple(task_from_row(row) for row in rows)

    async def list_for_message(self, inbound_message_id: int) -> tuple[Task, ...]:
        rows = await many(
            self._session,
            select(*TaskRow.__table__.c)
            .where(TaskRow.inbound_message_id == inbound_message_id)
            .order_by(TaskRow.position),
        )
        return tuple(task_from_row(row) for row in rows)
