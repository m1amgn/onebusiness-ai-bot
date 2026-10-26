import json
from pathlib import Path


async def test_owner_acceptance_rows_match_frozen_results() -> None:
    from tests.acceptance.replay_s4 import run_rows

    expected_path = Path(__file__).parents[1] / "acceptance" / "s4_expected.json"
    expected = json.loads(expected_path.read_text())
    assert await run_rows() == expected
