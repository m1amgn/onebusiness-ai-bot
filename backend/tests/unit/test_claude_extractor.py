from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Callable, Coroutine
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest
from pydantic import SecretStr

from taskbot.domain.extraction import TaskDraft
from taskbot.domain.text import MessageText
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin
from tests.processes import run_process

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

    from taskbot.infrastructure.extraction.claude import ClaudeExtractor, ThinkingMode

BACKEND_ROOT = Path(__file__).resolve().parents[2]
LOGGER = "taskbot.infrastructure.extraction.claude"
Handler = (
    Callable[[httpx2.Request], httpx2.Response]
    | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
)


def message_body(
    output: str | None = '{"title":"Book a meeting","description":"Tomorrow"}',
    stop: str = "end_turn",
) -> dict[str, object]:
    return {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [] if output is None else [{"type": "text", "text": output}],
        "stop_reason": stop,
        "usage": {"input_tokens": 123, "output_tokens": 17},
    }


def reply(
    output: str | None = '{"title":"Book a meeting"}', stop: str = "end_turn"
) -> httpx2.Response:
    return httpx2.Response(200, json=message_body(output, stop))


def client_for(handler: Handler) -> AsyncAnthropic:
    from anthropic import AsyncAnthropic

    from taskbot.infrastructure.extraction.claude import quiet_sdk_loggers

    quiet_sdk_loggers()
    return AsyncAnthropic(
        api_key="test",
        base_url="https://api.anthropic.com",
        max_retries=0,
        timeout=1,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def extractor_for(
    client: AsyncAnthropic,
    *,
    thinking: ThinkingMode = "disabled",
    deadline_seconds: float = 1,
    interrupts: tuple[type[BaseException], ...] = (),
) -> ClaudeExtractor:
    from taskbot.infrastructure.extraction.claude import ClaudeExtractor

    return ClaudeExtractor(
        client,
        model="claude-haiku-5-5",
        thinking=thinking,
        deadline_seconds=deadline_seconds,
        interrupts=interrupts,
    )


@pytest.mark.parametrize("thinking", ["disabled", "adaptive"])
async def test_structured_request_and_usage(
    thinking: ThinkingMode, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.infrastructure.extraction.prompt import SYSTEM_PROMPT

    requests: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        body = message_body()
        body["usage"] = {
            "input_tokens": 123,
            "output_tokens": 17,
            "cache_read_input_tokens": 512,
            "cache_creation_input_tokens": 0,
        }
        return httpx2.Response(200, json=body)

    caplog.set_level(logging.INFO, logger=LOGGER)
    async with client_for(handle) as client:
        result = await extractor_for(client, thinking=thinking).extract(
            MessageText.parse("Book a meeting tomorrow")
        )
    assert result.task == TaskDraft(title="Book a meeting", description="Tomorrow")
    assert result.extractor == "claude"
    assert not result.degraded
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "claude-haiku-5-5"
    assert request["max_tokens"] == 4096
    assert request["thinking"] == {"type": thinking}
    assert request["system"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    assert request["messages"] == [
        {"role": "user", "content": "<message>\nBook a meeting tomorrow\n</message>"}
    ]
    config = request["output_config"]
    assert isinstance(config, dict)
    assert config["effort"] == "low"
    assert config["format"]["schema"]["properties"]["title"]["type"] == "string"
    records = [record for record in caplog.records if record.name == LOGGER]
    assert len(records) == 1
    assert records[0].getMessage() == "claude extraction"
    assert records[0].__dict__["usage"] == {
        "input_tokens": 123,
        "output_tokens": 17,
        "cache_read_input_tokens": 512,
        "cache_creation_input_tokens": 0,
    }
    assert records[0].__dict__["latency_ms"] >= 0


@pytest.mark.parametrize("usage", [{"input_tokens": 1, "output_tokens": 2}, None])
async def test_missing_usage_counts_are_zero(
    usage: dict[str, int] | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    body = message_body()
    body["usage"] = usage
    async with client_for(lambda request: httpx2.Response(200, json=body)) as client:
        result = await extractor_for(client).extract(MessageText.parse("Book a meeting"))
    assert not result.degraded
    record = next(record for record in caplog.records if record.name == LOGGER)
    assert record.__dict__["usage"] == {
        "input_tokens": 1 if usage else 0,
        "output_tokens": 2 if usage else 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            httpx2.Response(401, json={"error": {"message": "unavailable"}}),
            "api:AuthenticationError",
        ),
        (httpx2.Response(429, json={"error": {"message": "unavailable"}}), "api:RateLimitError"),
        (
            httpx2.Response(500, json={"error": {"message": "unavailable"}}),
            "api:InternalServerError",
        ),
        (reply(stop="refusal"), "stop:refusal"),
        (reply(stop="max_tokens"), "stop:max_tokens"),
        (reply(stop="pause_turn"), "stop:pause_turn"),
        (reply(None, "refusal"), "stop:refusal"),
        (reply(None), "no_output"),
        (reply('{"title":"cut', "max_tokens"), "invalid_output"),
        (reply("not json"), "invalid_output"),
        (reply("{}"), "invalid_output"),
        (reply('{"title":"Book a meeting","extra":1}'), "invalid_output"),
        (reply(json.dumps({"title": "a" * 121})), "invalid_output"),
        (reply(json.dumps({"title": "valid", "description": "a" * 4097})), "invalid_output"),
        (reply('{"title":"   "}'), "invalid_output"),
        (reply(json.dumps({"title": "\u200b"})), "invalid_output"),
        (reply('{"title":"!!!"}'), "invalid_output"),
        (httpx2.Response(200, json=message_body() | {"content": None}), "error:TypeError"),
        (httpx2.Response(200, text="<html>unavailable</html>"), "error:AttributeError"),
        (reply(stop="PRIVATE_MODEL_OUTPUT"), "stop:unknown"),
    ],
    ids=[
        "401",
        "429",
        "500",
        "refusal",
        "max-tokens-complete",
        "pause",
        "refusal-empty",
        "no-output",
        "max-tokens-cut",
        "bad-json",
        "missing-title",
        "extra-field",
        "long-title",
        "long-description",
        "blank-title",
        "invisible-title",
        "punctuation-title",
        "null-content",
        "html",
        "unknown-stop",
    ],
)
async def test_unusable_responses_fall_back_once(
    response: httpx2.Response, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return response

    text = MessageText.parse("Original text\nwith a second line")
    async with client_for(handle) as client:
        result = await extractor_for(client).extract(text)
    assert result == await HeuristicExtractor(degraded=True).extract(text)
    assert calls == 1
    records = [record for record in caplog.records if record.name == LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].__dict__["reason"] == reason
    assert "PRIVATE_MODEL_OUTPUT" not in caplog.text


@pytest.mark.parametrize("failure", ["connection", "socket-timeout", "deadline"])
async def test_transport_failure_and_whole_call_deadline(
    failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if failure == "connection":
            raise httpx2.ConnectError("synthetic", request=request)
        if failure == "socket-timeout":
            raise httpx2.ReadTimeout("synthetic", request=request)
        await asyncio.sleep(5)
        return reply()

    text = MessageText.parse("Keep the message")
    async with client_for(handle) as client:
        started = time.monotonic()
        result = await extractor_for(client, deadline_seconds=0.05).extract(text)
        elapsed = time.monotonic() - started
    assert result == await HeuristicExtractor(degraded=True).extract(text)
    assert calls == 1
    if failure == "deadline":
        assert elapsed < 1
    records = [record for record in caplog.records if record.name == LOGGER]
    assert len(records) == 1
    assert records[0].__dict__["reason"] == (
        "api:APIConnectionError" if failure == "connection" else "timeout"
    )


async def test_cancellation_propagates() -> None:
    entered = asyncio.Event()

    async def handle(request: httpx2.Request) -> httpx2.Response:
        entered.set()
        await asyncio.Event().wait()
        return reply()

    async with client_for(handle) as client:
        task = asyncio.create_task(
            extractor_for(client, deadline_seconds=10).extract(MessageText.parse("Keep text"))
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("propagate", [True, False])
async def test_wrapped_interrupt_policy(propagate: bool) -> None:
    class InterruptError(Exception):
        pass

    def handle(request: httpx2.Request) -> httpx2.Response:
        raise InterruptError("synthetic worker interruption")

    text = MessageText.parse("Keep text")
    async with client_for(handle) as client:
        extractor = extractor_for(client, interrupts=(InterruptError,) if propagate else ())
        if propagate:
            with pytest.raises(InterruptError, match="synthetic worker interruption"):
                await extractor.extract(text)
        else:
            assert await extractor.extract(text) == await HeuristicExtractor(degraded=True).extract(
                text
            )


@pytest.mark.parametrize("invalid", [False, True])
async def test_logs_never_include_input_or_output(
    invalid: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="anthropic")
    caplog.set_level(logging.DEBUG, logger="httpx2")
    output = "PRIVATE_OUTPUT_MARKER" * (10 if invalid else 1)
    async with client_for(lambda request: reply(json.dumps({"title": output}))) as client:
        result = await extractor_for(client).extract(MessageText.parse("PRIVATE_INPUT_MARKER"))
    assert result.degraded is invalid
    assert "PRIVATE_INPUT_MARKER" not in caplog.text
    assert "PRIVATE_OUTPUT_MARKER" not in caplog.text


@pytest.mark.parametrize("thinking", [None, "adaptive"])
@pytest.mark.parametrize("fail", [False, True])
async def test_factory_builds_and_closes_the_real_client(
    thinking: ThinkingMode | None, fail: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import anthropic

    from taskbot.infrastructure.extraction.claude import DEFAULT_THINKING, ClaudeExtractor
    from taskbot.infrastructure.extraction.factory import open_extractor

    original = anthropic.AsyncAnthropic
    clients: list[AsyncAnthropic] = []
    options: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        if fail:
            raise RuntimeError("caller")
        return reply()

    def create(*, api_key: str, base_url: str, max_retries: int, timeout: float) -> AsyncAnthropic:
        options.append(
            {"key": api_key, "base_url": base_url, "retries": max_retries, "timeout": timeout}
        )
        client = original(
            api_key=api_key,
            base_url=base_url,
            max_retries=max_retries,
            timeout=timeout,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(anthropic, "AsyncAnthropic", create)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://invalid.example")
    settings = ExtractorSettingsMixin(task_extractor="claude", anthropic_api_key=SecretStr("test"))
    with pytest.raises(RuntimeError, match="caller") if fail else nullcontext():
        async with open_extractor(
            settings, deadline_seconds=8, thinking=thinking, interrupts=(RuntimeError,)
        ) as extractor:
            assert isinstance(extractor, ClaudeExtractor)
            assert not (await extractor.extract(MessageText.parse("Book a meeting"))).degraded
    assert len(clients) == 1
    assert clients[0].is_closed()
    assert options == [
        {"key": "test", "base_url": "https://api.anthropic.com", "retries": 0, "timeout": 8}
    ]
    assert requests[0]["thinking"] == {"type": thinking or DEFAULT_THINKING}
    assert logging.getLogger("anthropic").level == logging.WARNING
    assert logging.getLogger("httpx2").level == logging.WARNING


@pytest.mark.parametrize("key", [None, "", " \t "])
async def test_missing_key_uses_one_degraded_fallback_warning(
    key: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.infrastructure.extraction.factory import open_extractor

    settings = ExtractorSettingsMixin(
        task_extractor="claude", anthropic_api_key=None if key is None else SecretStr(key)
    )
    async with open_extractor(settings, deadline_seconds=1) as extractor:
        result = await extractor.extract(MessageText.parse("Keep text"))
    assert result.degraded
    assert result.extractor == "heuristic"
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING


async def test_factory_without_a_key_does_not_load_the_sdk() -> None:
    script = """
import asyncio, sys
from taskbot.infrastructure.extraction.factory import open_extractor
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin
from taskbot.domain.text import MessageText
assert 'anthropic' not in sys.modules
async def main():
    for provider in ('heuristic', 'claude'):
        settings = ExtractorSettingsMixin(task_extractor=provider)
        async with open_extractor(settings, deadline_seconds=1) as extractor:
            await extractor.extract(MessageText.parse('Keep text'))
    assert 'anthropic' not in sys.modules
asyncio.run(main())
"""
    result = await run_process(sys.executable, "-c", script, cwd=BACKEND_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_prompt_is_large_enough_for_the_cache_prefix() -> None:
    from taskbot.infrastructure.extraction.prompt import SYSTEM_PROMPT

    assert len(SYSTEM_PROMPT) >= 2600
