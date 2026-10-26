import json
from datetime import UTC, datetime

import pytest

from taskbot.application.dashboard_token import IssueDashboardToken
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.users import TelegramIdentity
from taskbot.entrypoints.api.app import create_app
from taskbot.infrastructure.tokens import Sha256TokenHasher
from tests.api_fixtures import api_client, fake_api
from tests.fakes.clock import FakeClock
from tests.fakes.tokens import FakeTokenGenerator


@pytest.mark.parametrize(
    "header",
    [
        None,
        b"Basic dA==",
        b"Bearer",
        "Bearer токен".encode(),
        b"Bearer " + b"x" * 129,
        b"Bearer unknown",
    ],
)
async def test_bearer_failures_have_one_response(header: bytes | None) -> None:
    fixture = await fake_api()
    headers = {} if header is None else {b"Authorization": header}
    async with api_client(fixture, token=None) as client:
        response = await client.get("/api/tasks", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Send /dashboard to the bot for a new link"}


async def test_injected_services_work_without_lifespan_and_routes_require_bearer() -> None:
    fixture = await fake_api()
    app = create_app(fixture.services)
    async with api_client(fixture, token=None) as client:
        for path, operations in app.openapi()["paths"].items():
            if path in {"/health", "/api/events"}:
                continue
            for method in operations:
                response = await client.request(
                    method,
                    path.replace("{task_id}", str(fixture.task_id)),
                    json={"status": "pending"},
                )
                assert response.status_code == 401, (method, path, response.text)
        response = await client.get(
            "/api/tasks", headers={"Authorization": f"Bearer {fixture.token}"}
        )
    assert response.status_code == 200
    assert response.json()["next_cursor"] is None
    assert response.json()["items"][0]["source_text"] == "Call home"


async def test_patch_commits_then_publishes_and_noop_keeps_source() -> None:
    fixture = await fake_api()
    fixture.store.calls.items.clear()
    async with api_client(fixture, token=fixture.token) as client:
        response = await client.patch(
            f"/api/tasks/{fixture.task_id}", json={"status": "in_progress"}
        )
        assert response.status_code == 200
        assert response.json()["source_text"] == "Call home"
        assert response.json()["status"] == "in_progress"
        assert fixture.store.state.tasks[fixture.task_id].status == "in_progress"
        assert fixture.store.calls.items.index("uow.commit") < fixture.store.calls.items.index(
            "events.publish"
        )
        assert len(fixture.publisher.events) == 1
        assert fixture.publisher.events[0].type == "task.updated"
        assert fixture.publisher.events[0].task.source_text == "Call home"
        again = await client.patch(f"/api/tasks/{fixture.task_id}", json={"status": "in_progress"})
        assert again.status_code == 200
        assert again.json() == response.json()
        assert len(fixture.publisher.events) == 1
        fixture.publisher.fail = True
        failed_publish = await client.patch(
            f"/api/tasks/{fixture.task_id}", json={"status": "completed"}
        )
        assert failed_publish.status_code == 200
        assert fixture.store.state.tasks[fixture.task_id].status == "completed"


@pytest.mark.parametrize(
    "query",
    ["cursor=abc", "cursor=9999999999.1", "cursor=1.100", "limit=0", "limit=201", "status=done"],
)
async def test_list_refuses_invalid_paging_parameters(query: str) -> None:
    fixture = await fake_api()
    async with api_client(fixture, token=fixture.token) as client:
        response = await client.get("/api/tasks?" + query)
    assert response.status_code == 422


@pytest.mark.parametrize(("task_id", "expected"), [(999, 404), (2147483648, 422), (0, 422)])
async def test_patch_refuses_missing_or_invalid_id(task_id: int, expected: int) -> None:
    fixture = await fake_api()
    async with api_client(fixture, token=fixture.token) as client:
        response = await client.patch(f"/api/tasks/{task_id}", json={"status": "completed"})
    assert response.status_code == expected


@pytest.mark.parametrize(
    "body", [{"status": "done"}, {"status": None}, {}, {"status": "completed", "title": "x"}, None]
)
async def test_patch_validates_status_at_http_boundary(body: object) -> None:
    fixture = await fake_api()
    async with api_client(fixture, token=fixture.token) as client:
        response = await client.patch(
            f"/api/tasks/{fixture.task_id}",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        invalid_json = await client.patch(
            f"/api/tasks/{fixture.task_id}",
            content="{",
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == invalid_json.status_code == 422


async def test_missing_ticket_is_unauthorized() -> None:
    fixture = await fake_api()
    async with api_client(fixture, token=None) as client:
        response = await client.get("/api/events")
    assert response.status_code == 401


def test_event_schema_is_canonical_and_discriminated() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    assert schemas["TaskView"]["properties"]["title"]["maxLength"] == 120
    assert schemas["TaskView"]["properties"]["description"]["maxLength"] == 4096
    assert "source_text" in schemas["TaskView"]["required"]
    assert "user_id" not in schemas["TaskCreated"]["properties"]
    assert schemas["ServerEvent"]["discriminator"]["propertyName"] == "type"
    assert len(schemas["ServerEvent"]["oneOf"]) == 4


async def test_task_ownership_is_checked_with_another_valid_token() -> None:

    fixture = await fake_api()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    foreign = await IssueDashboardToken(
        fixture.store,
        RegisterOrGetUser(fixture.store, clock),
        FakeTokenGenerator(1),
        Sha256TokenHasher(),
        clock,
    ).execute(TelegramIdentity(8, "Another user"))
    async with api_client(fixture, token=foreign.token) as client:
        response = await client.patch(f"/api/tasks/{fixture.task_id}", json={"status": "completed"})
        listing = await client.get("/api/tasks")
    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}
    assert listing.json() == {"items": [], "next_cursor": None}
    assert fixture.store.state.tasks[fixture.task_id].status == "pending"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/docs/oauth2-redirect", "/openapi.json"])
async def test_runtime_api_has_no_public_documentation_endpoints(path: str) -> None:
    fixture = await fake_api()
    async with api_client(fixture, token=None) as client:
        response = await client.get(path)
    assert response.status_code == 404


def test_expected_error_responses_share_a_generated_contract() -> None:
    paths = create_app().openapi()["paths"]
    for path, method, statuses in [
        ("/api/tasks", "get", (401,)),
        ("/api/tasks/{task_id}", "patch", (401, 404)),
        ("/api/tasks/{task_id}", "delete", (401, 404)),
        ("/api/realtime/tickets", "post", (401,)),
        ("/api/events", "get", (401, 503)),
    ]:
        for status in statuses:
            schema = paths[path][method]["responses"][str(status)]["content"]["application/json"][
                "schema"
            ]
            assert schema == {"$ref": "#/components/schemas/ApiError"}
