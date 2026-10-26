import asyncio
import os
from pathlib import Path

import pytest

from tests.redis_sessions import RedisSession
from tests.workers import read_records, running_worker, wait_records

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", ["transcribe", "notify"])
async def test_yarn_developer_command_finishes_task_after_ctrl_c(
    role: str,
    redis_session: RedisSession,
    tmp_path: Path,
) -> None:
    log = tmp_path / "tasks.jsonl"
    async with running_worker(
        role, redis_session.child(), log_dir=tmp_path, developer_command=True
    ) as worker:
        await worker.send("s3test.sleep", seconds=3, marker="ctrl-c", log=str(log))
        await wait_records(log, "start")
        state = worker.terminal_state
        assert state["session"] == state["supervisor_pid"] == state["supervisor_group"]
        assert state["job_group"] == state["job_pid"] == state["foreground_group"]
        assert state["job_group"] != state["supervisor_group"]
        assert os.getpgid(worker.pid) == state["job_group"]
        assert os.getsid(worker.pid) == state["session"]
        # Write VINTR to the controlling PTY, as a terminal Ctrl-C does.
        worker.ctrl_c()
        await wait_records(log, "end", timeout_seconds=10)
        assert worker.process.returncode is None
        assert worker.terminal_state["yarn_exit"] == -2
        (tmp_path / "terminal-evidence.json").write_text(str(state))
        await asyncio.wait_for(worker.stop(), 15)
        assert len(read_records(log, "start")) == 1
        output = worker.log_path.read_text()
        assert "Warm shutdown" in output
        assert f"role={role}" in output
        assert "queue=" + worker.queue in output
        assert "node=" + worker.node in output
    output = worker.log_path.read_text()
    assert "Exception ignored while finalizing file" not in output
    assert "Bad file descriptor" not in output
