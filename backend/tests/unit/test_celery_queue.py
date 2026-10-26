import asyncio
import socket
import threading
from typing import cast
from unittest.mock import Mock, PropertyMock

import pytest
from celery import Celery
from kombu.exceptions import OperationalError

from taskbot.domain.errors import EnqueueError
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.domain.ports.messaging import JobQueue
from taskbot.infrastructure.celery.app import create_celery_app
from taskbot.infrastructure.celery.job_queue import CeleryJobQueue


@pytest.mark.parametrize("namespace", ["", "taskbot_test_1_ab12cd34:t1:"])
def test_factory_has_exact_shared_configuration_without_connecting(
    namespace: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_connection(*args: object, **kwargs: object) -> None:
        pytest.fail("constructing a Celery application must not connect")

    monkeypatch.setattr(socket.socket, "connect", refuse_connection)
    app = create_celery_app(broker_url="redis://127.0.0.1:1/1", namespace=namespace)
    try:
        expected = {
            "broker_url": "redis://127.0.0.1:1/1",
            "task_acks_late": True,
            "task_reject_on_worker_lost": True,
            "worker_prefetch_multiplier": 1,
            "task_ignore_result": True,
            "result_backend": None,
            "task_serializer": "json",
            "accept_content": ["json"],
            "task_routes": {
                TranscriptionJob.name: {"queue": TranscriptionJob.queue},
                NotificationJob.name: {"queue": NotificationJob.queue},
            },
            "worker_hijack_root_logger": False,
            "worker_proc_alive_timeout": 30,
            "broker_connection_retry_on_startup": True,
            "broker_connection_timeout": 4,
            "task_publish_retry_policy": {
                "max_retries": 2,
                "interval_start": 0,
                "interval_step": 0.2,
                "interval_max": 0.5,
            },
            "broker_transport_options": {
                "visibility_timeout": 3600,
                "socket_connect_timeout": 2,
                "socket_timeout": 5,
                **({"global_keyprefix": namespace} if namespace else {}),
            },
        }
        assert {key: app.conf[key] for key in expected} == expected
        assert not ({TranscriptionJob.name, NotificationJob.name} & set(app.tasks))
    finally:
        app.close()


async def test_queue_sends_json_on_its_thread_and_eagerly_resolves_pool() -> None:
    app = Mock(spec=Celery)
    constructor_thread = threading.get_ident()
    pool_threads: list[int] = []
    pool = PropertyMock(side_effect=lambda: pool_threads.append(threading.get_ident()))
    type(app).producer_pool = pool
    send_threads: list[int] = []
    app.send_task.side_effect = lambda *a, **kw: send_threads.append(threading.get_ident())
    queue = CeleryJobQueue(cast(Celery, app))
    port: JobQueue = queue
    try:
        assert pool_threads == [constructor_thread]
        await port.enqueue(NotificationJob(voice_job_id=7, status="done"))
        app.send_task.assert_called_once_with(
            NotificationJob.name,
            kwargs={"voice_job_id": 7, "status": "done"},
            queue=NotificationJob.queue,
            countdown=None,
        )
        assert len(send_threads) == 1
        assert send_threads[0] != constructor_thread
    finally:
        queue.close()
    with pytest.raises(RuntimeError, match="shutdown"):
        await queue.enqueue(TranscriptionJob(voice_job_id=7))


async def test_negative_delay_is_refused_before_send() -> None:
    app = Mock(spec=Celery)
    queue = CeleryJobQueue(cast(Celery, app))
    try:
        with pytest.raises(ValueError, match="negative"):
            await queue.enqueue(TranscriptionJob(voice_job_id=7), delay_seconds=-1)
        app.send_task.assert_not_called()
    finally:
        queue.close()


async def test_dedicated_executor_runs_exactly_four_sends_at_once() -> None:
    app = Mock(spec=Celery)
    four_started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    threads: list[str] = []

    def blocked_send(*args: object, **kwargs: object) -> None:
        with lock:
            threads.append(threading.current_thread().name)
            if len(threads) == 4:
                four_started.set()
        if not release.wait(timeout=2):
            raise TimeoutError("the test did not release producer threads")

    app.send_task.side_effect = blocked_send
    queue = CeleryJobQueue(cast(Celery, app))
    tasks = [asyncio.create_task(queue.enqueue(TranscriptionJob(voice_job_id=7))) for _ in range(8)]
    try:
        assert await asyncio.to_thread(four_started.wait, 1)
        await asyncio.sleep(0.01)
        assert len(threads) == 4
        assert all(name.startswith("enqueue") for name in threads)
    finally:
        release.set()
        await asyncio.gather(*tasks)
        queue.close()


async def test_long_delay_is_clamped_and_logged_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    app = Mock(spec=Celery)
    queue = CeleryJobQueue(cast(Celery, app))
    try:
        with caplog.at_level("DEBUG", logger="taskbot.infrastructure.celery.job_queue"):
            await queue.enqueue(TranscriptionJob(voice_job_id=7), delay_seconds=900)
        assert app.send_task.call_args.kwargs["countdown"] == 600
        assert len(caplog.records) == 1
        assert "900" in caplog.text
        assert "600" in caplog.text
    finally:
        queue.close()


@pytest.mark.parametrize("error", [OperationalError("unavailable"), ValueError("bad task")])
async def test_only_operational_error_is_translated(error: Exception) -> None:
    app = Mock(spec=Celery)
    app.send_task.side_effect = error
    queue = CeleryJobQueue(cast(Celery, app))
    try:
        expected = EnqueueError if isinstance(error, OperationalError) else ValueError
        with pytest.raises(expected) as failure:
            await queue.enqueue(TranscriptionJob(voice_job_id=7))
        if expected is EnqueueError:
            assert failure.value.__cause__ is error
        else:
            assert failure.value is error
    finally:
        queue.close()
