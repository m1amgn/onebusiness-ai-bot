import asyncio
import json
import traceback
from collections import deque

import pytest
from aiogram import Bot
from aiohttp import ClientConnectionError

from taskbot.domain.errors import (
    MessageGoneError,
    NotificationError,
    RecipientBlockedError,
    RetryAfterError,
    VoiceFileTooLargeError,
    VoiceFileUnavailableError,
)


async def test_notifier_sends_plain_reply_and_returns_message_id() -> None:
    from taskbot.infrastructure.telegram.notifier import AiogramUserNotifier
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    async with Bot("123456:synthetic", session=session) as bot:
        assert await AiogramUserNotifier(bot).send_reply(7, "A <title>", reply_to_message_id=3) == 1
    payload = session.payloads[0]
    assert payload["text"] == "A <title>"
    assert payload["reply_parameters"] == {"message_id": 3, "allow_sending_without_reply": True}
    assert session.timeouts == [10]


@pytest.mark.parametrize(
    ("status", "description", "expected"),
    [
        (400, "Bad Request: message to edit not found", MessageGoneError),
        (400, "Bad Request: message can't be edited", MessageGoneError),
        (400, "Bad Request: synthetic invalid input", NotificationError),
        (403, "Forbidden: bot was blocked", RecipientBlockedError),
        (429, "Too Many Requests: synthetic", RetryAfterError),
        (500, "Server error: synthetic", NotificationError),
    ],
)
async def test_notifier_maps_telegram_errors_without_response_body(
    status: int, description: str, expected: type[Exception]
) -> None:
    from taskbot.infrastructure.telegram.notifier import AiogramUserNotifier
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    session.responses.append(
        (
            status,
            json.dumps(
                {
                    "ok": False,
                    "error_code": status,
                    "description": description,
                    "parameters": {"retry_after": 12} if status == 429 else {},
                }
            ),
        )
    )
    async with Bot("123456:synthetic", session=session) as bot:
        with pytest.raises(expected) as caught:
            await AiogramUserNotifier(bot).edit_message(7, 1, "title")
    assert description not in "".join(traceback.format_exception(caught.value))
    if isinstance(caught.value, RetryAfterError):
        assert caught.value.seconds == 12


async def test_not_modified_is_success() -> None:
    from taskbot.infrastructure.telegram.notifier import AiogramUserNotifier
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession(
        responses=deque(
            [
                (
                    400,
                    '{"ok":false,"error_code":400,'
                    '"description":"Bad Request: message is not modified"}',
                )
            ]
        )
    )
    async with Bot("123456:synthetic", session=session) as bot:
        await AiogramUserNotifier(bot).edit_message(7, 1, "same title")


@pytest.mark.parametrize(
    "error", [TimeoutError(), ClientConnectionError("synthetic network failure")]
)
async def test_notifier_maps_transport_failures(error: Exception) -> None:
    from taskbot.infrastructure.telegram.notifier import AiogramUserNotifier
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    session.request_errors["sendMessage"] = error
    async with Bot("123456:synthetic", session=session) as bot:
        with pytest.raises(NotificationError):
            await AiogramUserNotifier(bot).send_reply(7, "title")


async def test_telegram_file_too_big_becomes_size_refusal() -> None:
    from taskbot.infrastructure.telegram.voice_files import AiogramVoiceFileSource
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession(
        responses=deque(
            [
                (
                    400,
                    json.dumps(
                        {
                            "ok": False,
                            "error_code": 400,
                            "description": "Bad Request: file is too big",
                        }
                    ),
                )
            ]
        )
    )
    async with Bot("123456:synthetic", session=session) as bot:
        with pytest.raises(VoiceFileTooLargeError):
            await AiogramVoiceFileSource(bot).fetch("file", max_bytes=4)


@pytest.mark.parametrize(
    ("status", "body"),
    [(502, "<html>private response</html>"), (200, '{"ok":true,"result":{"private":"body"}}')],
)
async def test_malformed_notifier_response_stays_inside_the_port(status: int, body: str) -> None:
    from taskbot.infrastructure.telegram.notifier import AiogramUserNotifier
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession(responses=deque([(status, body)]))
    async with Bot("123456:synthetic", session=session) as bot:
        with pytest.raises(NotificationError) as caught:
            await AiogramUserNotifier(bot).send_reply(7, "title")
    assert "private" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("declared", [None, 4])
async def test_voice_download_caps_actual_bytes_with_optional_declared_size(
    declared: int | None,
) -> None:
    from taskbot.infrastructure.telegram.voice_files import AiogramVoiceFileSource
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    session.responses.append(
        (
            200,
            json.dumps(
                {
                    "ok": True,
                    "result": {
                        "file_id": "file",
                        "file_unique_id": "unique",
                        "file_path": "voice.ogg",
                        "file_size": declared,
                    },
                }
            ),
        )
    )
    session.chunks = [b"ab", b"cd"]
    async with Bot("123456:synthetic", session=session) as bot:
        assert await AiogramVoiceFileSource(bot).fetch("file", max_bytes=4) == b"abcd"


@pytest.mark.parametrize("declared", [None, 5])
async def test_voice_download_refuses_declared_or_actual_oversize(declared: int | None) -> None:
    from taskbot.infrastructure.telegram.voice_files import AiogramVoiceFileSource
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    session.responses.append(
        (
            200,
            json.dumps(
                {
                    "ok": True,
                    "result": {
                        "file_id": "file",
                        "file_unique_id": "unique",
                        "file_path": "voice.ogg",
                        "file_size": declared,
                    },
                }
            ),
        )
    )
    session.chunks = [b"abc", b"de"]
    async with Bot("123456:synthetic", session=session) as bot:
        with pytest.raises(VoiceFileTooLargeError):
            await AiogramVoiceFileSource(bot).fetch("file", max_bytes=4)
    assert session.downloads == (0 if declared else 1)


async def test_download_hides_token_bearing_transport_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from taskbot.infrastructure.telegram.voice_files import AiogramVoiceFileSource
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    session.stream_error = ClientConnectionError(
        "https://api.telegram.org/file/bot123456:synthetic/voice.ogg"
    )
    async with Bot("123456:synthetic", session=session) as bot:
        with pytest.raises(VoiceFileUnavailableError) as caught:
            await AiogramVoiceFileSource(bot).fetch("file", max_bytes=4)
    assert "123456:synthetic" not in "".join(traceback.format_exception(caught.value))
    assert "123456:synthetic" not in caplog.text


async def test_stalled_download_has_one_total_deadline() -> None:
    from taskbot.infrastructure.telegram.voice_files import AiogramVoiceFileSource
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    session.stream_gate = asyncio.Event()
    async with Bot("123456:synthetic", session=session) as bot:
        async with asyncio.timeout(1):
            with pytest.raises(VoiceFileUnavailableError):
                await AiogramVoiceFileSource(bot, deadline_seconds=0.02).fetch("file", max_bytes=4)


@pytest.mark.parametrize("result", [{"file_id": "file", "file_unique_id": "unique"}, None])
async def test_download_missing_file_path_or_server_error_is_unavailable(
    result: dict[str, str] | None,
) -> None:
    from taskbot.infrastructure.telegram.voice_files import AiogramVoiceFileSource
    from tests.fakes.telegram_session import FakeTelegramSession

    session = FakeTelegramSession()
    session.responses.append(
        (200, json.dumps({"ok": True, "result": result}))
        if result
        else (500, '{"ok":false,"description":"Server unavailable"}')
    )
    async with Bot("123456:synthetic", session=session) as bot:
        with pytest.raises(VoiceFileUnavailableError):
            await AiogramVoiceFileSource(bot).fetch("file", max_bytes=4)
