import os
import sys
from pathlib import Path

import pytest

from tests.processes import run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("value", ["0", "-1", "9223372036854775808", "abc"])
async def test_seed_refuses_invalid_user_id_before_settings_or_database(value: str) -> None:
    result = await run_process(
        sys.executable,
        "-m",
        "taskbot.entrypoints.seed",
        value,
        cwd=BACKEND_ROOT,
        env=dict(os.environ, DATABASE_URL="invalid-to-prove-no-database-access"),
    )
    assert result.returncode == 2
    assert "positive 64-bit integer" in result.stderr
    assert "Traceback" not in result.stderr
