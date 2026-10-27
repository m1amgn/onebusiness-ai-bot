import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.domain.jobs import TranscriptionJob
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.voice_jobs import (
    FailureKind,
    NotificationOutcome,
    TranscriptionProvider,
    VoiceJobStatus,
)
from taskbot.infrastructure.clock import SystemClock
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.subscription import subscribe_user_events
from tests.contract.helpers import all_tasks
from tests.redis_sessions import RedisSession
from tests.s5 import HOOKS, create_job, wait_job
from tests.workers import read_records, running_worker, wait_records


async def test_real_workers_complete_publish_notify_and_reuse_child_clients(
    postgres_uows: UnitOfWorkFactory,
    database_url: str,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    first = await create_job(postgres_uows, SystemClock())
    second = await create_job(postgres_uows, SystemClock(), message_id=11)
    namespace = redis_session.child()
    receipts = tmp_path / "receipts.jsonl"
    env = {"TASKBOT_TEST_S5_RECEIPTS": str(receipts)}
    async with (
        create_redis_client(redis_session.redis_url) as redis,
        subscribe_user_events(redis, namespace, first.user_id) as events,
        running_worker(
            "notify", namespace, log_dir=tmp_path, database_url=database_url, env=env, include=HOOKS
        ),
        running_worker(
            "transcribe",
            namespace,
            log_dir=tmp_path,
            database_url=database_url,
            env=env,
            include=HOOKS,
        ) as transcribe,
    ):
        for row in (first, second):
            await transcribe.send(TranscriptionJob.name, voice_job_id=row.id)
        for row in (first, second):
            final = await wait_job(postgres_uows, row.id, lambda item: item.notified_at is not None)
            assert final.status == VoiceJobStatus.DONE
            assert final.provider == TranscriptionProvider.FAKE
            assert final.notification_outcome == NotificationOutcome.EDITED
            assert final.attempts == 1
        notices = await wait_records(receipts, "notification", count=2)
        assert all(
            str(row["text"]).startswith("Task created: [demo transcript]") for row in notices
        )
        assert all(str(row["text"]).endswith(" (demo mode)") for row in notices)
        payloads = [json.loads(str(await events.next(timeout_seconds=5))) for _ in range(2)]
        assert all(payload["type"] == "task.created" for payload in payloads)
        async with postgres_uows() as uow:
            assert len(await all_tasks(uow, first.user_id)) == 2
        fetches = read_records(receipts, "fetch")
        assert len(fetches) == 2
        assert len({row["pid"] for row in fetches}) == 1
        for resource in ("engine", "redis", "extractor", "transcriber"):
            assert len({row[resource] for row in fetches}) == 1
            assert fetches[0][resource] is not None
        notify_builds = [row for row in read_records(receipts, "build") if row["role"] == "notify"]
        assert len(notify_builds) == 2
        assert all(row["extractor"] is None and row["transcriber"] is None for row in notify_builds)


async def test_keyless_openai_job_reaches_the_unavailable_notification(
    postgres_uows: UnitOfWorkFactory,
    database_url: str,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    row = await create_job(postgres_uows, SystemClock())
    namespace = redis_session.child()
    receipts = tmp_path / "receipts.jsonl"
    env = {
        "TASKBOT_TEST_S5_RECEIPTS": str(receipts),
        "TRANSCRIPTION_PROVIDER": "openai",
        "OPENAI_API_KEY": None,
    }
    async with (
        running_worker(
            "notify", namespace, log_dir=tmp_path, database_url=database_url, env=env, include=HOOKS
        ),
        running_worker(
            "transcribe",
            namespace,
            log_dir=tmp_path,
            database_url=database_url,
            env=env,
            include=HOOKS,
        ) as worker,
    ):
        await worker.send(TranscriptionJob.name, voice_job_id=row.id)
        final = await wait_job(postgres_uows, row.id, lambda item: item.notified_at is not None)
        assert final.failure_kind == FailureKind.PROVIDER_UNAVAILABLE
        assert final.provider == TranscriptionProvider.OPENAI
        notices = await wait_records(receipts, "notification")
        assert (
            notices[0]["text"] == "Transcription is unavailable right now. "
            "Your message was not lost: please type it as text."
        )
        assert (
            worker.log_path.read_text().count(
                "OpenAI transcription is unavailable: missing API key"
            )
            == 1
        )


async def test_five_real_child_crashes_exhaust_without_a_sixth_download(
    postgres_uows: UnitOfWorkFactory,
    database_url: str,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    row = await create_job(postgres_uows, SystemClock())
    namespace = redis_session.child()
    receipts = tmp_path / "receipts.jsonl"
    env = {
        "TASKBOT_TEST_S5_RECEIPTS": str(receipts),
        "TASKBOT_TEST_S5_CRASH": "1",
        "TASKBOT_TEST_S5_CLOCK": str(tmp_path / "clock"),
    }
    async with (
        running_worker(
            "notify", namespace, log_dir=tmp_path, database_url=database_url, env=env, include=HOOKS
        ),
        running_worker(
            "transcribe",
            namespace,
            log_dir=tmp_path,
            database_url=database_url,
            env=env,
            include=HOOKS,
        ) as worker,
    ):
        await worker.send(TranscriptionJob.name, voice_job_id=row.id)
        final = await wait_job(
            postgres_uows, row.id, lambda item: item.notified_at is not None, timeout_seconds=60
        )
        assert final.status == VoiceJobStatus.FAILED
        assert final.failure_kind == FailureKind.STUCK
        assert final.attempts == 5
        assert final.notification_outcome == NotificationOutcome.EDITED
        notices = await wait_records(receipts, "notification")
        assert notices[0]["text"] == (
            "Something went wrong while processing this voice message. Please send it again."
        )
        assert len(read_records(receipts, "fetch")) == 5
        assert len({item["pid"] for item in read_records(receipts, "fetch")}) == 5


async def test_notify_runs_startup_sweep_and_real_beat_tick(
    database_url: str,
    clean_database: None,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    receipts = tmp_path / "receipts.jsonl"
    env = {"TASKBOT_TEST_S5_RECEIPTS": str(receipts)}
    async with running_worker(
        "notify",
        redis_session.child(),
        log_dir=tmp_path,
        database_url=database_url,
        env=env,
        include=HOOKS,
    ) as worker:
        rows = await wait_records(receipts, "sweep", count=2, timeout_seconds=80)
        assert all(row["state"] == "SUCCESS" for row in rows)
        output = worker.log_path.read_text()
        assert "Voice recovery job failed (" not in output
        assert output.count("Voice recovery job completed") >= 2


async def test_database_error_logs_no_transcript_and_leaves_processing_for_recovery(
    postgres_uows: UnitOfWorkFactory,
    sql_sessions: async_sessionmaker[AsyncSession],
    database_url: str,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    row = await create_job(postgres_uows, SystemClock())
    async with sql_sessions.begin() as session:
        await session.execute(
            text("""
            CREATE FUNCTION s5_fail_message() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'S5 synthetic secret: %', NEW.text; END; $$
        """)
        )
        await session.execute(
            text("""
            CREATE TRIGGER s5_fail_message BEFORE INSERT ON inbound_messages
            FOR EACH ROW EXECUTE FUNCTION s5_fail_message()
        """)
        )
    receipts = tmp_path / "receipts.jsonl"
    try:
        async with running_worker(
            "transcribe",
            redis_session.child(),
            log_dir=tmp_path,
            database_url=database_url,
            include=HOOKS,
            env={"TASKBOT_TEST_S5_RECEIPTS": str(receipts)},
        ) as worker:
            await worker.send(TranscriptionJob.name, voice_job_id=row.id)
            output = await worker.wait_for_log("Transcription job failed (")
            assert "S5 synthetic secret" not in output
            assert "demo transcript" not in output
            assert "Please remind me" not in output
            assert "Traceback" not in output
            async with postgres_uows() as uow:
                stored = await uow.voice_jobs.get(row.id)
                assert stored is not None
                assert stored.status == VoiceJobStatus.PROCESSING
                assert stored.attempts == 1
                assert await uow.messages.get_by_ref(row.ref) is None
                assert not await all_tasks(uow, row.user_id)
    finally:
        async with sql_sessions.begin() as session:
            await session.execute(text("DROP TRIGGER s5_fail_message ON inbound_messages"))
            await session.execute(text("DROP FUNCTION s5_fail_message()"))
