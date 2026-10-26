from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from taskbot.domain.extraction import Extraction, ExtractorName, TaskDraft
from tests.acceptance import replay_s9
from tests.processes import run_process

if TYPE_CHECKING:
    from taskbot.infrastructure.extraction.claude import ThinkingMode

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def outcome(
    *,
    title: str = "Renew car insurance",
    description: str = "Before 3 March, policy number AB-12345.",
    cache_read: int = 512,
    degraded: bool = False,
    thinking: ThinkingMode = "disabled",
    reason: str | None = None,
) -> replay_s9.RowResult:
    return replay_s9.RowResult(
        extraction=Extraction(
            task=TaskDraft(title=title, description=description),
            extractor=ExtractorName.HEURISTIC if degraded else ExtractorName.CLAUDE,
            degraded=degraded,
        ),
        thinking=thinking,
        latency_ms=12.5,
        usage={
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": 0,
        },
        reason=reason,
    )


def test_replay_loads_the_frozen_owner_rows() -> None:
    rows = replay_s9.load_rows()
    assert [row.id for row in rows] == [f"A{index}" for index in range(1, 9)]
    assert len(rows[-1].text) == 2276
    assert len(rows[-1].must_keep) == 16


def test_replay_refuses_a_modified_copy_of_the_frozen_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed_rows = tmp_path / "s9_rows.json"
    changed_rows.write_bytes(replay_s9.ROWS_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(replay_s9, "ROWS_PATH", changed_rows)

    with pytest.raises(ValueError, match="differs from the owner-accepted SHA-256"):
        replay_s9.load_rows()


def test_fact_check_ignores_case_and_whitespace_but_reports_a_missing_fact() -> None:
    row = replay_s9.AcceptanceRow(
        id="A6", text="Synthetic", must_keep=("3  MARCH", "ab-\n12345", "missing fact")
    )
    assert replay_s9.check_row(row, outcome(), index=1) == ["must_keep:missing fact"]


@pytest.mark.parametrize(
    ("fact", "description"),
    [
        ("3 ноября", "13 ноября"),
        ("305", "1305"),
        ("45000", "145000"),
        ("48", "148"),
        ("48", "480"),
        ("12500", "125000"),
        ("AB-12345", "AB-123456"),
        ("305", "x305"),
        ("Белов", "Небелов"),
    ],
)
def test_fact_check_rejects_numeric_supersets_and_letter_prefixes(
    fact: str, description: str
) -> None:
    row = replay_s9.AcceptanceRow(id="A8", text="Synthetic", must_keep=(fact,))
    assert replay_s9.check_row(row, outcome(description=description), index=0) == [
        f"must_keep:{fact}"
    ]


def test_fact_cannot_span_the_title_description_boundary() -> None:
    row = replay_s9.AcceptanceRow(id="A8", text="Synthetic", must_keep=("3 ноября",))
    result = outcome(title="Reminder on 3", description="ноября")
    assert replay_s9.check_row(row, result, index=0) == ["must_keep:3 ноября"]


@pytest.mark.parametrize(
    ("fact", "description"),
    [
        ("понедельник", "До понедельника"),
        ("прогноз", "По прогнозу"),
        ("Белов", "Позвонить Белову"),
        ("Садов", "На Садовой"),
        ("AB-12345", "policy: AB- 12\t345."),
        ("305", "Room 305"),
    ],
)
def test_fact_check_preserves_real_word_edges_and_accepts_alphabetic_suffixes(
    fact: str, description: str
) -> None:
    row = replay_s9.AcceptanceRow(id="A8", text="Synthetic", must_keep=(fact,))
    assert replay_s9.check_row(row, outcome(description=description), index=0) == []


@pytest.mark.parametrize(
    ("description", "errors"),
    [
        ("Ну, э-э, сделай это.", ["forbidden_words:ну", "forbidden_words:э-э"]),
        ("Нулевой этап.", []),
    ],
)
def test_filler_check_uses_whole_words(description: str, errors: list[str]) -> None:
    row = replay_s9.AcceptanceRow(id="A2", text="Synthetic", forbidden_words=("ну", "э-э"))
    assert replay_s9.check_row(row, outcome(description=description), index=0) == errors


def test_injection_check_searches_anywhere_in_the_title() -> None:
    row = replay_s9.AcceptanceRow(id="A7", text="Synthetic", forbidden_title=("HACKED",))
    assert replay_s9.check_row(row, outcome(title="prefixHaCkEdsuffix"), index=0) == [
        "forbidden_title:HACKED"
    ]


def test_cache_hit_is_required_after_the_first_row_in_each_mode() -> None:
    row = replay_s9.AcceptanceRow(id="A6", text="Synthetic")
    result = outcome(cache_read=0)
    assert replay_s9.check_row(row, result, index=0) == []
    assert replay_s9.check_row(row, result, index=1) == ["cache_read_input_tokens:expected > 0"]


@pytest.mark.parametrize(
    ("thinking", "reason", "errors"),
    [
        ("adaptive", "timeout", []),
        ("disabled", "timeout", ["degraded:timeout"]),
        ("adaptive", "api:RateLimitError", ["degraded:api:RateLimitError"]),
    ],
)
def test_only_adaptive_deadlines_are_recorded_as_permitted_fallbacks(
    thinking: ThinkingMode, reason: str, errors: list[str]
) -> None:
    row = replay_s9.AcceptanceRow(id="A6", text="Synthetic", must_keep=("missing",))
    result = outcome(degraded=True, thinking=thinking, reason=reason)
    assert replay_s9.check_row(row, result, index=1) == errors


@pytest.mark.parametrize(
    ("provider", "key", "variable"),
    [
        ("heuristic", "synthetic-key", "TASK_EXTRACTOR"),
        ("claude", "", "ANTHROPIC_API_KEY"),
        ("claude", " \t ", "ANTHROPIC_API_KEY"),
    ],
)
async def test_live_command_refuses_missing_configuration_without_any_api_call(
    provider: str, key: str, variable: str
) -> None:
    result = await run_process(
        sys.executable,
        "tests/acceptance/replay_s9.py",
        "--thinking",
        "disabled",
        cwd=BACKEND_ROOT,
        env=dict(
            os.environ,
            TASKBOT_ENV_FILE="/dev/null",
            TASK_EXTRACTOR=provider,
            ANTHROPIC_API_KEY=key,
            DATABASE_URL="invalid",
            REDIS_URL="invalid",
        ),
    )
    assert result.returncode == 2
    assert variable in result.stderr
    assert "synthetic-key" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("scenario", ["success", "missing-fact", "adaptive-timeout"])
async def test_replay_prints_all_rows_and_restores_its_logging_state(
    scenario: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import logging
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from taskbot.domain.text import MessageText
    from taskbot.infrastructure.settings.extractor import ExtractorSettingsMixin

    fail = scenario == "missing-fact"
    thinking: ThinkingMode = "adaptive" if scenario == "adaptive-timeout" else "disabled"
    row = replay_s9.AcceptanceRow(
        id="A6", text="Synthetic", must_keep=("missing" if fail else "AB-12345",)
    )
    logger = logging.getLogger(replay_s9.ADAPTER_LOGGER)
    before = (logger.level, list(logger.handlers))

    class Extractor:
        calls = 0

        async def extract(self, text: MessageText) -> Extraction:
            self.calls += 1
            if scenario == "adaptive-timeout" and self.calls == 2:
                logger.warning("claude extraction fallback: timeout", extra={"reason": "timeout"})
                return outcome(degraded=True).extraction
            logger.info("claude extraction", extra={"usage": outcome().usage})
            return outcome().extraction

    @asynccontextmanager
    async def open_extractor(
        settings: ExtractorSettingsMixin,
        *,
        deadline_seconds: float,
        thinking: ThinkingMode,
    ) -> AsyncIterator[Extractor]:
        assert deadline_seconds == 8
        assert thinking == ("adaptive" if scenario == "adaptive-timeout" else "disabled")
        yield Extractor()

    monkeypatch.setattr(replay_s9, "open_extractor", open_extractor)
    monkeypatch.setattr(replay_s9, "load_rows", lambda: (row, row))
    assert await replay_s9.replay(ExtractorSettingsMixin(), thinking=thinking) == int(fail)
    output = capsys.readouterr()
    assert ("A6: must_keep:missing" in output.err) is fail
    rows = [json.loads(line) for line in output.out.splitlines()]
    assert len(rows) == 2
    assert all(row["row"] == "A6" for row in rows)
    assert rows[0]["extractor"] == "claude"
    assert rows[0]["usage"]["cache_read_input_tokens"] == 512
    if scenario == "adaptive-timeout":
        assert rows[1]["extractor"] == "heuristic"
        assert rows[1]["degraded"] is True
        assert rows[1]["reason"] == "timeout"
        assert rows[1]["usage"] is None
    else:
        assert rows[1]["extractor"] == "claude"
        assert rows[1]["usage"]["cache_read_input_tokens"] == 512
    assert (logger.level, logger.handlers) == before
