import os
import sys
from pathlib import Path

from tests.processes import COLD_START_TIMEOUT_SECONDS, run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_malformed_bot_token_exits_without_exposing_its_value() -> None:
    result = await run_process(
        sys.executable,
        "-m",
        "taskbot.entrypoints.bot",
        cwd=BACKEND_ROOT,
        timeout_seconds=COLD_START_TIMEOUT_SECONDS,
        env=os.environ
        | {
            "TASKBOT_ENV_FILE": "/dev/null",
            "DATABASE_URL": "postgresql+asyncpg://127.0.0.1:1/test",
            "TELEGRAM_BOT_TOKEN": "private bad token",
            "TASK_EXTRACTOR": "heuristic",
        },
    )
    assert result.returncode == 2
    assert "TELEGRAM_BOT_TOKEN" in result.stderr
    assert "private bad token" not in result.stderr
    assert "Traceback" not in result.stderr


async def test_unreachable_redis_stops_before_telegram_is_constructed() -> None:
    result = await run_process(
        sys.executable,
        "-m",
        "taskbot.entrypoints.bot",
        cwd=BACKEND_ROOT,
        timeout_seconds=COLD_START_TIMEOUT_SECONDS,
        env=os.environ
        | {
            "TASKBOT_ENV_FILE": "/dev/null",
            "DATABASE_URL": "postgresql+asyncpg://127.0.0.1:1/test",
            "TELEGRAM_BOT_TOKEN": "123456:synthetic",
            "TASK_EXTRACTOR": "heuristic",
            "TRANSCRIPTION_PROVIDER": "fake",
            "REDIS_URL": "redis://127.0.0.1:1/0",
        },
    )
    assert result.returncode == 1
    assert "Redis is unavailable at 127.0.0.1:1" in result.stderr
    assert "Traceback" not in result.stderr
    assert "123456:synthetic" not in result.stderr
