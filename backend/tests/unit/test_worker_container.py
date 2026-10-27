import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from pydantic import PostgresDsn, SecretStr
from sqlalchemy.pool import AsyncAdaptedQueuePool

from taskbot.domain.errors import NotificationError, VoiceFileUnavailableError
from taskbot.domain.ports.services import TaskExtractor
from taskbot.domain.ports.transcription import Transcriber
from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.entrypoints.worker import container as container_module
from taskbot.entrypoints.worker.container import WorkerContainer, build_container
from taskbot.infrastructure.extraction.heuristic import HeuristicExtractor
from taskbot.infrastructure.settings.worker import TranscribeWorkerSettings, WorkerSettings
from taskbot.infrastructure.transcription.fake import FakeTranscriber
from tests.processes import COLD_START_TIMEOUT_SECONDS, run_process

DATABASE_URL = PostgresDsn(
    "postgresql+asyncpg://taskbot:taskbot@127.0.0.1:1/taskbot_test_0_00000000"
)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WHISPER_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)


def transcribe_settings() -> TranscribeWorkerSettings:
    return TranscribeWorkerSettings(
        worker_role="transcribe",
        database_url=DATABASE_URL,
        transcription_provider=TranscriptionProvider.FAKE,
        task_extractor="heuristic",
        _env_file=None,
    )


async def test_role_containers_own_only_their_resources_and_reuse_them() -> None:
    transcribe = WorkerContainer(transcribe_settings())
    notify = WorkerContainer(
        WorkerSettings(worker_role="notify", database_url=DATABASE_URL, _env_file=None)
    )
    try:
        await build_container(transcribe)
        await build_container(notify)
        assert transcribe.transcribe is not None
        assert transcribe.notify is None
        assert transcribe.recover is None
        assert transcribe.extractor is not None
        assert transcribe.transcriber is not None
        assert transcribe.files is not None
        assert transcribe.notifier is not None
        assert transcribe.publisher is not None
        assert notify.notify is not None
        assert notify.recover is not None
        assert notify.transcribe is None
        assert notify.extractor is None
        assert notify.transcriber is None
        assert notify.files is None
        assert notify.publisher is None
        assert notify.notifier is not None
        for container, role in ((transcribe, "transcribe"), (notify, "notify")):
            assert container.redis is not None
            assert container.redis.connection_pool.connection_kwargs["client_name"] == (
                f"worker-{role}"
            )
            assert container.engine is not None
            assert isinstance(container.engine.pool, AsyncAdaptedQueuePool)
            assert container.engine.pool.size() == 5
            assert container.uows() is not container.uows()
        assert transcribe.transcribe._extractor is transcribe.extractor
        assert transcribe.transcribe._transcriber is transcribe.transcriber
        assert transcribe.transcribe._files is transcribe.files
        assert transcribe.transcribe._notifier is transcribe.notifier
        assert transcribe.transcribe._clock is transcribe.clock
    finally:
        await transcribe.aclose()
        await notify.aclose()


async def test_no_token_adapters_raise_domain_errors_without_connecting() -> None:
    container = WorkerContainer(transcribe_settings())
    try:
        await build_container(container)
        assert container.notifier is not None
        assert container.files is not None
        with pytest.raises(NotificationError):
            await container.notifier.send_reply(1, "private text")
        with pytest.raises(NotificationError):
            await container.notifier.edit_message(1, 2, "private text")
        with pytest.raises(VoiceFileUnavailableError):
            await container.files.fetch("private file", max_bytes=20)
    finally:
        await container.aclose()


async def test_keyless_workers_do_not_import_the_telegram_sdk() -> None:
    result = await run_process(
        sys.executable,
        "-c",
        """
import asyncio
import sys
from taskbot.entrypoints.worker.container import WorkerContainer, build_container
from taskbot.infrastructure.settings.worker import TranscribeWorkerSettings, WorkerSettings

async def check():
    for role, model in (("notify", WorkerSettings), ("transcribe", TranscribeWorkerSettings)):
        settings = model(
            worker_role=role,
            database_url="postgresql+asyncpg://test:test@127.0.0.1:1/taskbot_test_unused",
            **({"transcription_provider": "fake", "task_extractor": "heuristic"}
               if role == "transcribe" else {}),
            _env_file=None,
        )
        container = WorkerContainer(settings)
        try:
            await build_container(container)
            assert "aiogram" not in sys.modules, "keyless worker imported Telegram SDK"
        finally:
            await container.aclose()

asyncio.run(check())
""",
        cwd=BACKEND_ROOT,
        timeout_seconds=COLD_START_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_factory_lifetimes_and_interrupt_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def extractor(
        settings: object, *, deadline_seconds: float, interrupts: tuple[type[BaseException], ...]
    ) -> AsyncIterator[TaskExtractor]:
        assert deadline_seconds == 30
        assert interrupts == (SoftTimeLimitExceeded,)
        events.append("extractor open")
        try:
            yield HeuristicExtractor()
        finally:
            events.append("extractor close")

    @asynccontextmanager
    async def transcriber(settings: object) -> AsyncIterator[Transcriber]:
        events.append("transcriber open")
        try:
            yield FakeTranscriber()
        finally:
            events.append("transcriber close")

    monkeypatch.setattr(container_module, "open_extractor", extractor)
    monkeypatch.setattr(container_module, "open_transcriber", transcriber)
    container = WorkerContainer(transcribe_settings())
    await build_container(container)
    assert events == ["extractor open", "transcriber open"]
    await container.aclose()
    assert events == ["extractor open", "transcriber open", "transcriber close", "extractor close"]


async def test_partial_build_registers_and_closes_clients_immediately(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[str] = []

    class Session:
        async def close(self) -> None:
            events.append("telegram close")

    @asynccontextmanager
    async def extractor(
        settings: object, *, deadline_seconds: float, interrupts: tuple[type[BaseException], ...]
    ) -> AsyncIterator[TaskExtractor]:
        events.append("extractor open")
        try:
            yield HeuristicExtractor()
        finally:
            events.append("extractor close")
            raise RuntimeError("private cleanup data")

    @asynccontextmanager
    async def transcriber(settings: object) -> AsyncIterator[Transcriber]:
        raise RuntimeError("private constructor data")
        yield FakeTranscriber()

    monkeypatch.setattr("aiogram.Bot", lambda token: SimpleNamespace(session=Session()))
    monkeypatch.setattr(container_module, "open_extractor", extractor)
    monkeypatch.setattr(container_module, "open_transcriber", transcriber)
    settings = transcribe_settings().model_copy(
        update={"telegram_bot_token": SecretStr("123456789:" + "a" * 35)}
    )
    container = WorkerContainer(settings)
    with pytest.raises(RuntimeError, match="private constructor data"):
        await build_container(container)
    assert container.engine is not None
    assert container.redis is not None
    assert container.job_queue is not None
    close_queue, close_redis_client, dispose = (
        container.job_queue.close,
        container.redis.aclose,
        container.engine.dispose,
    )

    def close_job_queue() -> None:
        events.append("queue close")
        close_queue()

    async def close_redis() -> None:
        events.append("redis close")
        await close_redis_client()

    async def dispose_engine(engine: object) -> None:
        events.append("engine close")
        await dispose()

    monkeypatch.setattr(container.job_queue, "close", close_job_queue)
    monkeypatch.setattr(container.redis, "aclose", close_redis)
    monkeypatch.setattr(type(container.engine), "dispose", dispose_engine)
    await container.aclose()
    assert events == [
        "extractor open",
        "extractor close",
        "telegram close",
        "queue close",
        "redis close",
        "engine close",
    ]
    assert "private" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
