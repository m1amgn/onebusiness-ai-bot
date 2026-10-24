from dataclasses import replace
from datetime import datetime

from taskbot.domain.extraction import TaskDraft
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.tasks import TaskStatus
from tests.contract.helpers import create_message


async def test_task_order_owner_and_status_contract(uows: UnitOfWorkFactory, now: datetime) -> None:
    async with uows() as uow:
        older_id = await create_message(uow, now)
        message = await uow.messages.get(older_id)
        assert message is not None
        uid = message.user_id
        older = await uow.tasks.add_many(
            uid, older_id, (TaskDraft(title="a"), TaskDraft(title="b")), now
        )
        newer_id = await create_message(uow, now, 2)
        newer = await uow.tasks.add_many(uid, newer_id, (TaskDraft(title="c"),), now)
        assert await uow.tasks.list_for_user(uid) == newer + older
        assert await uow.tasks.list_for_message(older_id) == older
        assert await uow.tasks.list_for_message(999) == ()
        assert await uow.tasks.list_for_user(999) == ()
        assert await uow.tasks.get_for_update(999, older[0].id) is None
        assert await uow.tasks.get_for_update(uid, older[0].id) == older[0]
        changed = older[0].change_status(TaskStatus.COMPLETED, now)
        assert changed is not None
        assert await uow.tasks.save_status(replace(changed, user_id=999)) is None
        assert await uow.tasks.save_status(replace(changed, id=999)) is None
        assert await uow.tasks.save_status(replace(changed, title="must not overwrite")) == changed
        assert [t.id for t in await uow.tasks.list_for_user(uid)] == [t.id for t in newer + older]
        await uow.commit()
