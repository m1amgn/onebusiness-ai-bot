import asyncio
import json
import os
import signal
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import cast

import pytest

from taskbot.infrastructure.redis.client import create_redis_client
from tests.redis_sessions import RedisSession, open_redis_session
from tests.workers import read_records, running_worker, wait_records

pytestmark = pytest.mark.integration


async def test_countdown_and_reserved_eta_survive_restart(
    redis_session: RedisSession, tmp_path: Path
) -> None:
    namespace = redis_session.child()
    log = tmp_path / "tasks.jsonl"
    async with running_worker("transcribe", namespace, log_dir=tmp_path) as worker:
        sent = time.time()
        await worker.send("s3test.sleep", seconds=0.1, marker="eta3", log=str(log), countdown=3)
        rows = await wait_records(log, "start", timeout_seconds=8)
        assert float(str(rows[0]["time"])) >= sent + 3
        sent = time.time()
        await worker.send("s3test.sleep", seconds=0.1, marker="eta12", log=str(log), countdown=12)
        await asyncio.sleep(2)
        os.kill(worker.pid, signal.SIGTERM)
        await asyncio.wait_for(worker.process.wait(), 15)
    async with create_redis_client(redis_session.broker_url) as broker:
        messages = await cast(
            Awaitable[list[str]], broker.lrange(namespace + "transcription", 0, -1)
        )
        assert len(messages) == 1
        assert json.loads(messages[0])["headers"]["eta"]
    async with running_worker("transcribe", namespace, log_dir=tmp_path):
        await wait_records(log, "end", count=2, timeout_seconds=18)
        rows = [row for row in read_records(log, "start") if row["marker"] == "eta12"]
        assert len(rows) == 1
        assert float(str(rows[0]["time"])) >= sent + 12


async def test_worker_session_cleans_both_databases_and_beat_files(tmp_path: Path) -> None:
    async with open_redis_session() as session:
        async with running_worker("notify", session.child(), log_dir=tmp_path) as worker:
            await worker.send(
                "s3test.sleep", seconds=0.1, marker="cleanup", log=str(tmp_path / "tasks.jsonl")
            )
            await wait_records(tmp_path / "tasks.jsonl", "end")
        assert not list(worker.schedule_path.parent.glob(worker.schedule_path.name + "*"))
    for url in (session.redis_url, session.broker_url):
        async with create_redis_client(url) as client:
            assert [key async for key in client.scan_iter(match=session.id + ":*")] == []
