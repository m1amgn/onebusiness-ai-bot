import asyncio
import json
import sys
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from taskbot.application.change_task_status import ChangeTaskStatus
from taskbot.application.create_tasks import (
    CreateTasksCommand,
    CreateTasksFromMessage,
    DuplicateMessage,
)
from taskbot.application.dashboard_token import AuthenticateToken, IssueDashboardToken
from taskbot.application.delete_task import DeleteTask
from taskbot.application.list_tasks import ListTasks
from taskbot.application.realtime_tickets import ConsumeRealtimeTicket, IssueRealtimeTicket
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.application.stream_events import StreamUserEvents
from taskbot.domain.events import TaskUpdated
from taskbot.domain.messages import MessageKind, MessageRef
from taskbot.domain.users import TelegramIdentity
from taskbot.entrypoints.api.app import create_app
from taskbot.entrypoints.api.container import ApiServices
from taskbot.entrypoints.api.realtime import RealtimeTicket
from taskbot.entrypoints.api.tasks import TaskPage
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.tokens import Sha256TokenHasher

# Direct execution includes tests/acceptance, but the reusable test doubles need backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.acceptance.replay_s9 import load_rows
from tests.fakes.clock import FakeClock
from tests.fakes.messaging import FakeEventPublisher, FakeTicketStore
from tests.fakes.persistence import FakeStore
from tests.fakes.stream import FakeElapsed, FakeEventSubscriber, FakeEventSubscription
from tests.fakes.tokens import FakeTokenGenerator

IDENTITY = TelegramIdentity(7001, "Synthetic replay owner")
TITLES = {
    "A1": "Купить молоко",
    "A4": (
        "Подготовить к понедельнику квартальный отчёт для совета директоров с разбивкой по "
        "регионам, сравнением с прошлым годом…"
    ),
    "A8": (
        "Так, ну короче, слушай, это по переезду офиса, надо всё расписать, а то опять всё "
        "забудем. Значит, э-э, переезжаем со…"
    ),
}


@dataclass(frozen=True, slots=True)
class Scenario:
    services: ApiServices
    store: FakeStore
    publisher: FakeEventPublisher
    create: CreateTasksFromMessage
    issue_token: IssueDashboardToken
    commands: dict[str, CreateTasksCommand]
    task_ids: dict[str, int]
    sources: dict[str, str]
    token: str

    def labels(self, task_ids: list[int]) -> list[str]:
        by_task = {task_id: label for label, task_id in self.task_ids.items()}
        return [by_task.get(task_id, f"unknown task {task_id}") for task_id in task_ids]


async def build_scenario() -> Scenario:
    store = FakeStore()
    clock = FakeClock(datetime(2026, 10, 26, tzinfo=UTC))
    register = RegisterOrGetUser(store, clock)
    create = CreateTasksFromMessage(store, HeuristicExtractor(), register, clock)
    commands: dict[str, CreateTasksCommand] = {}
    task_ids: dict[str, int] = {}
    sources: dict[str, str] = {}
    for index, row in enumerate(load_rows(), start=1):
        command = CreateTasksCommand(
            IDENTITY, MessageRef(IDENTITY.telegram_user_id, index), MessageKind.TEXT, row.text
        )
        result = await create.execute(command)
        if isinstance(result, DuplicateMessage) or len(result.tasks) != 1:
            raise RuntimeError(f"Replay setup must create exactly one task for {row.id}")
        commands[row.id], task_ids[row.id], sources[row.id] = command, result.tasks[0].id, row.text
    hasher = Sha256TokenHasher()
    issue_token = IssueDashboardToken(store, register, FakeTokenGenerator(), hasher, clock)
    token = await issue_token.execute(IDENTITY)
    publisher = FakeEventPublisher(calls=store.calls)
    tickets = FakeTicketStore()
    elapsed = FakeElapsed()
    services = ApiServices(
        authenticate=AuthenticateToken(store, hasher),
        list_tasks=ListTasks(store),
        change_task_status=ChangeTaskStatus(store, clock),
        delete_task=DeleteTask(store),
        issue_ticket=IssueRealtimeTicket(tickets),
        consume_ticket=ConsumeRealtimeTicket(tickets, store),
        stream_events=StreamUserEvents(
            FakeEventSubscriber(FakeEventSubscription(elapsed)), store, elapsed
        ),
        publisher=publisher,
    )
    return Scenario(
        services, store, publisher, create, issue_token, commands, task_ids, sources, token.token
    )


async def check(
    row: str, expected: dict[str, object], outcome: Awaitable[dict[str, object]]
) -> bool:
    try:
        actual = await outcome
    except Exception as error:
        actual = {"error": type(error).__name__, "message": str(error)}
    passed = actual == expected
    print(
        json.dumps(
            {"row": row, "expected": expected, "actual": actual, "passed": passed},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not passed:
        print(f"{row}: outcome differs from the accepted After S6 table", file=sys.stderr)
    return passed


async def representation(client: AsyncClient, scenario: Scenario, label: str) -> dict[str, object]:
    response = await client.get("/api/tasks")
    page = TaskPage.model_validate(response.json())
    task = next((item for item in page.items if item.id == scenario.task_ids[label]), None)
    if task is None:
        return {"status": response.status_code, "item": None}
    return {
        "status": response.status_code,
        "title": task.title,
        "title_length": len(task.title),
        "description": task.description,
        "description_length": len(task.description),
        "source_text": task.source_text,
        "source_length": len(task.source_text),
    }


async def paging(client: AsyncClient, scenario: Scenario) -> dict[str, object]:
    cursor = None
    pages = []
    statuses = []
    for _ in range(3):
        params = {"limit": "3"}
        if cursor is not None:
            params["cursor"] = cursor
        response = await client.get("/api/tasks", params=params)
        statuses.append(response.status_code)
        page = TaskPage.model_validate(response.json())
        pages.append(scenario.labels([task.id for task in page.items]))
        cursor = page.next_cursor
    return {"statuses": statuses, "pages": pages, "next_cursor": cursor}


async def status_change(client: AsyncClient, scenario: Scenario) -> dict[str, object]:
    scenario.publisher.events.clear()
    response = await client.patch(
        f"/api/tasks/{scenario.task_ids['A6']}", json={"status": "in_progress"}
    )
    filtered = await client.get("/api/tasks", params={"status": "in_progress"})
    page = TaskPage.model_validate(filtered.json())
    events: list[dict[str, object]] = []
    for event in scenario.publisher.events:
        if isinstance(event, TaskUpdated):
            events.append(
                {
                    "type": event.type,
                    "row": scenario.labels([event.task.id])[0],
                    "source_text": event.task.source_text,
                }
            )
        else:
            events.append({"type": event.type})
    return {
        "patch_status": response.status_code,
        "list_status": filtered.status_code,
        "rows": scenario.labels([task.id for task in page.items]),
        "events": events,
    }


async def deletion(client: AsyncClient, scenario: Scenario) -> dict[str, object]:
    scenario.publisher.events.clear()
    task_id = scenario.task_ids["A5"]
    message_id = scenario.store.state.tasks[task_id].inbound_message_id
    response = await client.delete(f"/api/tasks/{task_id}")
    listed = await client.get("/api/tasks")
    page = TaskPage.model_validate(listed.json())
    before = len(scenario.store.state.tasks)
    repeated = await scenario.create.execute(scenario.commands["A5"])
    return {
        "delete_status": response.status_code,
        "body": response.text,
        "list_status": listed.status_code,
        "rows": scenario.labels([task.id for task in page.items]),
        "message_text": scenario.store.state.messages[message_id].text,
        "events": [event.model_dump(mode="json") for event in scenario.publisher.events],
        "duplicate": isinstance(repeated, DuplicateMessage),
        "seeded_tasks": len(scenario.store.state.tasks) - before,
    }


async def ownership(client: AsyncClient, scenario: Scenario) -> dict[str, object]:
    other = await scenario.issue_token.execute(TelegramIdentity(7002, "Synthetic other user"))
    task_id = scenario.task_ids["A1"]
    before = scenario.store.state.tasks[task_id]
    headers = {"Authorization": f"Bearer {other.token}"}
    patched = await client.patch(
        f"/api/tasks/{task_id}", json={"status": "in_progress"}, headers=headers
    )
    deleted = await client.delete(f"/api/tasks/{task_id}", headers=headers)
    return {
        "patch_status": patched.status_code,
        "delete_status": deleted.status_code,
        "a1_unchanged": scenario.store.state.tasks.get(task_id) == before,
    }


async def stale_ticket(client: AsyncClient, scenario: Scenario) -> dict[str, object]:
    issued = await client.post("/api/realtime/tickets")
    ticket = RealtimeTicket.model_validate(issued.json()).ticket
    await scenario.issue_token.execute(IDENTITY)
    try:
        async with asyncio.timeout(2):
            response = await client.get("/api/events", params={"ticket": ticket})
        status: int | str = response.status_code
    except TimeoutError:
        status = "stream did not reject the stale ticket within two seconds"
    return {"ticket_status": issued.status_code, "stream_status": status}


async def replay() -> int:
    scenario = await build_scenario()
    outcomes = []
    async with AsyncClient(
        transport=ASGITransport(app=create_app(scenario.services)),
        base_url="http://replay",
        headers={"Authorization": f"Bearer {scenario.token}"},
    ) as client:
        for row, label, title_length, source_length in (
            ("W1", "A1", 13, 13),
            ("W2", "A4", 119, 168),
            ("W3", "A8", 118, 2276),
        ):
            outcomes.append(
                await check(
                    row,
                    {
                        "status": 200,
                        "title": TITLES[label],
                        "title_length": title_length,
                        "description": "" if label == "A1" else scenario.sources[label],
                        "description_length": 0 if label == "A1" else source_length,
                        "source_text": scenario.sources[label],
                        "source_length": source_length,
                    },
                    representation(client, scenario, label),
                )
            )
        outcomes.append(
            await check(
                "W4",
                {
                    "statuses": [200, 200, 200],
                    "pages": [["A8", "A7", "A6"], ["A5", "A4", "A3"], ["A2", "A1"]],
                    "next_cursor": None,
                },
                paging(client, scenario),
            )
        )
        outcomes.append(
            await check(
                "W5",
                {
                    "patch_status": 200,
                    "list_status": 200,
                    "rows": ["A6"],
                    "events": [
                        {"type": "task.updated", "row": "A6", "source_text": scenario.sources["A6"]}
                    ],
                },
                status_change(client, scenario),
            )
        )
        outcomes.append(
            await check(
                "W6",
                {
                    "delete_status": 204,
                    "body": "",
                    "list_status": 200,
                    "rows": ["A8", "A7", "A6", "A4", "A3", "A2", "A1"],
                    "message_text": None,
                    "events": [{"type": "task.deleted", "task_id": scenario.task_ids["A5"]}],
                    "duplicate": True,
                    "seeded_tasks": 0,
                },
                deletion(client, scenario),
            )
        )
        outcomes.append(
            await check(
                "W7",
                {"patch_status": 404, "delete_status": 404, "a1_unchanged": True},
                ownership(client, scenario),
            )
        )
        outcomes.append(
            await check(
                "W8",
                {"ticket_status": 201, "stream_status": 401},
                stale_ticket(client, scenario),
            )
        )
    return int(not all(outcomes))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(replay()))
