from datetime import datetime

from taskbot.domain.extraction import ExtractorName
from taskbot.domain.messages import MessageKind, MessageRef, NewInboundMessage
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity


async def test_message_conflict_and_reads(uows: UnitOfWorkFactory, now: datetime) -> None:
    async with uows() as uow:
        user = await uow.users.upsert(TelegramIdentity(7, "A"), now)
        new = NewInboundMessage(
            user.user.id,
            MessageRef(7, 1),
            MessageKind.TEXT,
            MessageText.parse("Call home"),
            ExtractorName.HEURISTIC,
            False,
        )
        first = await uow.messages.add(new, now)
        assert first is not None
        assert await uow.messages.add(new, now) is None
        assert await uow.messages.get(first.id) == first
        assert await uow.messages.get_by_ref(new.ref) == first
        assert await uow.messages.get_by_ref(MessageRef(8, 1)) is None
        assert await uow.messages.get(999) is None
        await uow.commit()
    async with uows() as uow:
        assert await uow.messages.get(first.id) == first
