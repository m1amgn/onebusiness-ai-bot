"""Read-only S10 owner replay, using the real dispatcher and in-memory ports."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.domain.tasks import TaskStatus

# A directly executed replay needs the backend tests package on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.bot import HandlerHarness, message_update
from tests.fakes.clock import FakeClock

ROWS = tuple(f"T{index}" for index in range(1, 11))
ROOT = Path(__file__).parent


def acceptance_messages() -> list[str]:
    data = json.loads((ROOT / "s9_rows.json").read_text())
    return [row["text"] for row in data["rows"]]


async def add_tasks(
    harness: HandlerHarness, titles: list[str], *, in_progress: int = 0, completed: int = 0
) -> None:
    dispatcher = harness.dispatcher()
    async with harness.bot() as bot:
        for number, title in enumerate(titles, start=1):
            await dispatcher.feed_update(bot, message_update(number, title))
    change = ChangeTaskStatus(harness.store, FakeClock(datetime(2026, 1, 1, tzinfo=UTC)))
    for index, task in enumerate(tuple(harness.store.state.tasks.values())):
        if index < in_progress:
            await change.execute(task.user_id, task.id, TaskStatus.IN_PROGRESS)
        elif index < in_progress + completed:
            await change.execute(task.user_id, task.id, TaskStatus.COMPLETED)
    harness.notifier.replies.clear()
    harness.publisher.events.clear()
    harness.store.calls.items.clear()


async def run_scenario(row: str) -> HandlerHarness:
    harness = HandlerHarness()
    if row in {"T2", "T3", "T4", "T5"}:
        titles = acceptance_messages()
        if row == "T5":
            titles.extend(f"Extra task {number}" for number in range(9, 21))
        await add_tasks(harness, titles, completed=8 if row == "T4" else 0)
        if row == "T3":
            change = ChangeTaskStatus(harness.store, FakeClock(datetime(2026, 1, 1, tzinfo=UTC)))
            await change.execute(1, 2, TaskStatus.IN_PROGRESS)
            await change.execute(1, 1, TaskStatus.COMPLETED)
    if row == "T6":
        await add_tasks(harness, ["𝐀" * 120] * 16, in_progress=14)
    harness.notifier.replies.clear()
    harness.publisher.events.clear()
    update = message_update(100, "/tasks", group=row == "T7")
    if row == "T8":
        update = message_update(100, voice=True, caption="/tasks")
    elif row == "T9":
        update = message_update(100, "/start")
    elif row == "T10":
        update = message_update(100, "/help")
    async with harness.bot() as bot:
        await harness.dispatcher().feed_update(bot, update)
    return harness


async def run_rows() -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for row in ROWS:
        harness = await run_scenario(row)
        texts = [reply.text for reply in harness.notifier.replies]
        results[row] = {"replies": texts, "utf16": len(texts[0].encode("utf-16-le")) // 2}
    return results


async def main() -> None:
    actual = await run_rows()
    print(json.dumps(actual, ensure_ascii=False, indent=2))
    expected = json.loads((ROOT / "s10_expected.json").read_text())
    if actual != expected:
        raise SystemExit("S10 owner acceptance differs from the frozen T1-T10 expectations")
    print("S10 T1-T10 match; T11 is covered by the bot runtime integration tests.")


if __name__ == "__main__":
    asyncio.run(main())
