"""Synthetic voice jobs shared by the worker stand and the owner replay."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from taskbot.domain.messages import MessageRef
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import Clock
from taskbot.domain.users import TelegramIdentity
from taskbot.domain.voice_jobs import NewVoiceJob, VoiceJob

FIXTURE = Path(__file__).parent / "fixtures/voice-en.ogg"
HOOKS = ("tests.worker_tasks", "tests.worker_s5_hooks")
SENTENCE = "Please remind me to call the supplier tomorrow morning about the invoice."


async def create_job(
    uows: UnitOfWorkFactory,
    clock: Clock,
    *,
    message_id: int = 10,
    ack: bool = True,
    duration: int = 5,
) -> VoiceJob:
    async with uows() as uow:
        user = await uow.users.upsert(TelegramIdentity(7, "Synthetic user"), clock.now())
        added = await uow.voice_jobs.add_or_get(
            NewVoiceJob(
                user.user.id, MessageRef(7, message_id), "fixture", "synthetic", duration, None
            ),
            clock.now(),
        )
        if ack:
            await uow.voice_jobs.set_ack_message_id(added.job.id, message_id + 100, clock.now())
        row = await uow.voice_jobs.get(added.job.id)
        assert row is not None
        await uow.commit()
    return row


async def wait_job(
    uows: UnitOfWorkFactory,
    job_id: int,
    ready: Callable[[VoiceJob], bool],
    *,
    timeout_seconds: float = 30,
) -> VoiceJob:
    async with asyncio.timeout(timeout_seconds):
        while True:
            async with uows() as uow:
                row = await uow.voice_jobs.get(job_id)
            if row is not None and ready(row):
                return row
            await asyncio.sleep(0.05)
