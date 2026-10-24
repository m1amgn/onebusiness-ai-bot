import os
import subprocess
import sys
from pathlib import Path


def test_collect_only_needs_no_running_infrastructure() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-m", "integration"],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ
        | {
            "DATABASE_URL": "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:59999/taskbot",
            "REDIS_URL": "redis://127.0.0.1:59998/0",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2/" in result.stdout
