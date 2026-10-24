from datetime import datetime

from taskbot.domain.extraction import ExtractorName
from taskbot.domain.messages import MessageKind, MessageRef, NewInboundMessage
from taskbot.domain.ports.persistence import UnitOfWork
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
