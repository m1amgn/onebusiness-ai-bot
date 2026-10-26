import os
import sys
from pathlib import Path

from taskbot.infrastructure.redis.client import create_redis_client
from tests.processes import run_process
from tests.redis_sessions import RedisSession

BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_held_polling_lock_exits_cleanly_without_contacting_telegram(
    redis_session: RedisSession,
) -> None:
    from taskbot.infrastructure.redis.bot_lock import PollingLock

    namespace = redis_session.child()
    async with create_redis_client(redis_session.redis_url) as client:
        lock = PollingLock(client, namespace)
        assert await lock.acquire()
        try:
            result = await run_process(
                sys.executable,
                "-m",
                "taskbot.entrypoints.bot",
                cwd=BACKEND_ROOT,
                env=os.environ
                | {
                    "TASKBOT_ENV_FILE": "/dev/null",
                    "DATABASE_URL": "postgresql+asyncpg://127.0.0.1:1/test",
                    "TELEGRAM_BOT_TOKEN": "123456:synthetic",
                    "TASK_EXTRACTOR": "heuristic",
                    "REDIS_URL": redis_session.redis_url,
                    "REDIS_NAMESPACE": namespace,
                    "TRANSCRIPTION_PROVIDER": "fake",
                },
            )
            assert result.returncode == 1
            assert "Another bot instance holds the polling lock" in result.stderr
            assert "Traceback" not in result.stderr
        finally:
            await lock.release()


async def test_unavailable_database_releases_the_lock_before_exit(
    redis_session: RedisSession,
) -> None:
    from taskbot.infrastructure.redis.bot_lock import POLL_LOCK_KEY

    namespace = redis_session.child()
    result = await run_process(
        sys.executable,
        "-m",
        "taskbot.entrypoints.bot",
        cwd=BACKEND_ROOT,
        env=os.environ
        | {
            "TASKBOT_ENV_FILE": "/dev/null",
            "DATABASE_URL": "postgresql+asyncpg://127.0.0.1:1/test",
            "TELEGRAM_BOT_TOKEN": "123456:synthetic",
            "TASK_EXTRACTOR": "heuristic",
            "REDIS_URL": redis_session.redis_url,
            "REDIS_NAMESPACE": namespace,
            "TRANSCRIPTION_PROVIDER": "fake",
        },
    )
    assert result.returncode == 1
    assert "Database is unavailable or not migrated" in result.stderr
    assert "Traceback" not in result.stderr
    async with create_redis_client(redis_session.redis_url) as client:
        assert not await client.exists(namespace + POLL_LOCK_KEY)
