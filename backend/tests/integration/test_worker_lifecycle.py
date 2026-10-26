import asyncio
import os
import signal
from collections.abc import Awaitable
from pathlib import Path
from typing import cast

import pytest

from taskbot.infrastructure.redis.client import create_redis_client
from tests.redis_sessions import RedisSession
from tests.workers import read_records, running_worker, wait_records

pytestmark = pytest.mark.integration


async def test_role_workers_route_and_report_startup(
    redis_session: RedisSession, tmp_path: Path
) -> None:
    namespace = redis_session.child()
    log = tmp_path / "tasks.jsonl"
    async with (
        running_worker("transcribe", namespace, log_dir=tmp_path) as transcribe,
        running_worker("notify", namespace, log_dir=tmp_path) as notify,
    ):
        for worker in (transcribe, notify):
            ping = await worker.cli_ping()
            assert ping.returncode == 0, ping.stderr
            for number in range(2):
                await worker.send(
                    "s3test.sleep", seconds=0.1, marker=f"{worker.role}-{number}", log=str(log)
                )
        await wait_records(log, "end", count=4)
        rows = read_records(log, "start")
        transcription_pids = {
            row["pid"] for row in rows if str(row["marker"]).startswith("transcribe")
        }
        notification_pids = {row["pid"] for row in rows if str(row["marker"]).startswith("notify")}
        assert len(transcription_pids) == 1
        assert transcription_pids.isdisjoint(notification_pids)
        for worker in (transcribe, notify):
            output = worker.log_path.read_text()
            for text in (
                f"role={worker.role}",
                f"queue={worker.queue}",
                f"node={worker.node}",
                namespace,
            ):
                assert text in output
            assert output.count("TELEGRAM_BOT_TOKEN is not set") == 1
            assert "No job handler is registered for queue" in output
            assert not any(text in output for text in ("NameError", "__wrapped__", "Traceback"))
    assert (await transcribe.cli_ping()).returncode != 0
    assert (await notify.cli_ping()).returncode != 0


@pytest.mark.parametrize("role", ["transcribe", "notify"])
async def test_warm_main_term_finishes_task_and_removes_beat(
    role: str,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    log = tmp_path / "tasks.jsonl"
    async with running_worker(role, redis_session.child(), log_dir=tmp_path) as worker:
        await worker.send("s3test.sleep", seconds=3, marker="warm", log=str(log))
        await wait_records(log, "start")
        os.kill(worker.pid, signal.SIGTERM)
        assert await asyncio.wait_for(worker.process.wait(), 15) == 0
        assert len(read_records(log, "end")) == 1
    assert not list(worker.schedule_path.parent.glob(worker.schedule_path.name + "*"))
    output = worker.log_path.read_text()
    assert "Exception ignored while finalizing file" not in output
    assert "Bad file descriptor" not in output


async def test_replacement_waits_six_seconds_then_redelivers_only_once(
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    # TASKBOT_TEST_ALIVE_TIMEOUT=4 is the executed negative control for this same case.
    log = tmp_path / "tasks.jsonl"
    timeout = int(os.environ.get("TASKBOT_TEST_ALIVE_TIMEOUT", "30"))
    async with running_worker(
        "transcribe",
        redis_session.child(),
        log_dir=tmp_path,
        replacement_delay=6,
        alive_timeout=timeout,
    ) as worker:
        await worker.send("s3test.sleep", seconds=3, marker="redelivery", log=str(log))
        rows = await wait_records(log, "start")
        os.kill(int(str(rows[0]["pid"])), signal.SIGKILL)
        rows = await wait_records(log, "start", count=2, timeout_seconds=22)
        assert rows[1]["redelivered"] is True
        assert rows[0]["pid"] != rows[1]["pid"]
        await wait_records(log, "end", timeout_seconds=10)
        await asyncio.sleep(5)
        assert len(read_records(log, "start")) == 2
        async with create_redis_client(redis_session.broker_url) as broker:
            assert await cast(Awaitable[int], broker.llen(worker.namespace + "transcription")) == 0
            assert await cast(Awaitable[int], broker.hlen(worker.namespace + "unacked")) == 0
            assert await broker.zcard(worker.namespace + "unacked_index") == 0
