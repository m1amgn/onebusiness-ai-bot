import logging
import os
import sys
from types import SimpleNamespace

import pytest

from taskbot.domain.jobs import SweepJob
from taskbot.entrypoints.worker import app, runtime
from taskbot.entrypoints.worker import signals as worker_signals


@pytest.fixture(autouse=True)
def settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    monkeypatch.setenv("WORKER_ROLE", "notify")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:1/taskbot_test_0_00000000"
    )
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://user:synthetic-password@127.0.0.1:1/1")
    monkeypatch.setenv("REDIS_NAMESPACE", "test:t1:")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(app, "send_task", lambda *args, **kwargs: None)


def test_main_init_records_actual_pid_without_building_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("TASKBOT_WORKER_MAIN_PID", raising=False)

    def connect_nothing(*args: object, **kwargs: object) -> None:
        raise AssertionError("Main process built child resources")

    monkeypatch.setattr(runtime, "build_container", connect_nothing)
    worker_signals.worker_init(sender=None)
    assert os.environ["TASKBOT_WORKER_MAIN_PID"] == str(os.getpid())


def test_direct_prefork_is_refused_on_macos(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("FORKED_BY_MULTIPROCESSING", raising=False)
    with pytest.raises(SystemExit) as caught:
        worker_signals.worker_init(
            sender=SimpleNamespace(
                pool_cls=SimpleNamespace(__module__="celery.concurrency.prefork")
            )
        )
    assert caught.value.code == 2
    assert (
        "Run the worker through python -m taskbot.entrypoints.worker on macOS"
        in capsys.readouterr().err
    )


def test_ready_logs_role_queue_node_namespace_without_broker_password(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "CELERY_BROKER_URL",
        "redis://user:synthetic-password@127.0.0.1:1/1?password=synthetic-query#synthetic-fragment",
    )
    with caplog.at_level(logging.INFO):
        worker_signals.worker_ready(sender=SimpleNamespace(hostname="notify@test", app=app))
    text = caplog.text
    for value in ("notify", "notification", "notify@test", "test:t1:", "redis://user:"):
        assert value in text
    assert "synthetic-password" not in text
    assert "synthetic-query" not in text
    assert "synthetic-fragment" not in text
    assert text.count("TELEGRAM_BOT_TOKEN is not set") == 1
    assert "No job handler is registered" not in text


def test_notify_ready_sends_a_short_lived_startup_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def send(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(app, "send_task", send)
    worker_signals.worker_ready()
    assert calls == [((SweepJob.name,), {"kwargs": {}, "expires": 60})]


def test_startup_sweep_failure_logs_only_the_exception_class(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("private broker credentials")

    monkeypatch.setattr(app, "send_task", fail)
    worker_signals.worker_ready()
    assert "RuntimeError" in caplog.text
    assert "private broker credentials" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_transcribe_ready_logs_provider_and_extractor_without_sending_sweep(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("WORKER_ROLE", "transcribe")
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "fake")
    monkeypatch.setenv("TASK_EXTRACTOR", "heuristic")

    def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("Transcribe worker sent a startup sweep")

    monkeypatch.setattr(app, "send_task", refuse_send)
    with caplog.at_level(logging.INFO):
        worker_signals.worker_ready()
    assert "provider=fake" in caplog.text
    assert "extractor=heuristic" in caplog.text


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("redis://u:p@h:6379/1?password=s&x=y#f", "redis://u:***@h:6379/1"),
        ("redis://h:6379/1?password=s", "redis://h:6379/1"),
        ("rediss://u:p%40ss@[::1]:6380/1?ssl_keyfile=secret#f", "rediss://u:***@[::1]:6380/1"),
    ],
)
def test_broker_display_omits_password_query_and_fragment(url: str, expected: str) -> None:
    assert worker_signals._broker_display(url) == expected
