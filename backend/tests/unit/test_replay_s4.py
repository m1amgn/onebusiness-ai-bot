import json
from pathlib import Path

from tests.acceptance.replay_s4 import run_rows


async def test_owner_acceptance_rows_match_frozen_results() -> None:
    expected_path = Path(__file__).parents[1] / "acceptance" / "s4_expected.json"
    expected = json.loads(expected_path.read_text())
    assert await run_rows() == expected
