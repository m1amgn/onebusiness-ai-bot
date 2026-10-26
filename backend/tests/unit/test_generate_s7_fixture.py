import asyncio
from pathlib import Path

from tests.acceptance.generate_s7_fixture import fixture_json

FIXTURE = Path(__file__).resolve().parents[3] / "frontend/src/app/s7Tasks.fixture.json"


async def test_dashboard_fixture_matches_canonical_api_output() -> None:
    expected = await asyncio.to_thread(FIXTURE.read_text)
    assert await fixture_json() == expected
