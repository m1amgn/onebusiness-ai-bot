import asyncio
import json
import sys
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from taskbot.entrypoints.api.app import create_app
from taskbot.entrypoints.api.tasks import TaskPage

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.acceptance.replay_s6 import build_scenario


async def fixture_json() -> str:
    scenario = await build_scenario()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(scenario.services)),
        base_url="http://test",
        headers={"Authorization": f"Bearer {scenario.token}"},
    ) as client:
        response = await client.get("/api/tasks?limit=50")
        response.raise_for_status()
        page = TaskPage.model_validate(response.json())
    return (
        json.dumps(
            [task.model_dump(mode="json") for task in page.items],
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[3] / "frontend/src/app/s7Tasks.fixture.json"
    destination.write_text(asyncio.run(fixture_json()))
    print("Wrote eight synthetic API tasks to frontend/src/app/s7Tasks.fixture.json")
