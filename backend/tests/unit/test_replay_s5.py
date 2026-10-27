import json
from pathlib import Path


async def test_worker_owner_rows_match_frozen_chat_outcomes() -> None:
    from tests.acceptance.replay_s5 import run_rows

    expected = json.loads((Path(__file__).parents[1] / "acceptance/s5_expected.json").read_text())
    assert await run_rows() == expected
