import json
import os
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from taskbot.domain.ports.transcription import Transcriber, Transcript
from taskbot.domain.voice_jobs import TranscriptionProvider
from tests.fakes.transcription import ScriptedTranscriber
from tests.processes import COLD_START_TIMEOUT_SECONDS, run_process
from tests.s5 import FIXTURE, SENTENCE

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    for name in ("OPENAI_API_KEY", "TRANSCRIPTION_PROVIDER", "OPENAI_TRANSCRIPTION_MODEL"):
        monkeypatch.delenv(name, raising=False)

    def forbidden_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network reached an offline transcription probe test")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)


async def test_probe_uses_owned_adapter_and_reports_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.acceptance import live_transcription as probe

    calls: list[str] = []

    @asynccontextmanager
    async def open_fake(settings: object) -> AsyncIterator[Transcriber]:
        calls.append("open")
        try:
            yield ScriptedTranscriber([Transcript(SENTENCE, TranscriptionProvider.WHISPER_SERVER)])
        finally:
            calls.append("close")

    monkeypatch.setattr(probe, "open_transcriber", open_fake)
    audio = probe.read_audio(FIXTURE)
    result = await probe.measure(probe.LiveTranscriptionSettings(_env_file=None), audio)
    assert calls == ["open", "close"]
    assert result.provider == "whisper_server"
    assert result.model is None
    assert result.text == SENTENCE
    assert result.elapsed_seconds >= 0


def test_missing_openai_key_exits_before_opening_a_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.acceptance import live_transcription as probe

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Provider factory reached without a key")

    monkeypatch.setattr(probe, "open_transcriber", forbidden)
    monkeypatch.setattr(sys, "argv", ["live_transcription.py", "--provider", "openai"])
    with pytest.raises(SystemExit) as raised:
        probe.main()
    assert raised.value.code == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert "TRANSCRIPTION_PROVIDER" not in os.environ


def test_probe_sends_audio_to_provider_without_inspecting_its_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.acceptance import live_transcription as probe

    audio = tmp_path / "synthetic.bin"
    audio.write_bytes(b"provider-owned audio decoding")
    adapter = ScriptedTranscriber([Transcript(SENTENCE, TranscriptionProvider.WHISPER_SERVER)])

    @asynccontextmanager
    async def open_fake(settings: object) -> AsyncIterator[Transcriber]:
        yield adapter

    monkeypatch.setattr(probe, "open_transcriber", open_fake)
    monkeypatch.setattr(
        sys, "argv", ["live_transcription.py", "--provider", "whisper_server", "--file", str(audio)]
    )
    probe.main()
    assert adapter.audio == [audio.read_bytes()]
    assert json.loads(capsys.readouterr().out)["text"] == SENTENCE


def test_probe_cli_makes_one_call_without_database_or_telegram_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.acceptance import live_transcription as probe

    adapter = ScriptedTranscriber(
        [Transcript(SENTENCE, TranscriptionProvider.OPENAI)],
        provider=TranscriptionProvider.OPENAI,
    )

    @asynccontextmanager
    async def open_fake(settings: object) -> AsyncIterator[Transcriber]:
        yield adapter

    monkeypatch.setattr(probe, "open_transcriber", open_fake)
    monkeypatch.setattr(sys, "argv", ["live_transcription.py", "--provider", "openai"])
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("DATABASE_URL", "invalid")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "invalid")
    monkeypatch.setenv("TASK_EXTRACTOR", "invalid")
    probe.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-transcribe"
    assert result["text"] == SENTENCE
    assert adapter.audio == [FIXTURE.read_bytes()]
    assert "synthetic-test-key" not in output


async def test_live_worker_collection_requires_explicit_marker() -> None:
    results = []
    for marker in (None, "live_whisper"):
        result = await run_process(
            sys.executable,
            "-m",
            "pytest",
            "tests/live",
            "--collect-only",
            "-q",
            *(["-m", marker] if marker is not None else []),
            cwd=BACKEND_ROOT,
            env=os.environ | {"TASKBOT_ENV_FILE": "/dev/null"},
            timeout_seconds=COLD_START_TIMEOUT_SECONDS,
        )
        results.append(result)
    assert results[0].returncode == 5, results[0].stdout + results[0].stderr
    assert "1 deselected" in results[0].stdout
    assert "::test_real_whisper_worker" not in results[0].stdout
    assert results[1].returncode == 0, results[1].stdout + results[1].stderr
    assert "::test_real_whisper_worker" in results[1].stdout
