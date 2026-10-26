from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from taskbot.domain.messages import InboundMessage, MessageRef, NewInboundMessage
from taskbot.domain.time import require_aware
from taskbot.infrastructure.sqlalchemy.mapping import message_from_row
from taskbot.infrastructure.sqlalchemy.queries import one
from taskbot.infrastructure.sqlalchemy.tables import InboundMessageRow as MessageRow
from taskbot.infrastructure.sqlalchemy.tables import TaskRow, concrete_table


class SqlAlchemyMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, new: NewInboundMessage, now: datetime) -> InboundMessage | None:
        require_aware(now)
        statement = (
            insert(concrete_table(MessageRow))
            .values(
                user_id=new.user_id,
                telegram_chat_id=new.ref.chat_id,
                telegram_message_id=new.ref.message_id,
                kind=new.kind,
                text=new.text.value,
                extractor=new.extractor,
                degraded=new.degraded,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[MessageRow.telegram_chat_id, MessageRow.telegram_message_id]
            )
            .returning(*MessageRow.__table__.c)
        )
        row = await one(self._session, statement)
        return None if row is None else message_from_row(row)

    async def get_by_ref(self, ref: MessageRef) -> InboundMessage | None:
        row = await one(
            self._session,
            select(*MessageRow.__table__.c).where(
                MessageRow.telegram_chat_id == ref.chat_id,
                MessageRow.telegram_message_id == ref.message_id,
            ),
        )
        return None if row is None else message_from_row(row)

    async def get(self, message_id: int) -> InboundMessage | None:
        row = await one(
            self._session, select(*MessageRow.__table__.c).where(MessageRow.id == message_id)
        )
        return None if row is None else message_from_row(row)

    async def erase_text_if_unused(self, message_id: int) -> None:
        has_tasks = select(TaskRow.id).where(TaskRow.inbound_message_id == MessageRow.id).exists()
        await self._session.execute(
            update(concrete_table(MessageRow))
            .where(MessageRow.id == message_id, ~has_tasks)
            .values(text=None)
        )
