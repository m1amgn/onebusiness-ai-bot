import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from taskbot.domain.errors import DomainError
from taskbot.domain.ports.messaging import EventPublisher
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import TaskExtractor
from taskbot.entrypoints import seed as seed_entrypoint
from taskbot.entrypoints.seed import SeedPorts, SeedResult, build_seed_ports
from taskbot.entrypoints.seed import __main__ as seed_cli
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.seed import SeedSettings
from tests.processes import run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class CliResources:
    closed: list[str] = field(default_factory=list)
    fail_close: bool = False
    extractor: HeuristicExtractor = field(default_factory=HeuristicExtractor)

    async def aclose(self) -> None:
        self.closed.append("redis")
        if self.fail_close:
            raise RuntimeError("Redis close failed")

    async def dispose(self) -> None:
        self.closed.append("engine")


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> CliResources:
    monkeypatch.setenv("TASKBOT_ENV_FILE", "/dev/null")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://unused/test")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("REDIS_NAMESPACE", "taskbot_test_1_ab12cd34:t1:")
    monkeypatch.setenv("TASK_EXTRACTOR", "heuristic")
    owned = CliResources()
    monkeypatch.setattr(seed_cli, "create_engine_and_sessions", lambda url: (owned, object()))
    monkeypatch.setattr(seed_cli, "create_redis_client", lambda url: owned, raising=False)

    @asynccontextmanager
    async def open_extractor(
        settings: SeedSettings, *, deadline_seconds: float
    ) -> AsyncIterator[HeuristicExtractor]:
        assert deadline_seconds == 30
        try:
            yield owned.extractor
        finally:
            owned.closed.append("extractor")

    monkeypatch.setattr(seed_cli, "open_extractor", open_extractor, raising=False)
    return owned


async def successful_seed(
    ports: SeedPorts, telegram_user_id: int, text: str, message_id: int
) -> SeedResult:
    assert telegram_user_id == 1001
    assert message_id < 0
    return SeedResult(
        user_id=7,
        titles=(text,),
        token="synthetic-token",
        link="http://localhost:5173/#token=synthetic-token",
        duplicate=False,
        events=(),
    )


async def test_seed_cli_passes_the_opened_extractor_to_port_construction(
    resources: CliResources, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[TaskExtractor] = []

    def capture_build_seed_ports(
        settings: SeedSettings,
        uows: UnitOfWorkFactory,
        extractor: TaskExtractor,
        *,
        publisher: EventPublisher,
    ) -> SeedPorts:
        observed.append(extractor)
        return build_seed_ports(settings, uows, extractor, publisher=publisher)

    monkeypatch.setattr(seed_cli, "build_seed_ports", capture_build_seed_ports)
    monkeypatch.setattr(seed_cli, "seed", successful_seed)
    await seed_cli.run(1001, "Synthetic task")
    assert len(observed) == 1
    assert observed[0] is resources.extractor


async def test_seed_cli_prints_namespaced_internal_user_channel_and_closes_resources(
    resources: CliResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(seed_cli, "seed", successful_seed)
    await seed_cli.run(1001, "Synthetic task")
    assert resources.closed == ["extractor", "redis", "engine"]
    output = capsys.readouterr().out
    assert "Events channel: taskbot_test_1_ab12cd34:t1:events:user:7\n" in output
    assert "Token: synthetic-token\n" in output
    assert "Link: http://localhost:5173/#token=synthetic-token\n" in output


async def test_seed_cli_closes_resources_when_seed_raises(
    resources: CliResources, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_seed(*args: object) -> SeedResult:
        raise RuntimeError("Seed failed")

    monkeypatch.setattr(seed_cli, "seed", fail_seed)
    with pytest.raises(RuntimeError, match="Seed failed"):
        await seed_cli.run(1001, "Synthetic task")
    assert resources.closed == ["extractor", "redis", "engine"]


async def test_seed_cli_disposes_engine_when_redis_construction_fails(
    resources: CliResources, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_client(url: str) -> None:
        raise RuntimeError("Redis construction failed")

    monkeypatch.setattr(seed_cli, "create_redis_client", fail_client)
    with pytest.raises(RuntimeError, match="Redis construction failed"):
        await seed_cli.run(1001, "Synthetic task")
    assert resources.closed == ["engine"]


async def test_seed_cli_closes_resources_when_port_construction_fails(
    resources: CliResources, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_ports(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Port construction failed")

    monkeypatch.setattr(seed_cli, "build_seed_ports", fail_ports)
    with pytest.raises(RuntimeError, match="Port construction failed"):
        await seed_cli.run(1001, "Synthetic task")
    assert resources.closed == ["extractor", "redis", "engine"]


async def test_seed_cli_disposes_engine_even_if_redis_close_fails(
    resources: CliResources, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources.fail_close = True
    monkeypatch.setattr(seed_cli, "seed", successful_seed)
    with pytest.raises(RuntimeError, match="Redis close failed"):
        await seed_cli.run(1001, "Synthetic task")
    assert resources.closed == ["extractor", "redis", "engine"]


async def test_seed_cli_closes_redis_and_engine_when_extractor_construction_fails(
    resources: CliResources, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def fail_extractor(
        settings: SeedSettings, *, deadline_seconds: float
    ) -> AsyncIterator[HeuristicExtractor]:
        raise RuntimeError("Extractor construction failed")
        yield HeuristicExtractor()

    monkeypatch.setattr(seed_cli, "open_extractor", fail_extractor)
    with pytest.raises(RuntimeError, match="Extractor construction failed"):
        await seed_cli.run(1001, "Synthetic task")
    assert resources.closed == ["redis", "engine"]


@pytest.mark.parametrize("value", ["0", "-1", "9223372036854775808", "abc"])
async def test_seed_refuses_invalid_user_id_before_settings_or_database(value: str) -> None:
    result = await run_process(
        sys.executable,
        "-m",
        "taskbot.entrypoints.seed",
        value,
        cwd=BACKEND_ROOT,
        env=dict(os.environ, DATABASE_URL="invalid-to-prove-no-database-access"),
    )
    assert result.returncode == 2
    assert "positive 64-bit integer" in result.stderr
    assert "Traceback" not in result.stderr


async def test_seed_configuration_error_hides_invalid_secret_input() -> None:
    result = await run_process(
        sys.executable,
        "-m",
        "taskbot.entrypoints.seed",
        "1001",
        cwd=BACKEND_ROOT,
        env=dict(
            os.environ,
            TASKBOT_ENV_FILE="/dev/null",
            DATABASE_URL="https://test:synthetic-secret@invalid",
        ),
    )
    assert result.returncode == 2
    assert "Invalid configuration: DATABASE_URL" in result.stderr
    assert "synthetic-secret" not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("duplicate", [False, True])
async def test_keep_token_cli_prints_no_credentials_and_closes_resources(
    resources: CliResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    duplicate: bool,
) -> None:
    async def successful_keep_token(
        ports: SeedPorts, telegram_user_id: int, text: str, message_id: int
    ) -> seed_entrypoint.KeptTokenSeedResult:
        assert telegram_user_id == 1001
        assert message_id < 0
        return seed_entrypoint.KeptTokenSeedResult(
            user_id=7, titles=() if duplicate else (text,), duplicate=duplicate, events=()
        )

    monkeypatch.setattr(seed_cli, "seed_keep_token", successful_keep_token, raising=False)
    await seed_cli.run_keep_token(1001, "Synthetic task")
    assert resources.closed == ["extractor", "redis", "engine"]
    output = capsys.readouterr().out
    assert "Events channel: taskbot_test_1_ab12cd34:t1:events:user:7\n" in output
    assert "Token unchanged; open dashboards stay signed in.\n" in output
    assert ("Message already seeded.\n" in output) == duplicate
    assert ("Title: Synthetic task\n" in output) != duplicate
    assert "Token:" not in output
    assert "Link:" not in output
    assert "#token=" not in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--keep-token", "1001", "Synthetic", "task"],
        ["1001", "--keep-token", "Synthetic", "task"],
    ],
)
def test_keep_token_flag_dispatches_before_or_after_user_id(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    calls: list[tuple[int, str]] = []

    async def capture_run(telegram_user_id: int, text: str) -> None:
        calls.append((telegram_user_id, text))

    monkeypatch.setattr(seed_cli, "run_keep_token", capture_run, raising=False)
    monkeypatch.setattr(sys, "argv", ["seed", *arguments])
    seed_cli.main()
    assert calls == [(1001, "Synthetic task")]


def test_keep_token_cli_refusal_exits_two_and_closes_resources(
    resources: CliResources,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    message = "This user has no dashboard link yet; run without --keep-token first."

    async def refuse_seed(*args: object) -> seed_entrypoint.KeptTokenSeedResult:
        raise DomainError(message)

    monkeypatch.setattr(seed_cli, "seed_keep_token", refuse_seed, raising=False)
    monkeypatch.setattr(sys, "argv", ["seed", "--keep-token", "1001"])
    with pytest.raises(SystemExit) as exited:
        seed_cli.main()
    assert exited.value.code == 2
    assert resources.closed == ["extractor", "redis", "engine"]
    output = capsys.readouterr()
    assert message in output.err
    assert output.out == ""
    assert "Traceback" not in output.err
