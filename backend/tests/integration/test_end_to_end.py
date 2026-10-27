import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import PostgresDsn, RedisDsn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.voice_jobs import NotificationOutcome, TranscriptionProvider, VoiceJobStatus
from taskbot.entrypoints.api.app import create_app
from taskbot.entrypoints.api.container import open_api_services
from taskbot.entrypoints.bot import __main__ as bot_cli
from taskbot.infrastructure.settings.api import ApiSettings
from taskbot.infrastructure.sqlalchemy.tables import VoiceJobRow
from taskbot.infrastructure.transcription.fake import DEMO_TRANSCRIPT
from tests.api_server import api_server, assert_stream_headers, read_frame
from tests.bot import message_update
from tests.bot_runtime import configure_bot
from tests.processes import COLD_START_TIMEOUT_SECONDS
from tests.redis_sessions import RedisSession
from tests.s5 import HOOKS, wait_job
from tests.workers import running_worker, wait_records


async def created_source_texts(lines: AsyncIterator[str]) -> set[str]:
    sources: set[str] = set()
    while len(sources) < 2:
        frame = await read_frame(lines, timeout_seconds=COLD_START_TIMEOUT_SECONDS)
        assert frame is not None
        if frame[0].startswith(":"):
            continue
        assert len(frame) == 1
        assert frame[0].startswith("data: ")
        event = json.loads(frame[0].removeprefix("data: "))
        assert event["type"] == "task.created"
        source = event["task"]["source_text"]
        assert isinstance(source, str)
        sources.add(source)
    return sources


async def assert_voice_was_notified(
    uows: UnitOfWorkFactory,
    sessions: async_sessionmaker[AsyncSession],
    receipts: Path,
) -> None:
    async with sessions() as session:
        job_id = (await session.execute(select(VoiceJobRow.id))).scalar_one()
    final = await wait_job(
        uows,
        job_id,
        lambda row: row.notified_at is not None,
        timeout_seconds=COLD_START_TIMEOUT_SECONDS,
    )
    assert final.status == VoiceJobStatus.DONE
    assert final.provider == TranscriptionProvider.FAKE
    assert final.notification_outcome == NotificationOutcome.EDITED
    notices = await wait_records(
        receipts, "notification", timeout_seconds=COLD_START_TIMEOUT_SECONDS
    )
    assert len(notices) == 1
    assert str(notices[0]["text"]).startswith("Task created: [demo transcript]")
    assert str(notices[0]["text"]).endswith(" (demo mode)")


async def test_bot_text_and_voice_reach_tasks_notifications_and_sse(
    postgres_uows: UnitOfWorkFactory,
    sql_sessions: async_sessionmaker[AsyncSession],
    database_url: str,
    redis_session: RedisSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both harnesses disable dotenv and inherited provider credentials.
    harness = configure_bot(database_url, redis_session, monkeypatch)
    namespace = harness.settings.redis_namespace
    receipts = tmp_path / "receipts.jsonl"
    env = {"TASKBOT_TEST_S5_RECEIPTS": str(receipts)}
    settings = ApiSettings(
        _env_file=None,
        database_url=PostgresDsn(database_url),
        redis_url=RedisDsn(redis_session.redis_url),
        redis_namespace=namespace,
    )
    harness.session.updates = [
        message_update(1, "/dashboard").model_dump(mode="json", exclude_none=True)
    ]
    sent = asyncio.Event()
    harness.session.request_started["sendMessage"] = sent
    async with (
        running_worker(
            "notify", namespace, log_dir=tmp_path, database_url=database_url, env=env, include=HOOKS
        ),
        running_worker(
            "transcribe",
            namespace,
            log_dir=tmp_path,
            database_url=database_url,
            env=env,
            include=HOOKS,
        ),
        open_api_services(settings) as services,
        api_server(create_app(services=services)) as url,
        httpx.AsyncClient(base_url=url, timeout=COLD_START_TIMEOUT_SECONDS) as client,
    ):
        bot_task = asyncio.create_task(bot_cli.run(harness.settings))
        try:
            async with asyncio.timeout(COLD_START_TIMEOUT_SECONDS):
                await sent.wait()
                dashboard = str(
                    next(
                        payload["text"]
                        for name, payload in zip(
                            harness.session.methods, harness.session.payloads, strict=True
                        )
                        if name == "sendMessage"
                    )
                )
                link = dashboard.splitlines()[0].removeprefix("Your task board: ")
                token = parse_qs(urlsplit(link).fragment)["token"][0]
                headers = {"Authorization": f"Bearer {token}"}
                ticket = await client.post("/api/realtime/tickets", headers=headers)
                assert ticket.status_code == 201
                async with client.stream(
                    "GET", "/api/events", params={"ticket": ticket.json()["ticket"]}
                ) as response:
                    assert_stream_headers(response)
                    lines = response.aiter_lines()
                    assert await read_frame(lines, COLD_START_TIMEOUT_SECONDS) == (": connected",)
                    harness.session.updates = [
                        update.model_dump(mode="json", exclude_none=True)
                        for update in (
                            message_update(2, "Buy milk"),
                            message_update(3, voice=True),
                        )
                    ]
                    assert await created_source_texts(lines) == {"Buy milk", DEMO_TRANSCRIPT}
                    assert any(
                        name == "sendMessage" and payload.get("text") == "Task created: Buy milk"
                        for name, payload in zip(
                            harness.session.methods, harness.session.payloads, strict=True
                        )
                    )
                    await assert_voice_was_notified(postgres_uows, sql_sessions, receipts)
                    tasks = await client.get("/api/tasks", headers=headers)
                    assert tasks.status_code == 200
                    assert {item["source_text"] for item in tasks.json()["items"]} == {
                        "Buy milk",
                        DEMO_TRANSCRIPT,
                    }
        finally:
            assert harness.session.control is not None
            harness.session.control.signal()
            async with asyncio.timeout(COLD_START_TIMEOUT_SECONDS):
                assert await bot_task == 0
