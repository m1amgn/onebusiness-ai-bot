import json
import os
import sys
from pathlib import Path

import pytest

from taskbot.domain.extraction import Extraction
from taskbot.domain.text import MessageText
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from tests.acceptance import replay_s6
from tests.processes import run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_offline_s6_replay_checks_all_owner_rows_without_runtime_settings() -> None:
    result = await run_process(
        sys.executable,
        "tests/acceptance/replay_s6.py",
        cwd=BACKEND_ROOT,
        env=dict(
            os.environ,
            TASKBOT_ENV_FILE="/dev/null",
            DATABASE_URL="invalid",
            REDIS_URL="invalid",
            TASK_EXTRACTOR="claude",
            ANTHROPIC_API_KEY="synthetic-key-must-not-be-used",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "synthetic-key-must-not-be-used" not in result.stdout + result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert [row["row"] for row in rows] == [f"W{index}" for index in range(1, 9)]
    assert all(row["passed"] and row["actual"] == row["expected"] for row in rows)
    assert rows[3]["actual"]["pages"] == [["A8", "A7", "A6"], ["A5", "A4", "A3"], ["A2", "A1"]]
    assert rows[5]["actual"]["message_text"] is None


async def test_s6_replay_names_a_real_outcome_that_differs_from_the_accepted_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = HeuristicExtractor.extract

    async def changed_title(self: HeuristicExtractor, text: MessageText) -> Extraction:
        result = await original(self, text)
        if text.value.startswith("Подготовить к понедельнику"):
            return result.model_copy(
                update={"task": result.task.model_copy(update={"title": "Incorrect title"})}
            )
        return result

    monkeypatch.setattr(HeuristicExtractor, "extract", changed_title)
    assert await replay_s6.replay() == 1
    output = capsys.readouterr()
    rows = [json.loads(line) for line in output.out.splitlines()]
    assert len(rows) == 8
    assert [row["row"] for row in rows if not row["passed"]] == ["W2"]
    assert "W2: outcome differs from the accepted After S6 table" in output.err
    assert rows[1]["actual"]["title"] == "Incorrect title"
    assert rows[1]["expected"]["title_length"] == 119
