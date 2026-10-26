from datetime import UTC, datetime

import asyncpg
import pytest

from taskbot.domain.extraction import TaskDraft
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from tests.contract.helpers import create_message
from tests.databases import raw_url


async def test_description_limit_is_enforced_by_postgresql(
    postgres_uows: UnitOfWorkFactory, database_url: str
) -> None:
    description = "я" * 4096
    now = datetime(2026, 1, 1, tzinfo=UTC)
    async with postgres_uows() as uow:
        message_id = await create_message(uow, now)
        message = await uow.messages.get(message_id)
        assert message is not None
        tasks = await uow.tasks.add_many(
            message.user_id,
            message_id,
            (TaskDraft(title="Long description", description=description),),
            now,
        )
        await uow.commit()
    assert tasks[0].description == description

    connection = await asyncpg.connect(raw_url(database_url))
    try:
        with pytest.raises(asyncpg.CheckViolationError, match="ck_tasks_description_length"):
            await connection.execute(
                "UPDATE tasks SET description=$1 WHERE id=$2", description + "я", tasks[0].id
            )
        assert (
            await connection.fetchval("SELECT description FROM tasks WHERE id=$1", tasks[0].id)
            == description
        )
    finally:
        await connection.close()
