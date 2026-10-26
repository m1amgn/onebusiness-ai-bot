import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import Field, SecretStr, ValidationError

from taskbot.infrastructure.settings.worker import WorkerSettings
from tests.acceptance import replay_s3
from tests.processes import run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_replay_r3_fails_if_worker_settings_require_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RequiredTokenSettings(WorkerSettings):
        telegram_bot_token: SecretStr | None = Field(...)

    monkeypatch.setattr(replay_s3, "WorkerSettings", RequiredTokenSettings)
    with pytest.raises(ValidationError, match="telegram_bot_token"):
        await replay_s3.replay()


async def test_read_only_s3_replay_checks_token_roles_and_internal_user_channel(
    tmp_path: Path,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "REDIS_URL=invalid\nWORKER_ROLE=notify\nTELEGRAM_BOT_TOKEN=synthetic-secret\n"
    )
    result = await run_process(
        sys.executable,
        "tests/acceptance/replay_s3.py",
        cwd=BACKEND_ROOT,
        timeout_seconds=120,  # Cold SDK imports can be slow on a shared host.
        env=dict(
            os.environ,
            TASKBOT_ENV_FILE=str(dotenv),
            WORKER_ROLE="notify",
            DATABASE_URL="invalid",
            REDIS_URL="invalid",
            CELERY_BROKER_URL="invalid",
            REDIS_NAMESPACE="invalid",
            TELEGRAM_BOT_TOKEN="synthetic-secret",
            LOG_LEVEL="invalid",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "synthetic-secret" not in result.stdout + result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [row["row"] for row in rows] == ["R3", "R4", "R4", "R6"]
    assert rows[0] == {"row": "R3", "roles": ["transcribe", "notify"], "token_optional": True}
    assert [row["role"] for row in rows[1:3]] == ["bogus", "missing"]
    for row in rows[1:3]:
        assert row["returncode"] == 2
        assert row["error"].startswith("Invalid configuration: WORKER_ROLE")
    assert rows[3] == {"row": "R6", "channel": "events:user:7", "telegram_user_id": 1001}
