from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions


async def test_sql_engine_hides_bound_parameters() -> None:
    engine, _ = create_engine_and_sessions(
        "postgresql+asyncpg://test:test@127.0.0.1:1/taskbot_test_unused"
    )
    try:
        assert engine.sync_engine.hide_parameters is True
    finally:
        await engine.dispose()
