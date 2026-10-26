import logging
import os
import sys
from types import SimpleNamespace

import pytest

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


def test_main_init_records_actual_pid_without_building_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

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
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        worker_signals.worker_ready(sender=SimpleNamespace(hostname="notify@test", app=app))
    text = caplog.text
    for value in ("notify", "notification", "notify@test", "test:t1:", "redis://user:"):
        assert value in text
    assert "synthetic-password" not in text
    assert text.count("TELEGRAM_BOT_TOKEN is not set") == 1
    assert "No job handler is registered for queue notification" in text
