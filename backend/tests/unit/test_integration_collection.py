import os
import re
import sys
from pathlib import Path

from tests.processes import run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_collect_only_needs_no_running_infrastructure() -> None:
    result = await run_process(
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-m",
        "integration",
        "-q",
        cwd=BACKEND_ROOT,
        env=os.environ
        | {
            "DATABASE_URL": "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:59999/taskbot",
            "REDIS_URL": "redis://127.0.0.1:59998/0",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    baseline = await run_process(
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-m",
        "integration",
        "-q",
        cwd=BACKEND_ROOT,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    expected = [
        line for line in baseline.stdout.splitlines() if line.startswith("tests/") and "::" in line
    ]
    actual = [
        line for line in result.stdout.splitlines() if line.startswith("tests/") and "::" in line
    ]
    assert actual == expected
    assert actual
    summary = re.search(r"(\d+)(?:/\d+)? tests collected", result.stdout)
    assert summary is not None
    assert int(summary.group(1)) == len(expected)
