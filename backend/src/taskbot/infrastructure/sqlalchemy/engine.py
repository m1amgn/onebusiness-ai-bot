from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine_and_sessions(
    database_url: str, *, pool_size: int = 5
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
