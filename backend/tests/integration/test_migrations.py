import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg
from alembic import command

from tests.databases import make_alembic_config, raw_url
from tests.databases import test_database as database_context
from tests.integration.settings import InfraTestSettings
from tests.processes import run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Literal normalized PostgreSQL constraints, independent of runtime metadata.
EXPECTED_CHECKS = {
    "ck_inbound_messages_extractor": "CHECKextractor=ANYARRAY['heuristic','claude'][]",
    "ck_inbound_messages_kind": "CHECKkind=ANYARRAY['text','voice'][]",
    "ck_tasks_description_length": "CHECKchar_lengthdescription<=4096",
    "ck_tasks_position": 'CHECK"position">=0AND"position"<=9',
    "ck_tasks_status": "CHECKstatus=ANYARRAY['pending','in_progress','completed'][]",
    "ck_tasks_title_length": "CHECKchar_lengthtitle>=1ANDchar_lengthtitle<=120",
    "ck_voice_jobs_attempts": "CHECKattempts>=0ANDattempts<=5",
    "ck_voice_jobs_deferred_terminal": (
        "CHECKnotify_not_beforeISNULLORstatus=ANYARRAY['done','failed'][]"
    ),
    "ck_voice_jobs_done_has_message": "CHECKstatus<>'done'ORinbound_message_idISNOTNULL",
    "ck_voice_jobs_failed_has_kind": "CHECKstatus<>'failed'ORfailure_kindISNOTNULL",
    "ck_voice_jobs_failure_kind": (
        "CHECKfailure_kind=ANYARRAY['too_large','unsupported_audio','empty_transcript',"
        "'transcription_timeout','provider_unavailable','transient_exhausted','internal','stuck'][]"
    ),
    "ck_voice_jobs_notification_outcome": (
        "CHECKnotification_outcome=ANYARRAY['edited','replied','blocked','given_up'][]"
    ),
    "ck_voice_jobs_notified_terminal": "CHECKnotified_atISNULLORstatus=ANYARRAY['done','failed'][]",
    "ck_voice_jobs_notify_attempts": "CHECKnotify_attempts>=0ANDnotify_attempts<=5",
    "ck_voice_jobs_provider": "CHECKprovider=ANYARRAY['fake','openai','whisper_server'][]",
    "ck_voice_jobs_status": "CHECKstatus=ANYARRAY['queued','processing','done','failed'][]",
}


def normalize_sql(value: str) -> str:
    return re.sub(r"[\s()]+", "", value).replace("::charactervarying", "").replace("::text", "")


async def development_schema() -> list[str]:
    connection = await asyncpg.connect(raw_url(str(InfraTestSettings().database_url)))
    try:
        return [
            str(r[0])
            for r in await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
        ]
    finally:
        await connection.close()


async def test_migration_schema_drift_and_downgrade() -> None:
    before = await development_schema()
    async with database_context() as url:
        config = make_alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "head")
        await asyncio.to_thread(command.check, config)
        connection = await asyncpg.connect(raw_url(url))
        try:
            tables = await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
            assert [r[0] for r in tables] == [
                "alembic_version",
                "inbound_messages",
                "tasks",
                "users",
                "voice_jobs",
            ]
            constraints = await connection.fetch("""
                SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                WHERE contype='c' AND connamespace='public'::regnamespace ORDER BY conname
            """)
            checks = {str(r[0]): str(r[1]) for r in constraints}
            assert set(checks) == {
                "ck_inbound_messages_kind",
                "ck_inbound_messages_extractor",
                "ck_tasks_status",
                "ck_tasks_title_length",
                "ck_tasks_description_length",
                "ck_tasks_position",
                "ck_voice_jobs_status",
                "ck_voice_jobs_failure_kind",
                "ck_voice_jobs_provider",
                "ck_voice_jobs_notification_outcome",
                "ck_voice_jobs_attempts",
                "ck_voice_jobs_notify_attempts",
                "ck_voice_jobs_done_has_message",
                "ck_voice_jobs_failed_has_kind",
                "ck_voice_jobs_notified_terminal",
                "ck_voice_jobs_deferred_terminal",
            }
            normalized = {name: normalize_sql(definition) for name, definition in checks.items()}
            assert normalized == EXPECTED_CHECKS
            lengths = await connection.fetch("""
                SELECT character_maximum_length FROM information_schema.columns
                WHERE table_schema='public' AND data_type='character varying'
                    AND table_name <> 'alembic_version'
            """)
            assert len(lengths) == 7
            assert all(r[0] == 32 for r in lengths)
            predicate = await connection.fetchval("""
                SELECT pg_get_expr(indpred,indrelid) FROM pg_index
                WHERE indexrelid='ix_voice_jobs_pending_last_enqueued'::regclass
            """)
            expected = "status=ANYARRAY['queued','processing'][]ORnotified_atISNULL"
            assert normalize_sql(predicate) == expected
            definition = await connection.fetchval("""
                SELECT indexdef FROM pg_indexes
                WHERE indexname='ix_voice_jobs_pending_last_enqueued'
            """)
            assert normalize_sql(definition) == (
                "CREATEINDEXix_voice_jobs_pending_last_enqueuedONpublic.voice_jobs"
                "USINGbtreelast_enqueued_atWHERE" + expected
            )
        finally:
            await connection.close()
        await asyncio.to_thread(command.downgrade, config, "base")
        await asyncio.to_thread(command.upgrade, config, "head")
    assert await development_schema() == before


async def test_two_migrators_serialize_on_an_empty_database() -> None:
    before = await development_schema()
    async with database_context() as url:

        async def upgrade() -> None:
            process = await run_process(
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "head",
                cwd=BACKEND_ROOT,
                env=os.environ | {"DATABASE_URL": url},
                timeout_seconds=30,
            )
            assert process.returncode == 0, process.stdout + process.stderr

        async with asyncio.TaskGroup() as group:
            group.create_task(upgrade())
            group.create_task(upgrade())
        connection = await asyncpg.connect(raw_url(url))
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0002"
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
                )
                == 5
            )
        finally:
            await connection.close()
    assert await development_schema() == before


async def test_description_limit_downgrade_restores_2000_then_upgrade_restores_4096() -> None:
    before = await development_schema()
    async with database_context() as url:
        config = make_alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "head")
        connection = await asyncpg.connect(raw_url(url))
        try:
            await asyncio.to_thread(command.downgrade, config, "0001")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0001"
            definition = await connection.fetchval("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conname='ck_tasks_description_length'
                    AND conrelid='public.tasks'::regclass
            """)
            assert normalize_sql(definition) == "CHECKchar_lengthdescription<=2000"
            await asyncio.to_thread(command.upgrade, config, "head")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == "0002"
            definition = await connection.fetchval("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conname='ck_tasks_description_length'
                    AND conrelid='public.tasks'::regclass
            """)
            assert normalize_sql(definition) == "CHECKchar_lengthdescription<=4096"
        finally:
            await connection.close()
    assert await development_schema() == before
