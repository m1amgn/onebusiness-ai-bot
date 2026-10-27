from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from taskbot.domain.messages import InboundMessage, MessageKind, MessageRef
from taskbot.domain.text import MessageText
from taskbot.domain.voice_jobs import VoiceJob, VoiceJobStatus
from tests.audio import ogg_page, synthetic_opus


def test_duration_counts_packets_and_ignores_forged_granule() -> None:
    from taskbot.domain.audio import opus_duration_seconds

    assert opus_duration_seconds(synthetic_opus(125, granule=240000)) == pytest.approx(125)
    assert opus_duration_seconds(synthetic_opus(121)) == pytest.approx(121)
    fixture = Path(__file__).parents[1] / "fixtures/voice-en.ogg"
    assert opus_duration_seconds(fixture.read_bytes()) == pytest.approx(3.82, abs=0.02)


@pytest.mark.parametrize(
    ("packet", "seconds"),
    [
        (b"\x00", 0.01),
        (b"\x18", 0.06),
        (b"\x60", 0.01),
        (b"\x68", 0.02),
        (b"\x80", 0.0025),
        (b"\x81", 0.005),
        (b"\x82\x00", 0.005),
        (b"\x83\x03", 0.0075),
    ],
)
def test_opus_frame_configurations(packet: bytes, seconds: float) -> None:
    from taskbot.domain.audio import opus_duration_seconds

    headers = synthetic_opus(0)
    assert opus_duration_seconds(headers + ogg_page([packet], 2, flags=4)) == seconds


@pytest.mark.parametrize("packet", [b"", b"\x83", b"\x83\x00", b"\xfb\x3f"])
def test_invalid_packet_counts_are_rejected(packet: bytes) -> None:
    from taskbot.domain.audio import opus_duration_seconds
    from taskbot.domain.errors import UnreadableAudioError

    with pytest.raises(UnreadableAudioError):
        opus_duration_seconds(synthetic_opus(0) + ogg_page([packet], 2, flags=4))


def test_packet_continues_across_page_boundary() -> None:
    from taskbot.domain.audio import opus_duration_seconds

    data = synthetic_opus(0) + ogg_page([b"\xf8" + bytes(254)], 2)
    data += ogg_page([b"\x00"], 3, flags=5)
    assert opus_duration_seconds(data) == 0.02


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"RIFF" + bytes(40),
        b"OggS" + bytes(16),
        synthetic_opus(1)[:-1],
        synthetic_opus(0),
        synthetic_opus(1) + synthetic_opus(1),
    ],
)
def test_unreadable_audio_is_rejected(data: bytes) -> None:
    from taskbot.domain.audio import opus_duration_seconds
    from taskbot.domain.errors import UnreadableAudioError

    with pytest.raises(UnreadableAudioError):
        opus_duration_seconds(data)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (
            ogg_page([b"NotAHead" + bytes(11)], 0, flags=2)
            + ogg_page([b"OpusTags" + bytes(8)], 1)
            + ogg_page([b"\xf8\xff\xfe"], 2, flags=4),
            "Missing Opus header",
        ),
        (
            synthetic_opus(0) + ogg_page([b"OpusHead" + bytes(11)], 2, flags=4),
            "Repeated Opus header",
        ),
        (
            ogg_page([b"OpusHead" + bytes([1, 1]) + bytes(9)], 0, flags=2)
            + ogg_page([b"OpusTags" + bytes(8)], 1, flags=4),
            "Missing Opus audio",
        ),
        (
            ogg_page([b"OpusHead" + bytes([1, 1]) + bytes(9)], 0, flags=2)
            + ogg_page([b"\xf8\xff\xfe"], 1, flags=4),
            "Missing Opus tags",
        ),
        (
            synthetic_opus(0)
            + ogg_page([b"\xf8\xff\xfe"], 2, flags=4)[:14]
            + (2).to_bytes(4, "little")
            + ogg_page([b"\xf8\xff\xfe"], 2, flags=4)[18:],
            "Multiple Ogg streams",
        ),
        (
            synthetic_opus(0) + ogg_page([b"\xf8\xff\xfe"], 3, flags=4),
            "Missing Ogg page",
        ),
        (
            synthetic_opus(0) + ogg_page([b"\xf8\xff\xfe"], 2, flags=5),
            "Invalid Ogg continuation",
        ),
    ],
    ids=[
        "no-opus-head",
        "repeated-opus-head",
        "no-audio",
        "no-tags",
        "second-serial",
        "sequence-gap",
        "bad-continuation",
    ],
)
def test_malformed_ogg_streams_are_rejected_for_their_specific_fault(
    data: bytes, reason: str
) -> None:
    from taskbot.domain.audio import opus_duration_seconds
    from taskbot.domain.errors import UnreadableAudioError

    with pytest.raises(UnreadableAudioError, match=f"^{reason}$"):
        opus_duration_seconds(data)


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
