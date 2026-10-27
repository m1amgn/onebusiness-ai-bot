import asyncio
import logging
import os
import signal
from collections.abc import Coroutine
from contextlib import suppress

from taskbot.entrypoints.worker.container import WorkerContainer
from taskbot.entrypoints.worker.container import build_container as build_container
from taskbot.entrypoints.worker.settings import load_worker_settings

logger = logging.getLogger(__name__)
_loop: asyncio.AbstractEventLoop | None = None
_container: WorkerContainer | None = None


def current_loop() -> asyncio.AbstractEventLoop:
    if _loop is None:
        raise RuntimeError("Worker runtime is only available in a pool child")
    return _loop


def current_container() -> WorkerContainer:
    if _container is None:
        raise RuntimeError("Worker runtime is only available in a pool child")
    return _container


def close_child() -> None:
    global _loop, _container
    loop, container = _loop, _container
    _loop, _container = None, None
    if loop is None:
        return
    try:
        if container is not None:
            loop.run_until_complete(container.aclose())
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


def _stop_main() -> None:
    try:
        recorded = int(os.environ.get("TASKBOT_WORKER_MAIN_PID", "0"))
        parent = os.getppid()
        if parent > 1 and parent == recorded:
            os.kill(parent, signal.SIGTERM)
    except BaseException:
        # Shutdown cannot depend on a live parent or a working logger.
        pass


def start_child() -> None:
    global _loop, _container
    try:
        settings = load_worker_settings()
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _container = WorkerContainer(settings)
        _loop.run_until_complete(build_container(_container))
    except BaseException as error:
        try:
            with suppress(BaseException):
                close_child()
            with suppress(BaseException):
                logger.error("Worker child runtime failed (%s)", type(error).__name__)
            _stop_main()
        finally:
            os._exit(70)


def run_async[T](coroutine: Coroutine[object, object, T]) -> T:
    try:
        loop = current_loop()
    except BaseException:
        coroutine.close()
        raise
    task = loop.create_task(coroutine)
    try:
        return loop.run_until_complete(task)
    except BaseException:
        task.cancel()
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        raise
