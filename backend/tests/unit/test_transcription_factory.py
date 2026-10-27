from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING

import httpx
import httpx2
import pytest
from pydantic import SecretStr

from taskbot.domain.errors import TranscriptionError
from taskbot.domain.voice_jobs import FailureKind, TranscriptionProvider
from taskbot.infrastructure.settings.worker import TranscribeWorkerSettings

if TYPE_CHECKING:
    from types import ModuleType


def settings(**values: object) -> TranscribeWorkerSettings:
    return TranscribeWorkerSettings.model_validate(
        {
            "worker_role": "transcribe",
            "database_url": "postgresql+asyncpg://test:test@localhost/test",
            "task_extractor": "heuristic",
            **values,
        }
    )


def forbid_openai_import(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builtins.__import__

    def guarded(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "openai" or name.startswith("openai."):
            raise AssertionError("OpenAI must stay lazy on this factory branch")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)


async def test_fake_is_deterministic_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    from taskbot.infrastructure.transcription.factory import open_transcriber

    forbid_openai_import(monkeypatch)
    async with open_transcriber(settings(transcription_provider="fake")) as adapter:
        result = await adapter.transcribe(b"any audio")
        assert await adapter.transcribe(b"different audio") == result
    assert adapter.provider == result.provider == TranscriptionProvider.FAKE
    assert result.text == (
        "[demo transcript] Please remind me to call the supplier tomorrow morning "
        "about the invoice."
    )


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr(" \n\t")])
async def test_missing_key_warns_once_and_refuses_without_sdk(
    key: SecretStr | None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.infrastructure.transcription.factory import open_transcriber

    forbid_openai_import(monkeypatch)
    configured = settings(transcription_provider="openai", openai_api_key=key)
    async with open_transcriber(configured) as adapter:
        for _ in range(2):
            with pytest.raises(TranscriptionError) as raised:
                await adapter.transcribe(b"voice")
            assert raised.value.kind == FailureKind.PROVIDER_UNAVAILABLE
    assert adapter.provider == TranscriptionProvider.OPENAI
    records = [
        record
        for record in caplog.records
        if record.name == "taskbot.infrastructure.transcription.factory"
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING


@pytest.mark.parametrize("body_raises", [False, True])
async def test_openai_factory_configures_and_closes_client(
    monkeypatch: pytest.MonkeyPatch, body_raises: bool
) -> None:
    import openai

    from taskbot.infrastructure.transcription.factory import open_transcriber

    original = openai.AsyncOpenAI
    clients: list[openai.AsyncOpenAI] = []
    requests: list[httpx2.Request] = []

    def capture_client(
        *,
        api_key: str,
        base_url: str,
        max_retries: int,
        timeout: float,
        http_client: httpx2.AsyncClient,
    ) -> openai.AsyncOpenAI:
        client = original(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=http_client,
        )
        clients.append(client)
        return client

    async def send(
        transport: httpx2.AsyncHTTPTransport, request: httpx2.Request
    ) -> httpx2.Response:
        await request.aread()
        requests.append(request)
        return httpx2.Response(200, json={"text": "Call the supplier."})

    monkeypatch.setattr(openai, "AsyncOpenAI", capture_client)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", send)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.test")
    configured = settings(
        transcription_provider="openai",
        openai_api_key=SecretStr(" \n test-key\n"),
        openai_transcription_model="configured-model",
    )
    try:
        async with open_transcriber(configured) as adapter:
            assert not clients[0].is_closed()
            assert clients[0].max_retries == 0
            assert clients[0].timeout == 30
            assert not clients[0]._client.follow_redirects
            await adapter.transcribe(b"voice")
            if body_raises:
                raise RuntimeError("consumer failed")
    except RuntimeError:
        assert body_raises
    assert len(clients) == len(requests) == 1
    assert clients[0].is_closed()
    request = requests[0]
    assert request.url == "https://api.openai.com/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.extensions["timeout"] == {"connect": 30, "read": 30, "write": 30, "pool": 30}
    assert b'name="model"\r\n\r\nconfigured-model' in request.content
    for name in ("openai", "httpx2", "httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING


async def test_default_factory_opens_and_closes_whisper_without_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from taskbot.infrastructure.transcription.factory import open_transcriber

    original = httpx.AsyncClient
    clients: list[httpx.AsyncClient] = []
    requests: list[httpx.Request] = []

    def capture_client(
        *, base_url: str, timeout: httpx.Timeout, follow_redirects: bool
    ) -> httpx.AsyncClient:
        client = original(base_url=base_url, timeout=timeout, follow_redirects=follow_redirects)
        clients.append(client)
        return client

    async def send(transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"text": "Call the supplier."})

    monkeypatch.setattr(httpx, "AsyncClient", capture_client)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    forbid_openai_import(monkeypatch)
    async with open_transcriber(settings()) as adapter:
        assert adapter.provider == TranscriptionProvider.WHISPER_SERVER
        assert not clients[0].is_closed
        assert not clients[0].follow_redirects
        await adapter.transcribe(b"voice")
    assert clients[0].is_closed
    assert requests[0].url == "http://127.0.0.1:8178/inference"
    assert requests[0].extensions["timeout"] == {
        "connect": 5,
        "read": 300,
        "write": 60,
        "pool": 5,
    }
