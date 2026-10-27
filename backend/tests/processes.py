import asyncio
import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

# Cold SDK imports and complete test collection can exceed the normal process budget.
COLD_START_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


async def run_process(
    *command: str, cwd: Path, env: dict[str, str] | None = None, timeout_seconds: float = 30
) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except BaseException as error:
        # A wrapper can exit while its child still holds a pipe. Kill the whole group.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.communicate()
        if isinstance(error, TimeoutError):
            raise TimeoutError(f"Command timed out after {timeout_seconds} seconds") from error
        raise
    assert process.returncode is not None
    return ProcessResult(process.returncode, stdout.decode(), stderr.decode())
