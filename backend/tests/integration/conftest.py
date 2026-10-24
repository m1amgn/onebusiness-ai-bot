import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests.integration.settings import InfraTestSettings


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.integration)


def ensure_reachable(service: str, url: str, default_port: int) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=2):
            return
    except OSError:
        pytest.exit(f"{service} at {host}:{port} is unreachable: run yarn infra:up", returncode=2)


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    if session.config.option.collectonly:
        return
    if not any(item.get_closest_marker("integration") for item in session.items):
        return
    settings = InfraTestSettings()
    ensure_reachable("PostgreSQL", str(settings.database_url), 5432)
    ensure_reachable("Redis", str(settings.redis_url), 6379)
