"""Owned real-worker processes, namespaced broker traffic and file-backed task receipts."""

import asyncio
import json
import os
import pty
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit
from uuid import uuid4

from celery import Celery

from taskbot.entrypoints.worker.launcher import beat_schedule_path
from taskbot.infrastructure.celery.app import create_celery_app
from tests.databases import is_test_database
from tests.integration.settings import InfraTestSettings
from tests.processes import ProcessResult, run_process

BACKEND = Path(__file__).resolve().parents[1]
TERMINAL_SHUTDOWN_TIMEOUT_SECONDS = 30.0
DATABASE_URL = "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:1/taskbot_test_0_00000000"


def read_records(path: Path, marker: str) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        row
        for line in path.read_text().splitlines()
        if (row := json.loads(line))["marker"] == marker or row.get("event") == marker
    ]


async def wait_records(
    path: Path, marker: str, *, count: int = 1, timeout_seconds: float = 10
) -> list[dict[str, object]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        rows = read_records(path, marker)
        if len(rows) >= count:
            return rows
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for {count} {marker} records: {read_records(path, marker)}"
    )


def worker_environment(
    namespace: str,
    log_dir: Path,
    *,
    database_url: str = DATABASE_URL,
    env: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    if not namespace.startswith("taskbot_test_"):
        raise ValueError("Workers require an owned test namespace")
    log_dir.mkdir(parents=True, exist_ok=True)
    settings = InfraTestSettings()
    result = dict(os.environ)
    for credential in ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        result.pop(credential, None)
    for key in (
        "TASKBOT_TEST_FAIL_CHILD_RUNTIME",
        "TASKBOT_TEST_REPLACEMENT_INIT_DELAY",
        "TASKBOT_TEST_ALIVE_TIMEOUT",
        "TASKBOT_WORKER_MAIN_PID",
    ):
        result.pop(key, None)
    result.update(
        REDIS_URL=str(settings.redis_url),
        CELERY_BROKER_URL=settings.broker_url,
        REDIS_NAMESPACE=namespace,
        TASKBOT_ENV_FILE="/dev/null",
        DATABASE_URL=database_url,
        TRANSCRIPTION_PROVIDER="fake",
        TASK_EXTRACTOR="heuristic",
        TASKBOT_TEST_PARENT_PID=str(os.getpid()),
        PYTHONUNBUFFERED="1",
        TASKBOT_TEST_CHILD_STARTS=str(log_dir / ("child-" + uuid4().hex)),
    )
    for key, value in (env or {}).items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    if not is_test_database(urlsplit(result["DATABASE_URL"]).path.removeprefix("/")):
        raise ValueError("Workers require an owned test database")
    return result


def process_groups() -> list[tuple[int, int]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid="], capture_output=True, text=True, check=True, timeout=5
    )
    return [
        (int(fields[0]), int(fields[1]))
        for row in result.stdout.splitlines()
        if len(fields := row.split()) == 2
    ]


def group_pids(group: int) -> list[int]:
    return [pid for pid, candidate in process_groups() if candidate == group]


def session_groups(session_id: int) -> set[int]:
    # start_new_session makes the recorded subprocess PID our ownership boundary,
    # even before the supervisor can write any diagnostic receipt.
    groups: set[int] = set()
    for pid, group in process_groups():
        with suppress(ProcessLookupError):
            if group > 1 and os.getsid(pid) == session_id:
                groups.add(group)
    return groups


@dataclass
class RunningWorker:
    role: str
    namespace: str
    process: asyncio.subprocess.Process
    app: Celery
    env: dict[str, str]
    log_path: Path
    node: str
    queue: str
    schedule_path: Path
    terminal_fd: int | None = None
    main_pid: int | None = None

    @property
    def terminal_state(self) -> dict[str, int]:
        return dict(json.loads(Path(self.env["TASKBOT_TEST_TERMINAL_STATE"]).read_text()))

    def ctrl_c(self) -> None:
        if self.terminal_fd is None:
            raise RuntimeError("Worker has no controlling terminal")
        os.write(self.terminal_fd, b"\x03")

    async def assert_owned_beat_schedule(self) -> None:
        assert self.role == "notify"
        root = Path(self.env["TMPDIR"])
        assert self.schedule_path.parent == root / f"taskbot-{os.getuid()}"
        assert self.schedule_path.name.endswith(self.namespace.replace(":", "_"))
        async with asyncio.timeout(30):
            # Schedule-file creation has no portable asynchronous notification.
            while not self.schedule_path.exists():  # noqa: ASYNC110
                await asyncio.sleep(0.05)
        # This private directory was empty at launch. A namespace regression cannot
        # read, create or delete the shared development worker's schedule file.
        schedules = await asyncio.to_thread(lambda: list(root.rglob("celerybeat-schedule*")))
        assert all(
            path.parent == self.schedule_path.parent
            and path.name.startswith(self.schedule_path.name)
            for path in schedules
        )

    @property
    def pid(self) -> int:
        return self.main_pid or self.process.pid

    async def send(self, name: str, *, countdown: int = 0, **kwargs: object) -> None:
        await asyncio.to_thread(
            self.app.send_task, name, kwargs=kwargs, queue=self.queue, countdown=countdown or None
        )

    async def cli_ping(self) -> ProcessResult:
        return await run_process(
            sys.executable,
            "-m",
            "celery",
            "-A",
            "taskbot.entrypoints.worker",
            "inspect",
            "ping",
            "-d",
            self.node,
            "--timeout",
            "2",
            cwd=BACKEND,
            env=self.env,
            timeout_seconds=30,
        )

    async def ready(self) -> None:
        deadline = time.monotonic() + 30
        while self.process.returncode is None and time.monotonic() < deadline:
            result = await asyncio.to_thread(
                self.app.control.inspect(destination=[self.node], timeout=1).ping
            )
            if result and self.node in result:
                if self.terminal_fd is not None:
                    stats = await asyncio.to_thread(
                        self.app.control.inspect(destination=[self.node], timeout=1).stats
                    )
                    self.main_pid = int(stats[self.node]["pid"])
                return
            await asyncio.sleep(0.1)
        raise AssertionError("Worker did not become ready: " + self.log_path.read_text())

    async def wait_for_log(self, text: str, *, timeout_seconds: float = 30) -> str:
        async with asyncio.timeout(timeout_seconds):
            # Log files have no cross-process asyncio notification.
            while text not in (output := self.log_path.read_text()):  # noqa: ASYNC110
                await asyncio.sleep(0.05)
            return output

    async def stop(self) -> None:
        if self.terminal_fd is not None:
            await self._stop_terminal()
            return
        timed_out = False
        remaining: list[int] = []
        forced = False
        try:
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    os.kill(self.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), 30)
                except TimeoutError:
                    timed_out = True
            deadline = time.monotonic() + (0 if timed_out else 3)
            # Process-group membership has no portable asynchronous notification.
            while group_pids(self.pid) and time.monotonic() < deadline:  # noqa: ASYNC110
                await asyncio.sleep(0.1)
            remaining = group_pids(self.pid)
        finally:
            forced = bool(group_pids(self.pid))
            if forced:
                with suppress(ProcessLookupError):
                    os.killpg(self.pid, signal.SIGKILL)
            await asyncio.wait_for(self.process.wait(), 5)
            if forced:
                await asyncio.sleep(0.3)
        assert not group_pids(self.pid), f"Worker group survived: {remaining}"
        message = f"Worker warm shutdown required SIGKILL or left processes: {remaining}"
        assert not timed_out, message
        assert not remaining, message
        assert not forced, message

    async def _stop_terminal(self) -> None:
        session_id = self.process.pid
        remaining: set[int] = set()
        forced = False
        try:
            if self.main_pid is not None:
                with suppress(ProcessLookupError):
                    if os.getsid(self.main_pid) == session_id:
                        os.kill(self.main_pid, signal.SIGTERM)
            elif self.process.returncode is None:
                # Stop startup at its owner when readiness has not identified Celery.
                with suppress(ProcessLookupError):
                    self.process.terminate()
            deadline = time.monotonic() + TERMINAL_SHUTDOWN_TIMEOUT_SECONDS
            # OS session membership has no portable asynchronous notification.
            while time.monotonic() < deadline:
                if not session_groups(session_id) - {session_id}:
                    break
                await asyncio.sleep(0.1)
            # Snapshot before the supervisor's finalizer can erase a leaked child.
            remaining = session_groups(session_id) - {session_id}
        finally:
            try:
                forced = await self._reap_terminal_supervisor()
            finally:
                groups = session_groups(session_id)
                forced = forced or bool(groups)
                for group in groups:
                    with suppress(ProcessLookupError):
                        os.killpg(group, signal.SIGKILL)
                if groups:
                    await asyncio.sleep(0.3)
        assert not session_groups(session_id), "Owned terminal session survived"
        message = f"Terminal warm shutdown required SIGKILL or left groups: {sorted(remaining)}"
        assert not remaining, message
        assert not forced, message

    async def _reap_terminal_supervisor(self) -> bool:
        forced = False
        if self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), 5)
        finally:
            if self.process.returncode is None:
                forced = True
                with suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
        return forced


@asynccontextmanager
async def running_worker(
    role: str,
    namespace: str,
    *,
    log_dir: Path,
    replacement_delay: int = 0,
    alive_timeout: int | None = None,
    fail_runtime: bool = False,
    wait_ready: bool = True,
    developer_command: bool = False,
    database_url: str = DATABASE_URL,
    env: Mapping[str, str | None] | None = None,
    include: Sequence[str] = ("tests.worker_tasks",),
) -> AsyncIterator[RunningWorker]:
    env = worker_environment(namespace, log_dir, database_url=database_url, env=env)
    env["TASKBOT_TEST_TERMINAL_STATE"] = env["TASKBOT_TEST_CHILD_STARTS"] + "-terminal.json"
    env["TASKBOT_TEST_REPLACEMENT_INIT_DELAY"] = str(replacement_delay)
    if alive_timeout is not None:
        if alive_timeout != 4:
            raise ValueError("Only the four-second replacement negative control may override")
        env["TASKBOT_TEST_ALIVE_TIMEOUT"] = str(alive_timeout)
    if fail_runtime:
        env["TASKBOT_TEST_FAIL_CHILD_RUNTIME"] = "1"
    path = log_dir / f"{role}-{uuid4().hex}.log"
    command = (
        ["yarn", "dev:worker"]
        if developer_command
        else [sys.executable, "-m", "taskbot.entrypoints.worker"]
    )
    app = create_celery_app(broker_url=env["CELERY_BROKER_URL"], namespace=namespace)
    schedule = private_schedule(namespace, log_dir, env)
    try:
        with worker_output(path, terminal=developer_command) as (output, stdin, terminal_fd):
            if developer_command:
                command = [sys.executable, str(BACKEND / "tests/worker_terminal.py"), *command]
            process = await asyncio.create_subprocess_exec(
                *command,
                role,
                *(["--include", ",".join(include)] if include else []),
                cwd=BACKEND.parent if developer_command else BACKEND,
                env=env,
                start_new_session=True,
                stdin=stdin,
                stdout=output,
                stderr=output,
            )
            worker = RunningWorker(
                role,
                namespace,
                process,
                app,
                env,
                path,
                role + "@" + socket.gethostname(),
                "transcription" if role == "transcribe" else "notification",
                schedule,
                terminal_fd,
            )
            failed = False
            try:
                if wait_ready:
                    await worker.ready()
                yield worker
            except BaseException:
                failed = True
                raise
            finally:
                try:
                    await worker.stop()
                except AssertionError:
                    if not failed:
                        raise
    finally:
        app.close()
        remove_worker_files(schedule, env)


def private_schedule(namespace: str, log_dir: Path, env: dict[str, str]) -> Path:
    directory = log_dir / ("beat-" + uuid4().hex)
    directory.mkdir()
    env["TMPDIR"] = str(directory)
    return directory / f"taskbot-{os.getuid()}" / Path(beat_schedule_path(namespace)).name


def remove_worker_files(schedule: Path, env: dict[str, str]) -> None:
    Path(env["TASKBOT_TEST_CHILD_STARTS"]).unlink(missing_ok=True)
    Path(env["TASKBOT_TEST_TERMINAL_STATE"]).unlink(missing_ok=True)
    # Yarn and uv create launcher shims/locks under TMPDIR too. The harness created
    # this private directory, and every child that used it has already exited.
    root = Path(env["TMPDIR"])
    assert schedule.parent == root / f"taskbot-{os.getuid()}"
    shutil.rmtree(root)


@contextmanager
def worker_output(
    path: Path, *, terminal: bool
) -> Iterator[tuple[BinaryIO | int, int | None, int | None]]:
    with path.open("wb") as output:
        if not terminal:
            yield output, None, None
            return
        master, slave = pty.openpty()
        loop = asyncio.get_running_loop()
        os.set_blocking(master, False)

        def copy_terminal() -> None:
            try:
                data = os.read(master, 65536)
            except OSError:
                loop.remove_reader(master)
                return
            output.write(data)
            output.flush()

        loop.add_reader(master, copy_terminal)
        try:
            yield slave, slave, master
        finally:
            loop.remove_reader(master)
            os.close(master)
            os.close(slave)
