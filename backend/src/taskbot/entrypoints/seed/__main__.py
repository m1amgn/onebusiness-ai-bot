import argparse
import asyncio
import logging
import time

from taskbot.domain.errors import DomainError
from taskbot.entrypoints.seed import build_seed_ports, seed
from taskbot.infrastructure.settings.seed import SeedSettings
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

SAMPLE_TEXT = "Call home and ask about the weekend."


async def run(telegram_user_id: int, text: str) -> None:
    settings = SeedSettings()
    logging.basicConfig(level=settings.log_level)
    engine, sessions = create_engine_and_sessions(str(settings.database_url))

    def uows() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    try:
        result = await seed(
            build_seed_ports(settings, uows), telegram_user_id, text, -(time.time_ns() // 1000)
        )
        # S3 binds publish_events here; all returned events already belong to committed work.
        logging.getLogger(__name__).info(
            "%d events not published (no publisher until S3)", len(result.events)
        )
        if result.duplicate:
            print("Message already seeded.")
        for title in result.titles:
            print(f"Title: {title}")
        print(f"Token: {result.token}")
        print(f"Link: {result.link}")
        print("Older dashboard links stop working after every seed run.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create development tasks and a dashboard token.")
    parser.add_argument("telegram_user_id", type=int)
    parser.add_argument("text", nargs="*")
    args = parser.parse_args()
    text = " ".join(args.text) if args.text else SAMPLE_TEXT
    try:
        asyncio.run(run(args.telegram_user_id, text))
    except DomainError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
