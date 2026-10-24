import asyncio

from alembic import context
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from taskbot.infrastructure.settings.migration import MigrationSettings
from taskbot.infrastructure.sqlalchemy.tables import Base

MIGRATION_LOCK = 724062410


def migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        context.run_migrations()


async def run_online() -> None:
    # Alembic attributes are untyped; validate this override before building an engine.
    override = context.config.attributes.get("database_url")
    if override is not None and not isinstance(override, str):
        raise ValueError("database_url override must be a string")
    url = override or str(MigrationSettings().database_url)
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are unsupported; use a PostgreSQL connection")
asyncio.run(run_online())
