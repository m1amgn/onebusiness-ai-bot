import os
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path

from taskbot.entrypoints.worker.settings import load_worker_settings


def build_worker_argv(role: str, schedule_path: str) -> list[str]:
    if role not in ("transcribe", "notify"):
        raise ValueError("Invalid worker role")
    queue, concurrency = ("transcription", "1") if role == "transcribe" else ("notification", "2")
    arguments = [
        "-A",
        "taskbot.entrypoints.worker",
        "worker",
        "-Q",
        queue,
        "-n",
        role + "@%h",
        "--pool",
        "prefork",
        "--concurrency",
        concurrency,
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
        "-l",
        "INFO",
    ]
    if role == "notify":
        arguments.extend(("-B", "-s", schedule_path))
    return arguments


def worker_environment(environ: Mapping[str, str], platform: str) -> dict[str, str]:
    result = dict(environ)
    if platform == "darwin":
        result["FORKED_BY_MULTIPROCESSING"] = "1"
    return result


def beat_schedule_path(namespace: str) -> str:
    suffix = "-" + namespace.replace(":", "_") if namespace else ""
    directory = Path(tempfile.gettempdir()) / f"taskbot-{os.getuid()}"
    return str(directory / ("celerybeat-schedule" + suffix))


def _prepare_beat_directory(directory: Path) -> None:
    try:
        with suppress(FileExistsError):
            os.mkdir(directory, 0o700)
        metadata = os.lstat(directory)
        if (
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == os.getuid()
            and metadata.st_mode & 0o077 == 0
        ):
            return
    except OSError:
        pass
    print(
        f"Invalid configuration: beat schedule directory {directory} must be a directory "
        "owned by this user with no group or other access",
        file=sys.stderr,
    )
    raise SystemExit(2)


def launch(arguments: Sequence[str]) -> None:
    extra = list(arguments)
    role = extra.pop(0) if extra and not extra[0].startswith("-") else os.environ.get("WORKER_ROLE")
    if role is not None:
        os.environ["WORKER_ROLE"] = role
    settings = load_worker_settings()
    role = settings.worker_role
    schedule = beat_schedule_path(settings.redis_namespace)
    if role == "notify":
        _prepare_beat_directory(Path(schedule).parent)
    argv = [
        sys.executable,
        "-m",
        "celery",
        *build_worker_argv(role, schedule),
        *extra,
    ]
    os.execve(sys.executable, argv, worker_environment(os.environ, sys.platform))
