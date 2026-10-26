from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from taskbot.domain.ports.services import TaskExtractor
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin

if TYPE_CHECKING:
    from taskbot.infrastructure.extraction.claude import ThinkingMode

logger = logging.getLogger(__name__)


def _api_key(settings: ExtractorSettingsMixin) -> str | None:
    if settings.anthropic_api_key is None:
        return None
    return settings.anthropic_api_key.get_secret_value().strip() or None


def has_anthropic_key(settings: ExtractorSettingsMixin) -> bool:
    """Allow replay preflight without exposing a key or importing the SDK."""
    return _api_key(settings) is not None


@asynccontextmanager
async def open_extractor(
    settings: ExtractorSettingsMixin,
    *,
    deadline_seconds: float,
    thinking: ThinkingMode | None = None,
    interrupts: tuple[type[BaseException], ...] = (),
) -> AsyncIterator[TaskExtractor]:
    if settings.task_extractor == "heuristic":
        yield HeuristicExtractor()
        return
    key = _api_key(settings)
    if key is None:
        logger.warning("Claude extraction is unavailable: missing API key")
        yield HeuristicExtractor(degraded=True)
        return

    import anthropic

    from taskbot.infrastructure.extraction.claude import (
        DEFAULT_THINKING,
        ClaudeExtractor,
        quiet_sdk_loggers,
    )

    quiet_sdk_loggers()
    async with anthropic.AsyncAnthropic(
        api_key=key,
        base_url="https://api.anthropic.com",
        max_retries=0,
        timeout=deadline_seconds,
    ) as client:
        yield ClaudeExtractor(
            client,
            model=settings.anthropic_model,
            deadline_seconds=deadline_seconds,
            thinking=thinking or DEFAULT_THINKING,
            interrupts=interrupts,
        )
