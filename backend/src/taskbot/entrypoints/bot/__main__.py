import asyncio
import logging
import signal
import sys
from contextlib import AsyncExitStack, suppress
from urllib.parse import urlsplit

from aiogram import Bot, Dispatcher
from aiogram.exceptions import AiogramError, TelegramUnauthorizedError
from aiohttp import ClientError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from taskbot.domain.voice_jobs import TranscriptionProvider
from taskbot.entrypoints.bot import BotPorts, build_bot_ports
from taskbot.entrypoints.bot.handlers import create_dispatcher
from taskbot.entrypoints.bot.runtime import InFlightUpdates, PollingWatch, TokenRedactionFilter
from taskbot.entrypoints.settings import load_settings
from taskbot.infrastructure.celery.app import create_celery_app
from taskbot.infrastructure.celery.job_queue import CeleryJobQueue
from taskbot.infrastructure.extraction.factory import open_extractor
from taskbot.infrastructure.redis.bot_lock import HeartbeatTask, LockKeeper, PollingLock
from taskbot.infrastructure.redis.client import create_redis_client
from taskbot.infrastructure.redis.events import RedisEventPublisher
from taskbot.infrastructure.settings.bot import BotSettings
from taskbot.infrastructure.settings.transcription import has_openai_key
from taskbot.infrastructure.sqlalchemy.engine import create_engine_and_sessions
from taskbot.infrastructure.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

BOT_EXTRACTION_DEADLINE_SECONDS = 8
BOT_CONCURRENCY = 10
DRAIN_SECONDS = 15
logger = logging.getLogger(__name__)


class StopController:
    def __init__(self) -> None:
        self.task = asyncio.current_task()
        self.dispatcher: Dispatcher | None = None
        self.phase = "startup"
        self.stopped = False
        self.skip_drain = asyncio.Event()
        self.exit_code = 0
        self._stop_tasks: set[asyncio.Task[None]] = set()

    def signal(self) -> None:
        if self.stopped:
            self.skip_drain.set()
        self.request_stop()

    def fail(self, message: str) -> None:
        if self.exit_code == 0:
            print(message, file=sys.stderr)
        self.exit_code = 1
        self.request_stop()

    def request_stop(self) -> None:
        self.stopped = True
        if self.phase == "startup":
            if self.task is not None and self.task is not asyncio.current_task():
                self.task.cancel()
        elif self.phase == "polling":
            task = asyncio.create_task(self._stop_polling())
            self._stop_tasks.add(task)
            task.add_done_callback(self._stop_tasks.discard)

    async def _stop_polling(self) -> None:
        if self.dispatcher is not None:
            with suppress(RuntimeError):
                await self.dispatcher.stop_polling()

    async def close(self) -> None:
        await asyncio.gather(*self._stop_tasks, return_exceptions=True)


def _install_signals(control: StopController) -> None:
    loop = asyncio.get_running_loop()
    for number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(number, control.signal)


async def _stop_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _check_database(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1 FROM inbound_messages LIMIT 0"))
        return True
    except Exception:
        return False


def _supervise(
    task: asyncio.Task[None],
    control: StopController,
    message: str,
) -> None:
    if task.cancelled() or control.phase == "closing":
        return
    error = task.exception()
    if error is not None:
        logger.error("Bot background task failed (%s)", type(error).__name__)
    control.fail(message)


async def _poll(
    settings: BotSettings,
    resources: AsyncExitStack,
    control: StopController,
    lock: PollingLock,
    keeper: LockKeeper,
    client: Redis,
) -> None:
    engine, sessions = create_engine_and_sessions(str(settings.database_url))
    resources.push_async_callback(engine.dispose)
    if not await _check_database(engine):
        control.fail("Database is unavailable or not migrated")
        return
    app = create_celery_app(broker_url=settings.broker_url, namespace=settings.redis_namespace)
    resources.callback(app.close)
    queue = CeleryJobQueue(app)
    resources.callback(queue.close)
    extractor = await resources.enter_async_context(
        open_extractor(settings, deadline_seconds=BOT_EXTRACTION_DEADLINE_SECONDS)
    )
    bot = Bot(settings.telegram_bot_token.get_secret_value())
    resources.push_async_callback(bot.session.close)
    try:
        await bot.me()
        await bot.delete_webhook(drop_pending_updates=False)
    except TelegramUnauthorizedError:
        control.fail("Telegram rejected TELEGRAM_BOT_TOKEN.")
        return
    except AiogramError, TimeoutError, ClientError:
        control.fail("Cannot reach api.telegram.org.")
        return

    def uows() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    from taskbot.infrastructure.telegram.notifier import AiogramUserNotifier

    ports = build_bot_ports(
        settings,
        uows,
        extractor,
        AiogramUserNotifier(bot),
        queue,
        RedisEventPublisher(client, settings.redis_namespace),
    )
    await _run_dispatcher(resources, control, lock, keeper, bot, ports)


async def _run_dispatcher(
    resources: AsyncExitStack,
    control: StopController,
    lock: PollingLock,
    keeper: LockKeeper,
    bot: Bot,
    ports: BotPorts,
) -> None:
    in_flight = InFlightUpdates()
    dispatcher = create_dispatcher(ports, in_flight)
    control.dispatcher = dispatcher
    watch = PollingWatch()
    bot.session.middleware(watch)

    async def before_polling() -> None:
        if keeper.lost or control.stopped:
            control.fail("The polling lock was lost.")
            raise asyncio.CancelledError
        try:
            await lock.renew()
        except RedisError, TimeoutError:
            control.fail("The polling lock was lost.")
            raise asyncio.CancelledError from None
        watch.start()
        control.phase = "polling"

    dispatcher.startup.register(before_polling)
    heartbeat = asyncio.create_task(
        HeartbeatTask(lock, keeper, healthy=watch.healthy, failed=watch.failed).run()
    )
    resources.push_async_callback(_stop_task, heartbeat)

    def heartbeat_done(task: asyncio.Task[None]) -> None:
        message = "The bot heartbeat stopped."
        if keeper.lost:
            message = "The polling lock was lost."
        elif watch.failed():
            message = f"Telegram polling has failed for 60 s ({watch.last_error})."
        _supervise(task, control, message)

    heartbeat.add_done_callback(heartbeat_done)
    try:
        await dispatcher.start_polling(
            bot,
            handle_signals=False,
            close_bot_session=False,
            tasks_concurrency_limit=BOT_CONCURRENCY,
        )
    finally:
        control.phase = "draining"
        await in_flight.drain(DRAIN_SECONDS, skip=control.skip_drain)


async def _serve(settings: BotSettings, resources: AsyncExitStack, control: StopController) -> None:
    client = create_redis_client(str(settings.redis_url), client_name="bot")
    resources.push_async_callback(client.aclose)
    lock = PollingLock(client, settings.redis_namespace)
    try:
        acquired = await lock.acquire()
        if not acquired:
            remaining = await lock.remaining_seconds()
            control.fail(
                f"Another bot instance holds the polling lock {lock.key}; "
                f"it expires in {remaining} s."
            )
            return
    except RedisError, TimeoutError:
        address = urlsplit(str(settings.redis_url))
        control.fail(f"Redis is unavailable at {address.hostname}:{address.port or 6379}")
        return
    resources.push_async_callback(lock.release)
    keeper = LockKeeper(lock)
    keeper_task = asyncio.create_task(keeper.run())
    resources.push_async_callback(_stop_task, keeper_task)
    keeper_task.add_done_callback(
        lambda task: _supervise(task, control, "The polling lock was lost.")
    )
    await _poll(settings, resources, control, lock, keeper, client)


async def run(settings: BotSettings | None = None) -> int:
    if settings is None:
        settings = load_settings(BotSettings)
    logging.basicConfig(level=settings.log_level)
    redaction = TokenRedactionFilter(settings.telegram_bot_token.get_secret_value())
    handlers = list(logging.getLogger().handlers)
    for handler in handlers:
        handler.addFilter(redaction)
    if settings.transcription_provider == TranscriptionProvider.OPENAI and not has_openai_key(
        settings
    ):
        logger.warning("OpenAI transcription is unavailable: missing API key")
    control = StopController()
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    _install_signals(control)
    try:
        async with AsyncExitStack() as resources:
            try:
                await _serve(settings, resources, control)
            finally:
                control.phase = "closing"
    except asyncio.CancelledError:
        if not control.stopped:
            raise
    except Exception as error:
        control.fail(f"Bot stopped ({type(error).__name__}).")
    finally:
        control.phase = "closing"
        await control.close()
        loop = asyncio.get_running_loop()
        for number, old_handler in previous.items():
            loop.remove_signal_handler(number)
            signal.signal(number, old_handler)
        for handler in handlers:
            handler.removeFilter(redaction)
    return control.exit_code


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
