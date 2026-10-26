from datetime import datetime

from taskbot.domain.extraction import ExtractorName
from taskbot.domain.messages import MessageKind, MessageRef, NewInboundMessage
from taskbot.domain.ports.persistence import UnitOfWork
from taskbot.domain.tasks import Task
from taskbot.domain.text import MessageText
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import NewVoiceJob, VoiceJob


async def create_message(uow: UnitOfWork, now: datetime, message_id: int = 1) -> int:
    user = await uow.users.upsert(TelegramIdentity(7, "A"), now)
    message = await uow.messages.add(
        NewInboundMessage(
            user.user.id,
            MessageRef(7, message_id),
            MessageKind.TEXT,
            MessageText.parse("Call home"),
            ExtractorName.HEURISTIC,
            False,
        ),
        now,
    )
    assert message is not None
    return message.id


async def create_voice(uow: UnitOfWork, now: datetime) -> VoiceJob:
    user = await uow.users.upsert(TelegramIdentity(7, "A"), now)
    added = await uow.voice_jobs.add_or_get(
        NewVoiceJob(user.user.id, MessageRef(7, 10), "file", "unique", 5, None), now
    )
    return added.job


async def all_tasks(uow: UnitOfWork, user_id: int) -> tuple[Task, ...]:
    """Read the small fixture's tasks through the production paging contract."""
    rows = await uow.tasks.page_for_user(user_id, status=None, after=None, fetch=10000)
    return tuple(row.task for row in rows)
