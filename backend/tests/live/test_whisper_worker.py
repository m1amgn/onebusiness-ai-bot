from pathlib import Path

import pytest

from taskbot.domain.jobs import TranscriptionJob
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.voice_jobs import NotificationOutcome, TranscriptionProvider, VoiceJobStatus
from taskbot.infrastructure.clock import SystemClock
from tests.acceptance.live_transcription import LiveTranscriptionSettings
from tests.contract.helpers import all_tasks
from tests.redis_sessions import RedisSession
from tests.s5 import HOOKS, create_job, wait_job
from tests.workers import running_worker, wait_records

pytestmark = pytest.mark.live_whisper


async def test_real_whisper_worker_creates_task_and_notifies(
    postgres_uows: UnitOfWorkFactory,
    database_url: str,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    settings = LiveTranscriptionSettings()
    row = await create_job(postgres_uows, SystemClock())
    receipts = tmp_path / "live-whisper.jsonl"
    env = {"TASKBOT_TEST_S5_RECEIPTS": str(receipts)}
    namespace = redis_session.child()
    async with (
        running_worker(
            "notify", namespace, log_dir=tmp_path, database_url=database_url, env=env, include=HOOKS
        ),
        running_worker(
            "transcribe",
            namespace,
            log_dir=tmp_path,
            database_url=database_url,
            env=env
            | {
                "TRANSCRIPTION_PROVIDER": "whisper_server",
                "WHISPER_SERVER_URL": str(settings.whisper_server_url),
            },
            include=HOOKS,
        ) as worker,
    ):
        await worker.send(TranscriptionJob.name, voice_job_id=row.id)
        final = await wait_job(
            postgres_uows, row.id, lambda item: item.notified_at is not None, timeout_seconds=330
        )
        assert final.status == VoiceJobStatus.DONE
        assert final.provider == TranscriptionProvider.WHISPER_SERVER
        assert final.notification_outcome == NotificationOutcome.EDITED
        async with postgres_uows() as uow:
            message = await uow.messages.get_by_ref(row.ref)
            assert message is not None
            assert message.text
            tasks = await all_tasks(uow, row.user_id)
            assert len(tasks) == 1
            assert tasks[0].title
        notices = await wait_records(receipts, "notification")
        assert str(notices[0]["text"]).startswith("Task created: ")
        assert "(demo mode)" not in str(notices[0]["text"])
