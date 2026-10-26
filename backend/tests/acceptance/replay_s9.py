"""Opt-in paid replay of the owner's eight synthetic extraction rows."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, NonNegativeInt, TypeAdapter

from taskbot.domain.extraction import Extraction
from taskbot.domain.text import MessageText
from taskbot.entrypoints.settings import load_settings
from taskbot.infrastructure.extraction.factory import has_anthropic_key, open_extractor
from taskbot.infrastructure.settings.base import BaseAppSettings
from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin

if TYPE_CHECKING:
    from taskbot.infrastructure.extraction.claude import ThinkingMode

ADAPTER_LOGGER = "taskbot.infrastructure.extraction.claude"
ROWS_PATH = Path(__file__).with_name("s9_rows.json")
ROWS_SHA256 = "481b4cade068a92d9caf996cb30381a03595ddc430e4c19ba8c492be72a70595"
DEADLINE_SECONDS = 8
USAGE = TypeAdapter(dict[str, NonNegativeInt])


class ReplaySettings(BaseAppSettings, ExtractorSettingsMixin):
    pass


class AcceptanceRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    must_keep: tuple[str, ...] = ()
    forbidden_words: tuple[str, ...] = ()
    forbidden_title: tuple[str, ...] = ()


class AcceptanceRows(BaseModel):
    rows: tuple[AcceptanceRow, ...]


@dataclass(frozen=True, slots=True)
class RowResult:
    extraction: Extraction
    thinking: ThinkingMode
    latency_ms: float
    usage: dict[str, int] | None
    reason: str | None


class MetricsCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.usage: dict[str, int] | None = None
        self.reason: str | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if record.name != ADAPTER_LOGGER:
            return
        usage = record.__dict__.get("usage")
        if usage is not None:
            self.usage = USAGE.validate_python(usage, strict=True)
        reason = record.__dict__.get("reason")
        if isinstance(reason, str):
            self.reason = reason


def load_rows() -> tuple[AcceptanceRow, ...]:
    content = ROWS_PATH.read_bytes()
    if hashlib.sha256(content).hexdigest() != ROWS_SHA256:
        raise ValueError("s9_rows.json differs from the owner-accepted SHA-256")
    rows = AcceptanceRows.model_validate_json(content).rows
    if [row.id for row in rows] != [f"A{index}" for index in range(1, 9)]:
        raise ValueError("s9_rows.json must contain A1-A8 in order")
    return rows


def compact(value: str) -> str:
    return "".join(value.casefold().split())


def check_row(row: AcceptanceRow, result: RowResult, *, index: int) -> list[str]:
    if result.extraction.degraded:
        if result.thinking == "adaptive" and result.reason == "timeout":
            return []
        return [f"degraded:{result.reason or 'unknown'}"]
    task = result.extraction.task
    combined = compact(task.title + "\n" + task.description)
    errors = [f"must_keep:{fact}" for fact in row.must_keep if compact(fact) not in combined]
    errors.extend(
        f"forbidden_words:{word}"
        for word in row.forbidden_words
        if re.search(rf"(?<!\w){re.escape(word.casefold())}(?!\w)", task.description.casefold())
    )
    errors.extend(
        f"forbidden_title:{word}"
        for word in row.forbidden_title
        if word.casefold() in task.title.casefold()
    )
    if result.usage is None:
        errors.append("usage:missing")
    elif index > 0 and result.usage.get("cache_read_input_tokens", 0) <= 0:
        errors.append("cache_read_input_tokens:expected > 0")
    return errors


def print_result(row: AcceptanceRow, result: RowResult) -> None:
    print(
        json.dumps(
            {
                "row": row.id,
                "thinking": result.thinking,
                **result.extraction.task.model_dump(),
                "extractor": result.extraction.extractor.value,
                "degraded": result.extraction.degraded,
                "reason": result.reason,
                "latency_ms": result.latency_ms,
                "usage": result.usage,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


async def replay(settings: ExtractorSettingsMixin, *, thinking: ThinkingMode) -> int:
    rows = load_rows()
    logger = logging.getLogger(ADAPTER_LOGGER)
    previous_level = logger.level
    capture = MetricsCapture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    failures = 0
    try:
        async with open_extractor(
            settings, deadline_seconds=DEADLINE_SECONDS, thinking=thinking
        ) as extractor:
            for index, row in enumerate(rows):
                capture.usage, capture.reason = None, None
                started = time.perf_counter()
                extraction = await extractor.extract(MessageText.parse(row.text))
                result = RowResult(
                    extraction=extraction,
                    thinking=thinking,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    usage=capture.usage,
                    reason=capture.reason,
                )
                print_result(row, result)
                for error in check_row(row, result, index=index):
                    print(f"{row.id}: {error}", file=sys.stderr)
                    failures += 1
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        capture.close()
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paid owner replay: eight synthetic messages, one thinking mode per process."
    )
    parser.add_argument("--thinking", choices=("disabled", "adaptive"), default="disabled")
    args = parser.parse_args()
    settings = load_settings(ReplaySettings)
    missing: list[str] = []
    if settings.task_extractor != "claude":
        missing.append("TASK_EXTRACTOR=claude")
    if not has_anthropic_key(settings):
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        parser.error("Set " + " and ".join(missing) + " before this paid replay")
    raise SystemExit(asyncio.run(replay(settings, thinking=args.thinking)))


if __name__ == "__main__":
    main()
