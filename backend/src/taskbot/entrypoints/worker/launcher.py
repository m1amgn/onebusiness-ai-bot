import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from taskbot.entrypoints.settings import load_settings
from taskbot.infrastructure.settings.worker import WorkerSettings


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
    return str(Path(tempfile.gettempdir()) / ("celerybeat-schedule" + suffix))


def launch(arguments: Sequence[str]) -> None:
    extra = list(arguments)
    role = extra.pop(0) if extra and not extra[0].startswith("-") else os.environ.get("WORKER_ROLE")
    if role not in ("transcribe", "notify"):
        print("Invalid configuration: WORKER_ROLE: choose transcribe or notify", file=sys.stderr)
        raise SystemExit(2)
    os.environ["WORKER_ROLE"] = role
    settings = load_settings(WorkerSettings)
    argv = [
        sys.executable,
        "-m",
        "celery",
        *build_worker_argv(role, beat_schedule_path(settings.redis_namespace)),
        *extra,
    ]
    os.execve(sys.executable, argv, worker_environment(os.environ, sys.platform))
