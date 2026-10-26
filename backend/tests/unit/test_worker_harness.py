import asyncio
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from taskbot.entrypoints.worker import runtime
from tests import worker_tasks, workers


def test_worker_environment_drops_an_inherited_main_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TASKBOT_WORKER_MAIN_PID", "123456")
    assert "TASKBOT_WORKER_MAIN_PID" not in workers.worker_environment(
        "taskbot_test_0_00000000:t1:", tmp_path
    )


@pytest.mark.parametrize("override", [None, 4])
async def test_replacement_timeout_override_is_only_explicit(
    override: int | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, str]] = []
    process = Mock()
    process.pid = 999999

    async def spawn(*command: str, env: dict[str, str], **kwargs: object) -> Mock:
        captured.append(env)
        return process

    async def stop(self: workers.RunningWorker) -> None:
        pass

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(workers.RunningWorker, "stop", stop)
    async with workers.running_worker(
        "transcribe",
        "taskbot_test_0_00000000:t1:",
        log_dir=tmp_path,
        wait_ready=False,
        alive_timeout=override,
    ) as worker:
        assert worker.schedule_path.parent.is_relative_to(tmp_path)
        root = Path(captured[0]["TMPDIR"])
        assert worker.schedule_path.parent == root / f"taskbot-{os.getuid()}"
        if override is None:
            assert "TASKBOT_TEST_ALIVE_TIMEOUT" not in captured[0]
            assert "TASKBOT_TEST_ALIVE_TIMEOUT" not in worker.env
        else:
            assert captured[0]["TASKBOT_TEST_ALIVE_TIMEOUT"] == "4"
    assert not await asyncio.to_thread(root.exists)


def test_loop_receipts_distinguish_replaced_loops_even_with_reused_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "loops.jsonl"
    first, second = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        monkeypatch.setattr(worker_tasks, "id", lambda loop: 7, raising=False)
        for loop in (first, first, second):
            monkeypatch.setattr(runtime, "current_loop", lambda loop=loop: loop)
            worker_tasks.loop_identity.run(log=str(log))
        rows = workers.read_records(log, "loop")
        assert rows[0]["loop_marker"] == rows[1]["loop_marker"]
        assert rows[0]["loop_marker"] != rows[2]["loop_marker"]
        assert len({row["pid"] for row in rows}) == 1
    finally:
        first.close()
        second.close()
