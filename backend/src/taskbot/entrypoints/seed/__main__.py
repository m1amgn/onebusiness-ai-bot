import argparse
import asyncio
import logging
import time
from contextlib import AsyncExitStack

from taskbot.domain.errors import DomainError
from taskbot.entrypoints.seed import build_seed_ports, seed
from taskbot.entrypoints.settings import load_settings
from taskbot.infrastructure.redis.client import create_redis_client, user_channel
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

SAMPLE_TEXT = "Call home and ask about the weekend."


def positive_int64(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("User id must be a positive 64-bit integer") from error
    if not 1 <= result <= 2**63 - 1:
        raise argparse.ArgumentTypeError("User id must be a positive 64-bit integer")
    return result


async def run(telegram_user_id: int, text: str) -> None:
    settings = load_settings(SeedSettings)
    logging.basicConfig(level=settings.log_level)
    async with AsyncExitStack() as resources:
        engine, sessions = create_engine_and_sessions(str(settings.database_url))
        resources.push_async_callback(engine.dispose)
        client = create_redis_client(str(settings.redis_url))
        resources.push_async_callback(client.aclose)

        def uows() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(sessions)

        result = await seed(
            build_seed_ports(
                settings, uows, publisher=RedisEventPublisher(client, settings.redis_namespace)
            ),
            telegram_user_id,
            text,
            -(time.time_ns() // 1000),
        )
        if result.duplicate:
            print("Message already seeded.")
        for title in result.titles:
            print(f"Title: {title}")
        print(f"Token: {result.token}")
        print(f"Link: {result.link}")
        print(f"Events channel: {user_channel(settings.redis_namespace, result.user_id)}")
        print("Older dashboard links stop working after every seed run.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create development tasks and a dashboard token.")
    parser.add_argument("telegram_user_id", type=positive_int64)
    parser.add_argument("text", nargs="*")
    args = parser.parse_args()
    text = " ".join(args.text) if args.text else SAMPLE_TEXT
    try:
        asyncio.run(run(args.telegram_user_id, text))
    except DomainError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
