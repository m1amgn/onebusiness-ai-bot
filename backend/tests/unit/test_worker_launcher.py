import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import PostgresDsn, ValidationError

from taskbot.entrypoints.settings import load_settings
from taskbot.entrypoints.worker.launcher import (
    beat_schedule_path,
    build_worker_argv,
    launch,
    worker_environment,
)
from taskbot.infrastructure.settings.worker import BrokerSettings, WorkerSettings

ROOT = Path(__file__).resolve().parents[3]
DATABASE_URL = "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:1/taskbot_test_0_00000000"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    for name in (
        "WORKER_ROLE",
        "DATABASE_URL",
        "TELEGRAM_BOT_TOKEN",
        "REDIS_NAMESPACE",
        "REDIS_URL",
        "CELERY_BROKER_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_worker_settings_need_role_and_database_but_no_provider_or_token() -> None:
    with pytest.raises(ValidationError) as caught:
        WorkerSettings()
    assert {error["loc"][0] for error in caught.value.errors()} == {"worker_role", "database_url"}
    settings = WorkerSettings(worker_role="notify", database_url=PostgresDsn(DATABASE_URL))
    assert settings.telegram_bot_token is None
    assert not any(
        "provider" in name or "extractor" in name for name in WorkerSettings.model_fields
    )
    assert BrokerSettings().broker_url == "redis://localhost:6379/1"


@pytest.mark.parametrize(
    ("role", "queue", "concurrency"),
    [("transcribe", "transcription", "1"), ("notify", "notification", "2")],
)
def test_launcher_arguments(role: str, queue: str, concurrency: str) -> None:
    expected = [
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
        expected += ["-B", "-s", "/tmp/schedule"]
    assert build_worker_argv(role, "/tmp/schedule") == expected


def test_environment_and_beat_files_are_scoped() -> None:
    original = {"WORKER_ROLE": "notify"}
    assert worker_environment(original, "darwin") == {**original, "FORKED_BY_MULTIPROCESSING": "1"}
    assert worker_environment(original, "linux") == original
    assert original == {"WORKER_ROLE": "notify"}
    assert beat_schedule_path("test:t1:") == str(
        Path(tempfile.gettempdir()) / f"taskbot-{os.getuid()}" / "celerybeat-schedule-test_t1_"
    )
    assert beat_schedule_path("") == str(
        Path(tempfile.gettempdir()) / f"taskbot-{os.getuid()}" / "celerybeat-schedule"
    )


@pytest.mark.parametrize("arguments", [[], ["bogus"]])
def test_invalid_or_missing_role_never_uses_dotenv(
    arguments: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse_exec(*args: object) -> None:
        raise AssertionError("Invalid role reached Celery exec")

    monkeypatch.setattr(os, "execve", refuse_exec)
    dotenv = tmp_path / ".env"
    dotenv.write_text("WORKER_ROLE=notify\nDATABASE_URL=" + DATABASE_URL + "\n")
    monkeypatch.setenv("TASKBOT_ENV_FILE", str(dotenv))
    with pytest.raises(SystemExit) as caught:
        launch(arguments)
    assert caught.value.code == 2
    assert "Invalid configuration: WORKER_ROLE" in capsys.readouterr().err


def test_process_role_and_extra_arguments_reach_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setenv("WORKER_ROLE", "notify")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(
        os, "execve", lambda executable, argv, env: calls.append((executable, argv, env))
    )
    launch(["--include", "x"])
    executable, argv, env = calls[0]
    assert executable == sys.executable
    assert argv[:3] == [sys.executable, "-m", "celery"]
    assert argv[-2:] == ["--include", "x"]
    assert env["WORKER_ROLE"] == "notify"


def test_notify_launcher_uses_the_child_namespace_in_its_schedule_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("REDIS_NAMESPACE", "taskbot_test_0_01234567:t8:")
    monkeypatch.setattr(os, "execve", lambda executable, argv, env: calls.append(argv))
    launch(["notify"])
    arguments = calls[0]
    assert arguments[arguments.index("-s") + 1] == str(
        Path(tempfile.gettempdir())
        / f"taskbot-{os.getuid()}"
        / "celerybeat-schedule-taskbot_test_0_01234567_t8_"
    )


def test_notify_creates_private_schedule_directory_and_keeps_it_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / f"taskbot-{os.getuid()}"
    calls: list[list[str]] = []
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(os, "execve", lambda executable, argv, env: calls.append(argv))

    launch(["notify"])

    metadata = directory.lstat()
    assert stat.S_ISDIR(metadata.st_mode)
    assert metadata.st_uid == os.getuid()
    assert stat.S_IMODE(metadata.st_mode) == 0o700
    assert calls[0][calls[0].index("-s") + 1] == str(directory / "celerybeat-schedule")
    schedule = directory / "celerybeat-schedule"
    schedule.write_bytes(b"existing schedule")
    launch(["notify"])
    assert len(calls) == 2
    assert schedule.read_bytes() == b"existing schedule"


@pytest.mark.parametrize(
    "untrusted", ["symlink", "dangling-symlink", "file", "public", "group", "wrong-owner"]
)
def test_notify_refuses_untrusted_schedule_directory_before_exec(
    untrusted: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    uid = os.getuid()
    if untrusted == "wrong-owner":
        monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    directory = tmp_path / f"taskbot-{os.getuid()}"
    target = tmp_path / "target"
    if untrusted in ("symlink", "dangling-symlink"):
        if untrusted == "symlink":
            target.mkdir(mode=0o700)
        directory.symlink_to(target, target_is_directory=True)
    elif untrusted == "file":
        directory.write_bytes(b"untrusted")
    else:
        directory.mkdir(mode=0o700)
        if untrusted in ("public", "group"):
            directory.chmod(0o755 if untrusted == "public" else 0o710)
    calls: list[list[str]] = []
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(os, "execve", lambda executable, argv, env: calls.append(argv))

    with pytest.raises(SystemExit) as caught:
        launch(["notify"])

    assert caught.value.code == 2
    assert not calls
    assert capsys.readouterr().err == (
        f"Invalid configuration: beat schedule directory {directory} must be a directory "
        "owned by this user with no group or other access\n"
    )
    assert directory.is_symlink() if "symlink" in untrusted else directory.exists()


def test_transcribe_does_not_create_beat_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(os, "execve", lambda executable, argv, env: calls.append(argv))
    launch(["transcribe"])
    assert len(calls) == 1
    assert "-s" not in calls[0]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["mkdir", "lstat"])
def test_notify_reports_directory_access_failures_before_exec(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError("synthetic filesystem refusal")

    calls: list[list[str]] = []
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setattr(os, "execve", lambda executable, argv, env: calls.append(argv))
    monkeypatch.setattr(os, operation, deny)
    with pytest.raises(SystemExit) as caught:
        launch(["notify"])
    assert caught.value.code == 2
    assert not calls
    assert "Invalid configuration: beat schedule directory" in capsys.readouterr().err


def test_validation_errors_do_not_echo_secret_inputs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WORKER_ROLE", "notify")
    monkeypatch.setenv("DATABASE_URL", "https://test:synthetic-secret@bad")
    with pytest.raises(SystemExit) as caught:
        load_settings(WorkerSettings)
    assert caught.value.code == 2
    output = capsys.readouterr().err
    assert "DATABASE_URL" in output
    assert "synthetic-secret" not in output


async def test_developer_command_rejects_invalid_role() -> None:
    from tests.processes import run_process

    result = await run_process(
        "yarn",
        "dev:worker",
        "bogus",
        cwd=ROOT,
        env={**os.environ, "TASKBOT_ENV_FILE": "/dev/null"},
        timeout_seconds=30,
    )
    assert result.returncode != 0
    assert "Invalid configuration: WORKER_ROLE" in result.stderr
