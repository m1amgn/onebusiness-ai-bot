from pathlib import Path

import pytest
from pydantic import ValidationError
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff

from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.settings.worker import BrokerSettings


@pytest.fixture(autouse=True)
def clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("REDIS_URL", "CELERY_BROKER_URL", "REDIS_NAMESPACE", "TASKBOT_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_broker_default_and_empty_override(tmp_path: Path) -> None:
    assert BrokerSettings(_env_file=None).broker_url == "redis://localhost:6379/1"
    dotenv = tmp_path / ".env"
    dotenv.write_text("CELERY_BROKER_URL=\n")
    assert BrokerSettings(_env_file=dotenv).broker_url == "redis://localhost:6379/1"


def test_broker_derivation_preserves_credentials_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://u:p@h:6380/3?socket_timeout=2")
    settings = BrokerSettings(_env_file=None)
    assert settings.broker_url == "redis://u:p@h:6380/1?socket_timeout=2"
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://h:6380/4")
    assert BrokerSettings(_env_file=None).broker_url == "redis://h:6380/4"
    monkeypatch.setenv("CELERY_BROKER_URL", "amqp://h/")
    with pytest.raises(ValidationError, match="celery_broker_url"):
        BrokerSettings(_env_file=None)


@pytest.mark.parametrize(
    "namespace", ["", "taskbot_test_1_ab12cd34:", "taskbot_test_1_ab12cd34:t3:"]
)
def test_valid_namespaces(namespace: str) -> None:
    assert BrokerSettings(_env_file=None, redis_namespace=namespace).redis_namespace == namespace


@pytest.mark.parametrize("namespace", ["x", "X:", "a:b:c:", "a" * 65 + ":", "a:b-:"])
def test_invalid_namespaces(namespace: str) -> None:
    with pytest.raises(ValidationError, match="redis_namespace"):
        BrokerSettings(_env_file=None, redis_namespace=namespace)


def test_env_file_override_is_read_at_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in (1, 2):
        dotenv = tmp_path / f"{index}.env"
        dotenv.write_text(f"REDIS_NAMESPACE=file{index}:\n")
        monkeypatch.setenv("TASKBOT_ENV_FILE", str(dotenv))
        assert BrokerSettings().redis_namespace == f"file{index}:"
        assert BrokerSettings(_env_file=None).redis_namespace == ""


def test_client_options_are_canonical_and_lazy() -> None:
    client = create_redis_client("redis://127.0.0.1:1/0", client_name="test-client")
    options = client.connection_pool.connection_kwargs
    assert options["socket_connect_timeout"] == 2
    assert options["socket_timeout"] == 5
    assert options["socket_keepalive"] is True
    assert options["health_check_interval"] == 30
    assert options["decode_responses"] is True
    assert options["client_name"] == "test-client"
    assert isinstance(options["retry"], Retry)
    assert options["retry"]._retries == 3
    assert isinstance(options["retry"]._backoff, ExponentialWithJitterBackoff)
    assert options["retry"]._backoff._base == 0.1
    assert options["retry"]._backoff._cap == 1
