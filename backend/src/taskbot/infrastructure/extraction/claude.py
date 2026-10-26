from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Literal

from anthropic import APIError, APITimeoutError
from pydantic import ValidationError

from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from taskbot.domain.text import MessageText
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.extraction.prompt import SYSTEM_PROMPT

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types.parsed_message import ParsedMessage

ThinkingMode = Literal["disabled", "adaptive"]
DEFAULT_THINKING: ThinkingMode = "disabled"
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_SAFE_STOP_REASONS = frozenset(
    {
        "max_tokens",
        "stop_sequence",
        "tool_use",
        "pause_turn",
        "refusal",
        "model_context_window_exceeded",
    }
)
logger = logging.getLogger(__name__)


def quiet_sdk_loggers() -> None:
    # The SDK applies ANTHROPIC_LOG while importing; call this afterwards.
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)


class _FallbackRequiredError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _task_from_response(response: ParsedMessage[TaskDraft]) -> TaskDraft:
    if response.stop_reason != "end_turn":
        stop = response.stop_reason
        safe_stop = stop if isinstance(stop, str) and stop in _SAFE_STOP_REASONS else "unknown"
        raise _FallbackRequiredError(f"stop:{safe_stop}")
    task = response.parsed_output
    if task is None:
        raise _FallbackRequiredError("no_output")
    if not any(character.isalnum() for character in task.title):
        raise _FallbackRequiredError("invalid_output")
    return task


def _raise_interrupt(error: BaseException, interrupts: tuple[type[BaseException], ...]) -> None:
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, interrupts):
            raise current
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)


def _failure_reason(error: Exception) -> str:
    if isinstance(error, _FallbackRequiredError):
        return error.reason
    if isinstance(error, (TimeoutError, APITimeoutError)):
        return "timeout"
    if isinstance(error, APIError):
        return f"api:{type(error).__name__}"
    if isinstance(error, ValidationError):
        return "invalid_output"
    return f"error:{type(error).__name__}"


def _token_count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _usage_counts(response: ParsedMessage[TaskDraft]) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {name: _token_count(getattr(usage, name, None)) for name in USAGE_FIELDS}


class ClaudeExtractor:
    def __init__(
        self,
        client: AsyncAnthropic,
        *,
        model: str,
        deadline_seconds: float,
        thinking: ThinkingMode,
        interrupts: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._client = client
        self._model = model
        self._deadline_seconds = deadline_seconds
        self._thinking = thinking
        self._interrupts = interrupts
        self._fallback = HeuristicExtractor(degraded=True)

    async def extract(self, text: MessageText) -> Extraction:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._deadline_seconds):
                response = await self._client.messages.parse(
                    model=self._model,
                    max_tokens=4096,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": f"<message>\n{text.value}\n</message>"}],
                    output_format=TaskDraft,
                    output_config={"effort": "low"},
                    thinking=(
                        {"type": "disabled"}
                        if self._thinking == "disabled"
                        else {"type": "adaptive"}
                    ),
                )
                result = Extraction(
                    task=_task_from_response(response),
                    extractor=ExtractorName.CLAUDE,
                    degraded=False,
                )
        except Exception as error:
            _raise_interrupt(error, self._interrupts)
            reason = _failure_reason(error)
            logger.warning("claude extraction fallback: %s", reason, extra={"reason": reason})
            return await self._fallback.extract(text)

        logger.info(
            "claude extraction",
            extra={
                "usage": _usage_counts(response),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )
        return result
