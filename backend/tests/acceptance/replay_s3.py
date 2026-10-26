"""Read-only replay of S3's configuration rows and internal-user channel rule."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Literal
from unittest.mock import patch

from taskbot.domain.events import AuthRevoked
from taskbot.infrastructure.redis.client import user_channel
from taskbot.infrastructure.settings.worker import WorkerSettings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:1/taskbot_test_0_00000000"
REDIS_URL = "redis://127.0.0.1:1/0"


async def replay() -> None:
    # A direct script run omits backend/ from sys.path; reuse the subprocess owner.
    sys.path.insert(0, str(BACKEND_ROOT))
    from tests.processes import run_process

    # Override every setting before subprocess imports build the lazy worker app.
    env = dict(
        os.environ,
        TASKBOT_ENV_FILE="/dev/null",
        DATABASE_URL=DATABASE_URL,
        REDIS_URL=REDIS_URL,
        CELERY_BROKER_URL="redis://127.0.0.1:1/1",
        REDIS_NAMESPACE="",
        LOG_LEVEL="INFO",
    )
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("WORKER_ROLE", None)
    roles: list[Literal["transcribe", "notify"]] = ["transcribe", "notify"]
    with patch.dict(os.environ, env, clear=True):
        for role in roles:
            settings = WorkerSettings(_env_file=None, worker_role=role)
            assert settings.telegram_bot_token is None
    print(json.dumps({"row": "R3", "roles": roles, "token_optional": True}))
    for arguments in (["bogus"], []):
        result = await run_process(
            sys.executable,
            "-m",
            "taskbot.entrypoints.worker",
            *arguments,
            cwd=BACKEND_ROOT,
            env=env,
        )
        error = result.stderr.strip()
        assert result.returncode == 2
        assert error.startswith("Invalid configuration: WORKER_ROLE")
        print(
            json.dumps(
                {
                    "row": "R4",
                    "role": arguments[0] if arguments else "missing",
                    "returncode": result.returncode,
                    "error": error,
                }
            )
        )
    event = AuthRevoked(user_id=7)
    channel = user_channel("", event.user_id)
    assert channel == "events:user:7"
    print(json.dumps({"row": "R6", "channel": channel, "telegram_user_id": 1001}))


if __name__ == "__main__":
    asyncio.run(replay())
