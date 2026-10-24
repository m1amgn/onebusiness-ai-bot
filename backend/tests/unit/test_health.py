from httpx import ASGITransport, AsyncClient

from taskbot.entrypoints.api.app import create_app


async def test_health_is_public_and_returns_ok() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_contract_requires_defaulted_response_fields() -> None:
    schema = create_app().openapi()
    assert schema["components"]["schemas"]["Health"]["required"] == ["status"]
    assert "/health" in schema["paths"]
