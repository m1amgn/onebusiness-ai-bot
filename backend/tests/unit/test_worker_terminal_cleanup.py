import asyncio
import json
import os
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

import pytest

from tests import workers

# The fake foreground job has no broker, database, provider or real worker.
# It ignores terminal hangup so closing the PTY cannot hide failed ownership cleanup.
SUPERVISOR = """
import fcntl,json,os,signal,subprocess,sys,termios,time
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time; signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)'],
    process_group=0)
path = os.environ['S3_CLEANUP_TEST_OWNERSHIP']
with open(path + '.tmp', 'w') as output:
    json.dump({'job_group':child.pid}, output)
os.replace(path + '.tmp', path)
if os.environ['S3_CLEANUP_TEST_RECEIPT'] == 'partial':
    with open(os.environ['TASKBOT_TEST_TERMINAL_STATE'], 'w') as output:
        output.write('{')
while True:
    time.sleep(1)
"""

SURVIVING_CHILD = """
import json,os,signal,subprocess,sys,time
signal.signal(signal.SIGHUP, signal.SIG_IGN)
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
child = subprocess.Popen([sys.executable, '-c',
    'import signal,time; signal.signal(signal.SIGHUP, signal.SIG_IGN); '
    'signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(60)'],
    stdout=subprocess.PIPE,
    process_group=0 if os.environ['S3_CLEANUP_TEST_TERMINAL'] == '1' else None)
child.stdout.readline()
path = os.environ['S3_CLEANUP_TEST_OWNERSHIP']
with open(path + '.tmp', 'w') as output:
    json.dump({'child': child.pid}, output)
os.replace(path + '.tmp', path)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    time.sleep(1)
"""


@pytest.mark.parametrize("terminal", [False, True])
async def test_warm_stop_rejects_and_cleans_an_owned_surviving_child(
    terminal: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ownership = tmp_path / "survivor.json"
    processes: list[asyncio.subprocess.Process] = []
    children: list[int] = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(
        *command: str,
        cwd: Path,
        env: dict[str, str],
        start_new_session: bool,
        stdin: BinaryIO | int | None,
        stdout: BinaryIO | int,
        stderr: BinaryIO | int,
    ) -> asyncio.subprocess.Process:
        # The real spawn keeps the harness's owned session and terminal descriptors.
        process = await original_spawn(
            sys.executable,
            "-c",
            SURVIVING_CHILD,
            cwd=cwd,
            env=env,
            start_new_session=start_new_session,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        processes.append(process)
        return process

    async def ready(self: workers.RunningWorker) -> None:
        async with asyncio.timeout(5):
            # The subprocess receipt has no portable asynchronous notification.
            while not ownership.exists():  # noqa: ASYNC110
                await asyncio.sleep(0.01)
        children.append(int(json.loads(ownership.read_text())["child"]))
        if terminal:
            self.main_pid = children[-1]

    monkeypatch.setenv("S3_CLEANUP_TEST_OWNERSHIP", str(ownership))
    monkeypatch.setenv("S3_CLEANUP_TEST_TERMINAL", str(int(terminal)))
    monkeypatch.setattr(workers, "TERMINAL_SHUTDOWN_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(workers.RunningWorker, "ready", ready)
    try:
        with pytest.raises(AssertionError, match="warm shutdown"):
            async with workers.running_worker(
                "notify",
                "taskbot_test_0_00000000:t1:",
                log_dir=tmp_path,
                developer_command=terminal,
            ):
                pass
        assert processes[0].returncode is not None
        assert not workers.session_groups(processes[0].pid)
    finally:
        # This finalizer owns both known groups even when the regression fails.
        for group in children + [process.pid for process in processes]:
            with suppress(ProcessLookupError):
                os.killpg(group, signal.SIGKILL)
        for process in processes:
            await asyncio.wait_for(process.wait(), 5)
        await asyncio.sleep(0.3)
        assert not any(workers.session_groups(process.pid) for process in processes)


@pytest.mark.parametrize("receipt", ["missing", "partial"])
async def test_cancelled_startup_reaps_terminal_without_a_valid_receipt(
    receipt: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ownership = tmp_path / "test-owned.json"
    processes: list[asyncio.subprocess.Process] = []
    groups: list[int] = []
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(
        *command: str,
        cwd: Path,
        env: dict[str, str],
        start_new_session: bool,
        stdin: BinaryIO | int | None,
        stdout: BinaryIO | int,
        stderr: BinaryIO | int,
    ) -> asyncio.subprocess.Process:
        # Keep the harness's session and PTY, replace only the external program.
        process = await original_spawn(
            sys.executable,
            "-c",
            SUPERVISOR,
            cwd=cwd,
            env=env,
            start_new_session=start_new_session,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        processes.append(process)
        return process

    async def fail_readiness(self: workers.RunningWorker) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if ownership.exists():
                groups.append(int(json.loads(ownership.read_text())["job_group"]))
                # Let the optional corrupt receipt write complete before cancellation.
                await asyncio.sleep(0.1)
                raise asyncio.CancelledError("cancelled before startup receipt")
            await asyncio.sleep(0.01)
        raise AssertionError("Fake supervisor did not create its foreground job")

    monkeypatch.setattr(workers, "TERMINAL_SHUTDOWN_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setenv("S3_CLEANUP_TEST_OWNERSHIP", str(ownership))
    monkeypatch.setenv("S3_CLEANUP_TEST_RECEIPT", receipt)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(workers.RunningWorker, "ready", fail_readiness)
    # The test has an independent finalizer even when the baseline leaks both groups.
    try:
        with pytest.raises(asyncio.CancelledError, match="before startup receipt"):
            async with workers.running_worker(
                "notify",
                "taskbot_test_0_00000000:t1:",
                log_dir=tmp_path,
                developer_command=True,
            ):
                raise AssertionError("Cancelled startup yielded a worker")
        assert processes[0].returncode is not None
        assert not workers.group_pids(processes[0].pid)
        assert groups
        assert not workers.group_pids(groups[0])
    finally:
        for group in groups + [process.pid for process in processes]:
            with suppress(ProcessLookupError):
                os.killpg(group, signal.SIGKILL)
        for process in processes:
            await asyncio.wait_for(process.wait(), 5)
        await asyncio.sleep(0.3)
        assert not any(workers.group_pids(group) for group in groups)
        assert not any(workers.group_pids(process.pid) for process in processes)
