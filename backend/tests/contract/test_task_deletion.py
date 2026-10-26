from datetime import datetime

from taskbot.domain.extraction import ExtractorName, TaskDraft
from taskbot.domain.messages import MessageKind, MessageRef, NewInboundMessage
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.text import MessageText
from tests.contract.helpers import create_message


async def test_erasure_waits_for_last_task_and_keeps_message_deduplication(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        message_id = await create_message(uow, now)
        first, second = await uow.tasks.add_many(
            1, message_id, (TaskDraft(title="First"), TaskDraft(title="Second")), now
        )
        await uow.commit()

    async with uows() as uow:
        await uow.tasks.delete(first.id)
        await uow.messages.erase_text_if_unused(message_id)
        await uow.commit()
    async with uows() as uow:
        message = await uow.messages.get(message_id)
        assert message is not None
        assert message.text == "Call home"
        assert await uow.tasks.list_for_message(message_id) == (second,)
        page = await uow.tasks.page_for_user(1, status=None, after=None, fetch=10)
        assert len(page) == 1
        assert page[0].source_text == "Call home"
        await uow.tasks.delete(second.id)
        await uow.messages.erase_text_if_unused(message_id)
        await uow.commit()

    async with uows() as uow:
        message = await uow.messages.get_by_ref(MessageRef(7, 1))
        assert message is not None
        assert message.id == message_id
        assert message.text is None
        assert await uow.tasks.list_for_message(message_id) == ()
        assert (
            await uow.messages.add(
                NewInboundMessage(
                    1,
                    MessageRef(7, 1),
                    MessageKind.TEXT,
                    MessageText.parse("Duplicate delivery"),
                    ExtractorName.HEURISTIC,
                    False,
                ),
                now,
            )
            is None
        )
        await uow.commit()


async def test_deletion_and_erasure_roll_back_together(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        message_id = await create_message(uow, now)
        (task,) = await uow.tasks.add_many(1, message_id, (TaskDraft(title="Keep"),), now)
        await uow.commit()
    async with uows() as uow:
        await uow.tasks.delete(task.id)
        await uow.messages.erase_text_if_unused(message_id)
        message = await uow.messages.get(message_id)
        assert message is not None
        assert message.text is None
    async with uows() as uow:
        assert await uow.tasks.list_for_message(message_id) == (task,)
        message = await uow.messages.get(message_id)
        assert message is not None
        assert message.text == "Call home"


async def test_deletion_does_not_reuse_task_ids_or_affect_other_messages(
    uows: UnitOfWorkFactory, now: datetime
) -> None:
    async with uows() as uow:
        kept_message_id = await create_message(uow, now)
        (kept,) = await uow.tasks.add_many(1, kept_message_id, (TaskDraft(title="Keep"),), now)
        deleted_message_id = await create_message(uow, now, 2)
        (deleted,) = await uow.tasks.add_many(
            1, deleted_message_id, (TaskDraft(title="Delete"),), now
        )
        await uow.commit()
    async with uows() as uow:
        await uow.tasks.delete(deleted.id)
        await uow.messages.erase_text_if_unused(deleted_message_id)
        await uow.tasks.delete(999999)
        await uow.messages.erase_text_if_unused(999999)
        await uow.commit()
    async with uows() as uow:
        new_message_id = await create_message(uow, now, 3)
        (new,) = await uow.tasks.add_many(1, new_message_id, (TaskDraft(title="New"),), now)
        assert new.id > deleted.id
        assert await uow.tasks.list_for_message(kept_message_id) == (kept,)
        kept_message = await uow.messages.get(kept_message_id)
        assert kept_message is not None
        assert kept_message.text == "Call home"
        assert await uow.tasks.get_for_update(1, deleted.id) is None
        await uow.commit()
