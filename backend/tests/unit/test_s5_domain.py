from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from taskbot.domain.messages import InboundMessage, MessageKind, MessageRef
from taskbot.domain.text import MessageText
from taskbot.domain.voice_jobs import VoiceJob, VoiceJobStatus


def test_bounded_transcript_keeps_last_complete_word() -> None:
    original = MessageText.parse("call supplier " * 400)
    text, cut = original.bounded(4096)
    assert cut
    assert len(text.value) <= 4096
    assert text.value.endswith("supplier") or text.value.endswith("call")
    assert original.bounded(6000) == (original, False)
    hard, cut = MessageText.parse("x" * 5000).bounded(4096)
    assert cut
    assert hard.value == "x" * 4096


def test_worker_settings_validate_token_and_keep_provider_fields_role_scoped() -> None:
    from taskbot.infrastructure.settings.worker import TranscribeWorkerSettings, WorkerSettings

    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://test:test@localhost/test",
        "worker_role": "transcribe",
    }
    settings = TranscribeWorkerSettings.model_validate(values)
    assert settings.openai_transcription_model == "gpt-transcribe"
    assert str(settings.whisper_server_url) == "http://127.0.0.1:8178/"
    with pytest.raises(ValidationError):
        TranscribeWorkerSettings.model_validate(values | {"telegram_bot_token": SecretStr("abc")})
    with pytest.raises(ValidationError):
        TranscribeWorkerSettings.model_validate(values | {"transcription_provider": "invalid"})
    assert (
        WorkerSettings.model_validate(
            values | {"worker_role": "notify", "transcription_provider": "invalid"}
        ).worker_role
        == "notify"
    )


def test_notification_for_deleted_task_preserves_degraded_success() -> None:
    from taskbot.application.replies import DEGRADED_LINE, notification_text
    from taskbot.domain.extraction import ExtractorName

    now = datetime(2026, 10, 27, tzinfo=UTC)
    row = VoiceJob(1, 1, MessageRef(1, 1), "file", "unique", 5, 10, now, now, now)
    row = replace(row, status=VoiceJobStatus.DONE, inbound_message_id=1)
    message = InboundMessage(
        1, 1, MessageRef(1, 1), MessageKind.VOICE, None, ExtractorName.HEURISTIC, True, now
    )
    assert notification_text(row, message, None) == "Task created.\n" + DEGRADED_LINE
