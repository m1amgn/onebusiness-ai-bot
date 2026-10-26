"""Process-owned Redis namespaces; unknown ownership always means retention."""

import fcntl
import logging
import os
import re
import secrets
import socket
import stat
import tempfile
import time
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from taskbot.domain.time import require_aware
from taskbot.infrastructure.redis.client import create_redis_client, namespaced
from tests.integration.settings import InfraTestSettings

PATTERN = re.compile(r"taskbot_test_([0-9]+)_[0-9a-f]{8}")
logger = logging.getLogger(__name__)
_active_ids: set[str] = set()


class OwnerRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    host: str
    lock: str


@dataclass
class RedisSession:
    id: str
    namespace: str
    redis_url: str
    broker_url: str
    _counter: int = field(default=0, init=False, repr=False)

    def child(self) -> str:
        self._counter += 1
        return namespaced(self.namespace, f"t{self._counter}:")


def candidate_id(key: str) -> str | None:
    session_id, separator, _ = key.partition(":")
    return session_id if separator and PATTERN.fullmatch(session_id) else None


def session_lock_path(session_id: str) -> Path:
    if PATTERN.fullmatch(session_id) is None:
        raise ValueError("Invalid Redis test session id")
    return Path(tempfile.gettempdir()).resolve() / "taskbot-redis-sessions" / f"{session_id}.lock"


def _unlink_lock(path: Path, descriptor: int) -> None:
    # Never remove a replacement file or a symlink at the canonical path.
    try:
        stored = path.lstat()
    except FileNotFoundError:
        return
    held = os.fstat(descriptor)
    if stat.S_ISREG(stored.st_mode) and (stored.st_dev, stored.st_ino) == (
        held.st_dev,
        held.st_ino,
    ):
        path.unlink()


async def _remove_keys(client: Redis, session_id: str) -> None:
    owner_key = namespaced(session_id + ":", "owner")
    batch: list[str] = []
    async for key in client.scan_iter(match=session_id + ":*", count=500):
        if key == owner_key:
            continue
        batch.append(key)
        if len(batch) == 500:
            await client.unlink(*batch)
            batch.clear()
    if batch:
        await client.unlink(*batch)


async def _remove_session(app: Redis, broker: Redis, session_id: str) -> None:
    await _remove_keys(app, session_id)
    await _remove_keys(broker, session_id)
    # A partial cleanup retains the ownership proof for the next sweep.
    await app.unlink(namespaced(session_id + ":", "owner"))


@asynccontextmanager
async def open_redis_session() -> AsyncIterator[RedisSession]:
    settings = InfraTestSettings()
    session_id = f"taskbot_test_{int(time.time())}_{secrets.token_hex(4)}"
    path = session_lock_path(session_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    recorded = False
    cleaned = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        async with (
            create_redis_client(str(settings.redis_url), client_name=session_id) as app,
            create_redis_client(settings.broker_url, client_name=session_id) as broker,
        ):
            owner = OwnerRecord(host=socket.gethostname(), lock=str(path))
            recorded = (
                await app.set(
                    namespaced(session_id + ":", "owner"), owner.model_dump_json(), nx=True
                )
                is True
            )
            if not recorded:
                raise RuntimeError("Redis test session ownership collision")
            _active_ids.add(session_id)
            try:
                yield RedisSession(
                    session_id, session_id + ":", str(settings.redis_url), settings.broker_url
                )
            finally:
                await _remove_session(app, broker, session_id)
                cleaned = True
    finally:
        _active_ids.discard(session_id)
        try:
            if cleaned or not recorded:
                _unlink_lock(path, descriptor)
        finally:
            os.close(descriptor)


async def _read_owner_record(app: Redis, session_id: str) -> OwnerRecord | None:
    try:
        raw = await app.get(namespaced(session_id + ":", "owner"))
        if raw is not None:
            return OwnerRecord.model_validate_json(raw)
    except ValidationError, ResponseError, UnicodeError:
        logger.info("Keeping Redis session %s: unreadable owner record", session_id)
        return None
    logger.info("Keeping Redis session %s: missing owner record", session_id)
    return None


async def _dead_owner_lock(app: Redis, session_id: str) -> int | None:
    owner = await _read_owner_record(app, session_id)
    if owner is None:
        return None
    path = session_lock_path(session_id)
    if owner.host != socket.gethostname() or owner.lock != str(path):
        logger.info("Keeping Redis session %s: unknown host or noncanonical lock path", session_id)
        return None
    descriptor: int | None = None
    try:
        # No O_CREAT: a missing lock file is unknown ownership, never proof of death.
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Owner lock is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        logger.info("Keeping Redis session %s: missing, unreadable or held lock", session_id)
        return None


async def _candidates(
    clients: tuple[Redis, Redis], now: datetime, ids: Collection[str] | None
) -> set[str]:
    candidates: set[str] = set()
    cutoff = now.timestamp() - 6 * 60 * 60
    for client in clients:
        async for key in client.scan_iter(match="taskbot_test_*", count=500):
            session_id = candidate_id(key)
            if session_id is None or session_id in _active_ids:
                continue
            match = PATTERN.fullmatch(session_id)
            if (
                match is not None
                and int(match.group(1)) < cutoff
                and (ids is None or session_id in ids)
            ):
                candidates.add(session_id)
    return candidates


async def drop_abandoned_namespaces(
    now: datetime, *, ids: Collection[str] | None = None
) -> list[str]:
    require_aware(now)
    settings = InfraTestSettings()
    removed = []
    async with (
        create_redis_client(str(settings.redis_url)) as app,
        create_redis_client(settings.broker_url) as broker,
    ):
        for session_id in sorted(await _candidates((app, broker), now, ids)):
            descriptor = await _dead_owner_lock(app, session_id)
            if descriptor is None:
                continue
            try:
                await _remove_session(app, broker, session_id)
                _unlink_lock(session_lock_path(session_id), descriptor)
                removed.append(session_id)
            finally:
                os.close(descriptor)
    return removed
