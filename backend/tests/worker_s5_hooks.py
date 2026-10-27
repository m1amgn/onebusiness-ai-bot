"""Explicit --include seam; keeps PostgreSQL, Redis and producer clients real."""

import fcntl
import os
import signal
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from celery import signals

from taskbot.domain.jobs import SweepJob
from taskbot.entrypoints.worker import container as container_module
from taskbot.entrypoints.worker import runtime
from taskbot.entrypoints.worker.container import WorkerContainer
from tests.s5 import FIXTURE
from tests.worker_tasks import record


def receipt(marker: str, **fields: object) -> None:
    record(os.environ["TASKBOT_TEST_S5_RECEIPTS"], marker, event=marker, **fields)


def identities(container: WorkerContainer) -> dict[str, object]:
    assert container.engine is not None
    assert container.redis is not None
    if container.settings.worker_role == "transcribe":
        assert container.extractor is not None
        assert container.transcriber is not None
    return {
        "role": container.settings.worker_role,
        "engine": id(container.engine),
        "redis": id(container.redis),
        "extractor": None if container.extractor is None else id(container.extractor),
        "transcriber": None if container.transcriber is None else id(container.transcriber),
    }


class RecordingNotifier:
    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        receipt("notification", action="edited", chat_id=chat_id, message_id=message_id, text=text)

    async def send_reply(
        self, chat_id: int, text: str, *, reply_to_message_id: int | None = None
    ) -> int:
        receipt(
            "notification",
            action="replied",
            chat_id=chat_id,
            reply_to=reply_to_message_id,
            text=text,
        )
        return 1


class FixtureVoiceSource:
    async def fetch(self, file_id: str, *, max_bytes: int) -> bytes:
        receipt("fetch", **identities(runtime.current_container()))
        if os.environ.get("TASKBOT_TEST_S5_CRASH") == "1":
            os.kill(os.getpid(), signal.SIGKILL)
        data = FIXTURE.read_bytes()
        assert len(data) <= max_bytes
        return data


class ShiftClock:
    def __init__(self, shift: int) -> None:
        self._shift = timedelta(seconds=shift)

    def now(self) -> datetime:
        return datetime.now(UTC) + self._shift


def next_shift() -> int:
    descriptor = os.open(os.environ["TASKBOT_TEST_S5_CLOCK"], os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+") as counter:
        fcntl.flock(counter, fcntl.LOCK_EX)
        count = int(counter.read() or "0")
        counter.seek(0)
        counter.truncate()
        counter.write(str(count + 1))
        counter.flush()
    return count * 460


original_build = runtime.build_container


async def build_with_receipts(container: WorkerContainer) -> None:
    if (
        os.environ.get("TASKBOT_TEST_S5_CRASH") == "1"
        and container.settings.worker_role == "transcribe"
    ):
        container.clock = ShiftClock(next_shift())
    await original_build(container)
    receipt("build", **identities(container))


def after_task(sender: object = None, state: object = None, **kwargs: object) -> None:
    if getattr(sender, "name", None) == SweepJob.name:
        receipt("sweep", state=state)


patch.object(container_module, "UnconfiguredNotifier", RecordingNotifier).start()
patch.object(container_module, "UnconfiguredVoiceSource", FixtureVoiceSource).start()
runtime.build_container = build_with_receipts
signals.task_postrun.connect(after_task, weak=False)
