from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING

import httpx
import httpx2
import pytest
from billiard.exceptions import SoftTimeLimitExceeded

from taskbot.domain.errors import TranscriptionError, TransientTranscriptionError
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider

if TYPE_CHECKING:
    from openai import AsyncOpenAI

OpenAIHandler = (
    Callable[[httpx2.Request], httpx2.Response]
    | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
)
WhisperHandler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


def openai_client(handler: OpenAIHandler) -> AsyncOpenAI:
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key="test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def whisper_client(handler: WhisperHandler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://whisper.test", transport=httpx.MockTransport(handler))


async def test_openai_request_uses_configured_model_and_audio() -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"text": "  Call the supplier. \n"})

    async with openai_client(handle) as client:
        adapter = OpenAITranscriber(client, model="configured-model")
        result = await adapter.transcribe(b"synthetic-audio")
    assert result.text == "Call the supplier."
    assert result.provider == adapter.provider == TranscriptionProvider.OPENAI
    assert len(requests) == 1
    request = requests[0]
    assert request.url == "https://api.openai.com/v1/audio/transcriptions"
    assert request.method == "POST"
    assert b'name="model"\r\n\r\nconfigured-model' in request.content
    assert b'name="file"; filename="voice.ogg"' in request.content
    assert b"synthetic-audio" in request.content


@pytest.mark.parametrize(
    ("status", "error", "kind"),
    [
        (401, {}, FailureKind.PROVIDER_UNAVAILABLE),
        (403, {}, FailureKind.PROVIDER_UNAVAILABLE),
        (404, {"code": "model_not_found"}, FailureKind.PROVIDER_UNAVAILABLE),
        (400, {"param": "model"}, FailureKind.PROVIDER_UNAVAILABLE),
        (429, {"code": "insufficient_quota"}, FailureKind.PROVIDER_UNAVAILABLE),
        (429, {"type": "insufficient_quota"}, FailureKind.PROVIDER_UNAVAILABLE),
        (400, {"param": "file"}, FailureKind.UNSUPPORTED_AUDIO),
        (413, {}, FailureKind.UNSUPPORTED_AUDIO),
        (415, {}, FailureKind.UNSUPPORTED_AUDIO),
        (302, {}, FailureKind.INTERNAL),
        (409, {}, FailureKind.INTERNAL),
        (422, {}, FailureKind.INTERNAL),
    ],
)
async def test_openai_final_status_mapping(
    status: int, error: dict[str, object], kind: FailureKind, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    calls = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(status, json={"error": error | {"message": "PRIVATE_PROVIDER_BODY"}})

    async with openai_client(handle) as client:
        with pytest.raises(TranscriptionError) as raised:
            await OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"PRIVATE_AUDIO")
    assert raised.value.kind == kind
    assert calls == 1
    assert "PRIVATE_" not in str(raised.value)
    assert "PRIVATE_" not in caplog.text


@pytest.mark.parametrize("status", [408, 429, 500, 503, 599])
async def test_openai_transient_status_mapping(status: int) -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    calls = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(status, json={"error": {"message": "PRIVATE_PROVIDER_BODY"}})

    async with openai_client(handle) as client:
        with pytest.raises(TransientTranscriptionError):
            await OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"voice")
    assert calls == 1


@pytest.mark.parametrize("error_type", [httpx2.ReadTimeout, httpx2.ConnectError])
async def test_openai_sdk_connection_and_timeout_are_transient(
    error_type: type[httpx2.RequestError],
) -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    def handle(request: httpx2.Request) -> httpx2.Response:
        raise error_type("PRIVATE_PROVIDER_BODY", request=request)

    async with openai_client(handle) as client:
        with pytest.raises(TransientTranscriptionError):
            await OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"voice")


async def test_openai_unknown_sdk_error_is_internal() -> None:
    from openai import APIError

    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    def handle(request: httpx2.Request) -> httpx2.Response:
        raise APIError("PRIVATE_PROVIDER_BODY", request=request, body=None)

    async with openai_client(handle) as client:
        with pytest.raises(TranscriptionError) as raised:
            await OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"voice")
    assert raised.value.kind == FailureKind.INTERNAL


async def test_openai_malformed_quota_field_keeps_rate_limit_transient() -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    response = httpx2.Response(429, json={"error": {"type": {"unexpected": "object"}}})
    async with openai_client(lambda request: response) as client:
        with pytest.raises(TransientTranscriptionError):
            await OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"voice")


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, json={}),
        httpx2.Response(200, json={"text": None}),
        httpx2.Response(200, json={"text": 14}),
        httpx2.Response(200, json={"text": ["private"]}),
        httpx2.Response(200, json=[{"text": "private"}]),
        httpx2.Response(200, text="not json"),
        httpx2.Response(200, content=b"{broken", headers={"content-type": "application/json"}),
    ],
    ids=["missing", "null", "number", "list-text", "list-body", "text-body", "broken-json"],
)
async def test_openai_malformed_result_is_internal(response: httpx2.Response) -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    async with openai_client(lambda request: response) as client:
        with pytest.raises(TranscriptionError) as raised:
            await OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"voice")
    assert raised.value.kind == FailureKind.INTERNAL


@pytest.mark.parametrize("text", ["", " \n\t", "... !?", "\u200b"])
async def test_openai_empty_text_is_final(text: str) -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber

    async with openai_client(lambda request: httpx2.Response(200, json={"text": text})) as client:
        with pytest.raises(TranscriptionError) as raised:
            await OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"voice")
    assert raised.value.kind == FailureKind.EMPTY_TRANSCRIPT


async def test_openai_wall_clock_is_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    from taskbot.infrastructure.transcription import openai as module

    cancelled = asyncio.Event()

    async def never_answer(request: httpx2.Request) -> httpx2.Response:
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(module, "OPENAI_TIMEOUT_SECONDS", 0.01)
    async with openai_client(never_answer) as client:
        with pytest.raises(TransientTranscriptionError):
            await module.OpenAITranscriber(client, model="gpt-transcribe").transcribe(b"voice")
    assert cancelled.is_set()


async def test_whisper_request_and_response() -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"text": "  Call the supplier. \n"})

    async with whisper_client(handle) as client:
        adapter = WhisperServerTranscriber(client)
        result = await adapter.transcribe(b"synthetic-audio")
    assert result.text == "Call the supplier."
    assert result.provider == adapter.provider == TranscriptionProvider.WHISPER_SERVER
    assert len(requests) == 1
    request = requests[0]
    assert request.url == "http://whisper.test/inference"
    assert request.method == "POST"
    assert b'name="file"; filename="voice.ogg"' in request.content
    assert b"Content-Type: audio/ogg" in request.content
    assert b"synthetic-audio" in request.content
    assert b'name="response_format"\r\n\r\njson' in request.content
    assert b'name="temperature"\r\n\r\n0' in request.content
    for field in ("prompt", "language", "temperature_inc"):
        assert f'name="{field}"'.encode() not in request.content


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (400, "file not found", FailureKind.INTERNAL),
        (400, "file is required", FailureKind.INTERNAL),
        (400, "failed to read audio", FailureKind.UNSUPPORTED_AUDIO),
        (413, "too large", FailureKind.UNSUPPORTED_AUDIO),
        (415, "unsupported type", FailureKind.UNSUPPORTED_AUDIO),
        (500, "FFmpeg conversion failed.", FailureKind.UNSUPPORTED_AUDIO),
        (302, "redirect", FailureKind.INTERNAL),
        (401, "unauthorized", FailureKind.INTERNAL),
        (404, "not found", FailureKind.INTERNAL),
        (429, "rate limit", FailureKind.INTERNAL),
    ],
)
async def test_whisper_final_status_mapping(
    status: int, body: str, kind: FailureKind, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    response = httpx.Response(status, json={"error": body, "private": "PRIVATE_PROVIDER_BODY"})
    async with whisper_client(lambda request: response) as client:
        with pytest.raises(TranscriptionError) as raised:
            await WhisperServerTranscriber(client).transcribe(b"PRIVATE_AUDIO")
    assert raised.value.kind == kind
    assert "PRIVATE_" not in str(raised.value)
    assert "PRIVATE_" not in caplog.text


async def test_whisper_real_missing_file_response_is_internal() -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    response = httpx.Response(400, json={"error": "no 'file' field in the request"})
    async with whisper_client(lambda request: response) as client:
        with pytest.raises(TranscriptionError) as raised:
            await WhisperServerTranscriber(client).transcribe(b"voice")
    assert raised.value.kind == FailureKind.INTERNAL


@pytest.mark.parametrize(
    ("status", "body"),
    [(499, "cancelled"), (500, "failed to process audio"), (503, "not ready"), (599, "unknown")],
)
async def test_whisper_transient_status_mapping(status: int, body: str) -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    async with whisper_client(lambda request: httpx.Response(status, text=body)) as client:
        with pytest.raises(TransientTranscriptionError):
            await WhisperServerTranscriber(client).transcribe(b"voice")


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
        httpx.WriteTimeout,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.LocalProtocolError,
        httpx.RemoteProtocolError,
    ],
)
async def test_whisper_transient_transport_errors(error_type: type[httpx.RequestError]) -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    def handle(request: httpx.Request) -> httpx.Response:
        raise error_type("PRIVATE_PROVIDER_BODY", request=request)

    async with whisper_client(handle) as client:
        with pytest.raises(TransientTranscriptionError):
            await WhisperServerTranscriber(client).transcribe(b"voice")


async def test_whisper_read_timeout_is_final() -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("PRIVATE_PROVIDER_BODY", request=request)

    async with whisper_client(handle) as client:
        with pytest.raises(TranscriptionError) as raised:
            await WhisperServerTranscriber(client).transcribe(b"voice")
    assert raised.value.kind == FailureKind.TRANSCRIPTION_TIMEOUT


async def test_whisper_unmapped_http_error_is_internal() -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.UnsupportedProtocol("PRIVATE_PROVIDER_BODY", request=request)

    async with whisper_client(handle) as client:
        with pytest.raises(TranscriptionError) as raised:
            await WhisperServerTranscriber(client).transcribe(b"voice")
    assert raised.value.kind == FailureKind.INTERNAL


async def test_whisper_wall_clock_is_final(monkeypatch: pytest.MonkeyPatch) -> None:
    from taskbot.infrastructure.transcription import whisper_server as module

    cancelled = asyncio.Event()

    async def never_answer(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(module, "WHISPER_TIMEOUT_SECONDS", 0.01)
    async with whisper_client(never_answer) as client:
        with pytest.raises(TranscriptionError) as raised:
            await module.WhisperServerTranscriber(client).transcribe(b"voice")
    assert raised.value.kind == FailureKind.TRANSCRIPTION_TIMEOUT
    assert cancelled.is_set()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"text": None}),
        httpx.Response(200, json={"text": 14}),
        httpx.Response(200, json={"text": ["private"]}),
        httpx.Response(200, json=[{"text": "private"}]),
        httpx.Response(200, text="not json"),
    ],
    ids=["missing", "null", "number", "list-text", "list-body", "text-body"],
)
async def test_whisper_malformed_result_is_internal(response: httpx.Response) -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    async with whisper_client(lambda request: response) as client:
        with pytest.raises(TranscriptionError) as raised:
            await WhisperServerTranscriber(client).transcribe(b"voice")
    assert raised.value.kind == FailureKind.INTERNAL


@pytest.mark.parametrize(
    "text", ["", " \n\t", "... !?", "[BLANK_AUDIO]", "(silence)", "[Music] (silence) ..."]
)
async def test_whisper_empty_and_non_speech_text_is_final(text: str) -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    async with whisper_client(lambda request: httpx.Response(200, json={"text": text})) as client:
        with pytest.raises(TranscriptionError) as raised:
            await WhisperServerTranscriber(client).transcribe(b"voice")
    assert raised.value.kind == FailureKind.EMPTY_TRANSCRIPT


@pytest.mark.parametrize("text", ["Thank you for watching!", "[Music] Call the supplier."])
async def test_whisper_keeps_fluent_text_and_mixed_speech(text: str) -> None:
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    async with whisper_client(lambda request: httpx.Response(200, json={"text": text})) as client:
        result = await WhisperServerTranscriber(client).transcribe(b"voice")
    assert result.text == text


@pytest.mark.parametrize("provider", ["openai", "whisper"])
@pytest.mark.parametrize(
    "error", [SoftTimeLimitExceeded(), asyncio.CancelledError(), TimeoutError(), RuntimeError()]
)
async def test_adapters_preserve_interrupts_and_unowned_timeout(
    provider: str, error: BaseException
) -> None:
    from taskbot.infrastructure.transcription.openai import OpenAITranscriber
    from taskbot.infrastructure.transcription.whisper_server import WhisperServerTranscriber

    def openai_handle(request: httpx2.Request) -> httpx2.Response:
        raise error

    def whisper_handle(request: httpx.Request) -> httpx.Response:
        raise error

    if provider == "openai":
        async with openai_client(openai_handle) as openai:
            with pytest.raises(type(error)) as raised:
                await OpenAITranscriber(openai, model="gpt-transcribe").transcribe(b"voice")
    else:
        async with whisper_client(whisper_handle) as whisper:
            with pytest.raises(type(error)) as raised:
                await WhisperServerTranscriber(whisper).transcribe(b"voice")
    assert raised.value is error
