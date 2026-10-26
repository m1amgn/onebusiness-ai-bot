import asyncio
import os
import signal
from collections.abc import Coroutine
from typing import cast

import pytest

from taskbot.entrypoints.worker import runtime
from taskbot.entrypoints.worker.container import WorkerContainer

DATABASE_URL = "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:1/taskbot_test_0_00000000"


@pytest.fixture(autouse=True)
def worker_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("WORKER_ROLE", "transcribe")
    monkeypatch.setenv("TASKBOT_WORKER_MAIN_PID", "4567")
    monkeypatch.setattr(os, "getppid", lambda: 4567)


class ClosedClient:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def aclose(self) -> None:
        self.calls.append("redis closed")


class ClosedEngine:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def dispose(self) -> None:
        self.calls.append("engine closed")


class FailingQueue:
    def close(self) -> None:
        raise RuntimeError("queue close failed")


@pytest.mark.parametrize("failure", [RuntimeError("builder failed"), SystemExit(2)])
@pytest.mark.parametrize("parent", [4567, 1, 9999])
def test_broken_child_closes_partial_resources_and_always_exits(
    failure: BaseException,
    parent: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(os, "getppid", lambda: parent)
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(f"signal {pid} {sig}"))
    monkeypatch.setattr(os, "_exit", lambda status: calls.append(f"exit {status}"))

    def fail(container: WorkerContainer) -> None:
        # Substitute only external close boundaries; the owner and fatal path stay real.
        monkeypatch.setattr(container, "redis", ClosedClient(calls))
        monkeypatch.setattr(container, "job_queue", FailingQueue())
        monkeypatch.setattr(container, "engine", ClosedEngine(calls))
        raise failure

    monkeypatch.setattr(runtime, "build_container", fail)
    runtime.start_child()
    assert calls[:2] == ["redis closed", "engine closed"]
    assert calls[-1] == "exit 70"
    assert (f"signal 4567 {signal.SIGTERM}" in calls) is (parent == 4567)
    with pytest.raises(RuntimeError):
        runtime.current_loop()


def test_signal_and_logging_failures_cannot_skip_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr(runtime, "build_container", fail)
    monkeypatch.setattr(os, "kill", fail)
    monkeypatch.setattr(runtime.logger, "exception", fail)
    monkeypatch.setattr(os, "_exit", calls.append)
    runtime.start_child()
    assert calls == [70]


def test_child_keeps_one_loop_and_closes_coroutine_outside_runtime() -> None:
    async def identity() -> int:
        return id(asyncio.get_running_loop())

    coroutine = identity()
    with pytest.raises(RuntimeError):
        runtime.run_async(coroutine)
    with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
        coroutine.send(None)
    runtime.start_child()
    try:
        assert runtime.run_async(identity()) == runtime.run_async(identity())
        assert runtime.current_container().settings.worker_role == "transcribe"
    finally:
        runtime.close_child()
    with pytest.raises(RuntimeError):
        runtime.current_container()


def test_interrupted_async_task_is_cancelled_and_drained(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime.start_child()
    loop = runtime.current_loop()
    original = loop.run_until_complete
    cleaned: list[bool] = []
    count = 0

    async def waiting() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            cleaned.append(True)

    def interrupt(future: object) -> object:
        nonlocal count
        count += 1
        if count == 1:
            loop.call_soon(lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        return original(cast(Coroutine[object, object, object], future))

    monkeypatch.setattr(loop, "run_until_complete", interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.run_async(waiting())
        assert cleaned == [True]
        assert not asyncio.all_tasks(loop)
    finally:
        runtime.close_child()


def test_settings_system_exit_uses_the_child_fatal_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fail_settings(model: object) -> None:
        raise SystemExit(2)

    monkeypatch.setattr(runtime, "load_settings", fail_settings)
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(f"signal {pid}"))
    monkeypatch.setattr(os, "_exit", lambda status: calls.append(f"exit {status}"))
    runtime.start_child()
    assert calls == ["signal 4567", "exit 70"]
