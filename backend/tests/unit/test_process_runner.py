import sys
from pathlib import Path

import pytest

CURRENT_DIRECTORY = Path.cwd()


@pytest.mark.parametrize(
    "script",
    [
        "import time; time.sleep(30)",
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])",
    ],
)
async def test_timeout_kills_the_process_group(script: str) -> None:
    from tests.processes import run_process

    with pytest.raises(TimeoutError, match="timed out"):
        await run_process(sys.executable, "-c", script, cwd=CURRENT_DIRECTORY, timeout_seconds=0.15)


async def test_process_runner_collects_output_and_bounds_a_hang() -> None:
    from tests.processes import run_process

    result = await run_process(sys.executable, "-c", "print('ready')", cwd=CURRENT_DIRECTORY)
    assert result.returncode == 0
    assert result.stdout.strip() == "ready"
    with pytest.raises(TimeoutError, match="timed out"):
        await run_process(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            cwd=CURRENT_DIRECTORY,
            timeout_seconds=0.1,
        )
