from pathlib import Path

import pytest

from tests.workers import DATABASE_URL, worker_environment


def test_worker_harness_keeps_provider_calls_offline_and_accepts_explicit_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-parent-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-parent-key")
    monkeypatch.setenv("TASK_EXTRACTOR", "claude")
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
    default = worker_environment("taskbot_test_0_01234567:t1:", tmp_path)
    assert default["DATABASE_URL"] == DATABASE_URL
    assert default["TASKBOT_ENV_FILE"] == "/dev/null"
    assert default["TRANSCRIPTION_PROVIDER"] == "fake"
    assert default["TASK_EXTRACTOR"] == "heuristic"
    assert "OPENAI_API_KEY" not in default
    assert "ANTHROPIC_API_KEY" not in default
    custom = worker_environment(
        "taskbot_test_0_01234567:t2:",
        tmp_path,
        database_url="postgresql+asyncpg://test:test@localhost/taskbot_test_0_abcd1234",
        env={"TRANSCRIPTION_PROVIDER": "openai", "OPENAI_API_KEY": None},
    )
    assert custom["DATABASE_URL"].endswith("taskbot_test_0_abcd1234")
    assert custom["TRANSCRIPTION_PROVIDER"] == "openai"
    assert "OPENAI_API_KEY" not in custom


def test_worker_harness_refuses_development_database(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="owned test database"):
        worker_environment(
            "taskbot_test_0_01234567:t1:",
            tmp_path,
            database_url="postgresql+asyncpg://test:test@localhost/taskbot",
        )
