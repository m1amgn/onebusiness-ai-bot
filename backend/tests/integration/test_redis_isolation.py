import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from taskbot.domain.events import AuthRevoked
from taskbot.domain.jobs import TicketClaims
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.redis.subscription import subscribe_user_events
from taskbot.infrastructure.redis.tickets import RedisTicketStore
from tests.fakes.tokens import FakeTokenGenerator
from tests.integration.settings import InfraTestSettings
from tests.processes import run_process
from tests.redis_sessions import (
    drop_abandoned_namespaces,
    open_redis_session,
    session_lock_path,
)

BACKEND = Path(__file__).resolve().parents[2]


async def keys_for(session_id: str) -> list[str]:
    settings = InfraTestSettings()
    found = []
    for url in (str(settings.redis_url), settings.broker_url):
        async with create_redis_client(url) as client:
            found.extend([key async for key in client.scan_iter(match=f"{session_id}:*")])
    return sorted(found)


async def test_session_cleans_all_adapters_both_databases_and_owner_last() -> None:
    settings = InfraTestSettings()
    async with (
        create_redis_client(str(settings.redis_url)) as client,
        client.monitor() as monitor,
    ):
        async with open_redis_session() as session:
            ns = session.child()
            assert session.child() != ns
            async with subscribe_user_events(client, ns, 7) as subscription:
                await RedisEventPublisher(client, ns).publish(AuthRevoked(user_id=7))
                assert await subscription.next(timeout_seconds=1) is not None
            await RedisTicketStore(client, FakeTokenGenerator(), ns).issue(
                TicketClaims(user_id=7, token_version=1)
            )
            async with create_redis_client(session.broker_url) as broker:
                await broker.set(ns + "broker", "synthetic")
            owner_key = session.namespace + "owner"
            assert await client.exists(owner_key)
            lock = session_lock_path(session.id)
            assert lock.stat().st_mode & 0o777 == 0o600
        commands = []
        async with asyncio.timeout(3):
            while True:
                command = str((await monitor.next_command())["command"])
                if command.startswith("UNLINK ") and session.id in command:
                    commands.append(command)
                    if command == "UNLINK " + owner_key:
                        break
        assert len(commands) >= 3
        assert all(owner_key not in command for command in commands[:-1])
    assert await keys_for(session.id) == []
    assert not lock.exists()


OWNER_SCRIPT = """
import asyncio, json
from taskbot.infrastructure.redis.client import create_redis_client
from tests.redis_sessions import open_redis_session
async def main():
    async with open_redis_session() as session:
        async with create_redis_client(session.redis_url, client_name=session.id) as client:
            await client.set(session.namespace + "owned", "synthetic")
            async with create_redis_client(session.broker_url, client_name=session.id) as broker:
                await broker.set(session.namespace + "broker", "synthetic")
                print(json.dumps({"id": session.id}), flush=True)
                await asyncio.Event().wait()
asyncio.run(main())
"""


@asynccontextmanager
async def redis_owner() -> AsyncIterator[tuple[subprocess.Popen[bytes], str]]:
    process = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-c", OWNER_SCRIPT],
        cwd=BACKEND,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    session_id: str | None = None
    try:
        assert process.stdout is not None
        line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=15)
        session_id = str(json.loads(line)["id"])
        yield process, session_id
    finally:
        # Poll only after the test's unreaped-owner proof has finished.
        if process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await asyncio.to_thread(process.communicate)
        if session_id is not None:
            await drop_abandoned_namespaces(
                datetime.now(UTC) + timedelta(hours=7), ids={session_id}
            )


async def assert_sweep_keeps_paused_owner(session_id: str, future: datetime) -> None:
    assert await drop_abandoned_namespaces(future, ids={session_id}) == []
    settings = InfraTestSettings()
    for url in (str(settings.redis_url), settings.broker_url):
        async with create_redis_client(url) as control:
            for connection in await control.client_list():
                if connection["name"] == session_id:
                    await control.client_kill_filter(_id=connection["id"])
    assert await drop_abandoned_namespaces(future, ids={session_id}) == []
    script = (
        "import asyncio; from datetime import datetime; "
        "from tests.redis_sessions import drop_abandoned_namespaces; "
        f"now=datetime.fromisoformat({future.isoformat()!r}); "
        f"print(asyncio.run(drop_abandoned_namespaces(now, ids={{{session_id!r}}})))"
    )
    env = dict(os.environ, TZ="UTC", LC_ALL="C")
    result = await run_process(sys.executable, "-c", script, cwd=BACKEND, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


async def test_paused_disconnected_owner_is_kept_then_killed_owner_is_cleaned() -> None:
    async with redis_owner() as (process, session_id):
        future = datetime.now(UTC) + timedelta(hours=7)
        os.kill(process.pid, signal.SIGSTOP)
        before = await keys_for(session_id)
        assert len(before) == 3
        await assert_sweep_keeps_paused_owner(session_id, future)
        assert await keys_for(session_id) == before
        os.kill(process.pid, signal.SIGKILL)
        await asyncio.sleep(0.1)
        # Popen has not been waited/polled: the owner remains unreaped during this proof.
        removed = await asyncio.gather(
            drop_abandoned_namespaces(future, ids={session_id}),
            drop_abandoned_namespaces(future, ids={session_id}),
        )
        assert session_id in removed[0] + removed[1]
        assert await keys_for(session_id) == []
        assert not session_lock_path(session_id).exists()


@pytest.mark.parametrize(
    "record_kind", ["missing", "corrupt", "other_host", "missing_lock", "bad_path", "wrongtype"]
)
async def test_unknown_ownership_is_retained(record_kind: str, tmp_path: Path) -> None:
    session_id = f"taskbot_test_{int(time.time())}_{secrets.token_hex(4)}"
    settings = InfraTestSettings()
    lock = session_lock_path(session_id)
    lock.parent.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_path / "foreign.lock"
    sentinel.write_text("do not touch")
    if record_kind != "missing_lock":
        lock.touch(mode=0o600)
    record = json.dumps({"host": socket.gethostname(), "lock": str(lock)})
    if record_kind == "corrupt":
        record = "not JSON"
    elif record_kind == "other_host":
        record = json.dumps({"host": "another-host", "lock": str(lock)})
    elif record_kind == "bad_path":
        record = json.dumps({"host": socket.gethostname(), "lock": str(sentinel)})
    async with create_redis_client(str(settings.redis_url)) as client:
        try:
            await client.set(session_id + ":key", "synthetic")
            if record_kind == "wrongtype":
                # redis-py shares a sync/async command annotation for LPUSH.
                await client.lpush(session_id + ":owner", "invalid record")  # type: ignore[misc]
            elif record_kind != "missing":
                await client.set(session_id + ":owner", record)
            before = await keys_for(session_id)
            assert (
                await drop_abandoned_namespaces(
                    datetime.now(UTC) + timedelta(hours=7), ids={session_id}
                )
                == []
            )
            assert await keys_for(session_id) == before
            assert sentinel.read_text() == "do not touch"
        finally:
            # These records and keys belong to this test, including deliberately invalid records.
            await client.unlink(session_id + ":key", session_id + ":owner")
            lock.unlink(missing_ok=True)


async def test_failed_session_entry_removes_its_unrecorded_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from redis.exceptions import RedisError

    from tests import redis_sessions

    paths: list[Path] = []

    def track_lock(session_id: str) -> Path:
        path = session_lock_path(session_id)
        paths.append(path)
        return path

    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.setattr(redis_sessions, "session_lock_path", track_lock)
    session = open_redis_session()
    with pytest.raises(RedisError):
        await session.__aenter__()
    assert len(paths) == 1
    assert not paths[0].exists()


async def test_interrupted_cleanup_retains_ownership_for_the_next_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from redis.asyncio import Redis

    from tests import redis_sessions

    original_remove = redis_sessions._remove_keys
    calls = 0

    async def interrupt_broker_cleanup(client: Redis, session_id: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interrupted cleanup")
        await original_remove(client, session_id)

    monkeypatch.setattr(redis_sessions, "_remove_keys", interrupt_broker_cleanup)
    context = open_redis_session()
    session = await context.__aenter__()
    async with create_redis_client(session.broker_url) as broker:
        await broker.set(session.child() + "pending", "synthetic")
    with pytest.raises(RuntimeError, match="interrupted cleanup"):
        await context.__aexit__(None, None, None)
    monkeypatch.setattr(redis_sessions, "_remove_keys", original_remove)
    lock = session_lock_path(session.id)
    try:
        assert lock.exists()
        assert len(await keys_for(session.id)) == 2
        assert session.id in await drop_abandoned_namespaces(
            datetime.now(UTC) + timedelta(hours=7), ids={session.id}
        )
        assert await keys_for(session.id) == []
        assert not lock.exists()
    finally:
        await drop_abandoned_namespaces(datetime.now(UTC) + timedelta(hours=7), ids={session.id})
