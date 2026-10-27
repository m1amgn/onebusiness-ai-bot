import asyncio
import json
from collections.abc import Awaitable
from pathlib import Path
from typing import cast

import pytest

from taskbot.infrastructure.celery.app import create_celery_app
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.subscription import subscribe_user_events
from tests.processes import COLD_START_TIMEOUT_SECONDS
from tests.redis_sessions import RedisSession
from tests.workers import read_records, running_worker, wait_records

pytestmark = pytest.mark.integration


async def test_stable_loop_publishes_and_drains_soft_limit(
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    namespace = redis_session.child()
    log = tmp_path / "tasks.jsonl"
    async with (
        running_worker("transcribe", namespace, log_dir=tmp_path) as worker,
        create_redis_client(redis_session.redis_url) as client,
        subscribe_user_events(client, namespace, 42) as subscription,
    ):
        for _ in range(2):
            await worker.send("s3test.loop_identity", log=str(log))
        await wait_records(log, "loop", count=2)
        await worker.send("s3test.publish", user_id=42, log=str(log))
        assert json.loads(str(await subscription.next(timeout_seconds=5))) == {
            "type": "auth.revoked"
        }
        await worker.send("s3test.soft_limit", log=str(log))
        await wait_records(log, "soft_limit", timeout_seconds=COLD_START_TIMEOUT_SECONDS)
        assert len(read_records(log, "cancelled")) == 1
        await worker.send("s3test.loop_identity", log=str(log))
        rows = await wait_records(log, "loop", count=3)
        assert len({row["loop_marker"] for row in rows}) == 1
        assert len({row["pid"] for row in rows}) == 1
        assert all(row["pending"] == 0 for row in rows)


async def test_failed_runtime_runs_no_task_restores_message_and_allows_healthy_retry(
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    namespace = redis_session.child()
    log = tmp_path / "tasks.jsonl"
    # Queue before starting to cover a message reserved during fatal child init.
    app = create_celery_app(broker_url=redis_session.broker_url, namespace=namespace)
    try:
        await asyncio.to_thread(
            app.send_task,
            "s3test.sleep",
            kwargs={"seconds": 0.1, "marker": "retry", "log": str(log)},
            queue="transcription",
        )
    finally:
        app.close()
    async with running_worker(
        "transcribe", namespace, log_dir=tmp_path, fail_runtime=True, wait_ready=False
    ) as broken:
        assert not read_records(log, "start")
        await asyncio.wait_for(broken.process.wait(), COLD_START_TIMEOUT_SECONDS)
        assert "Worker child runtime failed" in broken.log_path.read_text()
    async with create_redis_client(redis_session.broker_url) as broker:
        assert await cast(Awaitable[int], broker.llen(namespace + "transcription")) == 1
    async with running_worker("transcribe", namespace, log_dir=tmp_path) as healthy:
        await wait_records(log, "end", timeout_seconds=COLD_START_TIMEOUT_SECONDS)
        assert len(read_records(log, "start")) == 1
        assert healthy.process.returncode is None
