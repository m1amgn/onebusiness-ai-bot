import asyncio
import base64
import json
import math
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import pytest
from kombu.exceptions import OperationalError
from pydantic import BaseModel

from taskbot.domain.errors import EnqueueError
from taskbot.domain.jobs import NotificationJob, TranscriptionJob
from taskbot.infrastructure.celery.app import create_celery_app
from taskbot.infrastructure.celery.job_queue import CeleryJobQueue
from taskbot.infrastructure.redis.client import create_redis_client
from tests.processes import run_process
from tests.redis_sessions import RedisSession
from tests.tcp_proxy import tcp_proxy

BACKEND = Path(__file__).resolve().parents[2]


class BrokerHeaders(BaseModel):
    task: str
    eta: datetime | None


class BrokerMessage(BaseModel):
    headers: BrokerHeaders
    body: str


@pytest.mark.parametrize(
    ("job", "delay"),
    [
        (TranscriptionJob(voice_job_id=7), 0),
        (NotificationJob(voice_job_id=7, status="failed"), 0),
        (TranscriptionJob(voice_job_id=7), 5),
        (TranscriptionJob(voice_job_id=7), 900),
    ],
)
async def test_real_redis_message_route_payload_and_eta(
    redis_session: RedisSession, job: TranscriptionJob | NotificationJob, delay: int
) -> None:
    namespace = redis_session.child()
    other_namespace = redis_session.child()
    app = create_celery_app(broker_url=redis_session.broker_url, namespace=namespace)
    queue = CeleryJobQueue(app)
    started = datetime.now(UTC)
    try:
        await queue.enqueue(job, delay_seconds=delay)
        async with create_redis_client(redis_session.broker_url) as client:
            key = namespace + job.queue
            try:
                # redis-py shares synchronous/async return annotations for list commands.
                assert await client.llen(key) == 1  # type: ignore[misc]
                raw = await client.lindex(key, 0)  # type: ignore[misc]
                assert raw is not None
                message = BrokerMessage.model_validate_json(raw)
                assert message.headers.task == job.name
                body = json.loads(base64.b64decode(message.body))
                assert body[0] == []
                assert body[1] == job.model_dump(mode="json")
                other_queue = "notification" if job.queue == "transcription" else "transcription"
                assert await client.exists(namespace + other_queue) == 0
                assert await client.exists(other_namespace + job.queue) == 0
                if delay:
                    eta = message.headers.eta
                    assert eta is not None
                    eta_delay = (eta - started).total_seconds()
                    assert abs(eta_delay - min(delay, 600)) < 1
                else:
                    assert message.headers.eta is None
            finally:
                await client.unlink(key)
    finally:
        queue.close()
        app.close()


async def test_negative_delay_leaves_no_real_broker_message(redis_session: RedisSession) -> None:
    namespace = redis_session.child()
    app = create_celery_app(broker_url=redis_session.broker_url, namespace=namespace)
    queue = CeleryJobQueue(app)
    try:
        with pytest.raises(ValueError, match="negative"):
            await queue.enqueue(TranscriptionJob(voice_job_id=7), delay_seconds=-1)
        async with create_redis_client(redis_session.broker_url) as client:
            assert [key async for key in client.scan_iter(match=namespace + "*")] == []
    finally:
        queue.close()
        app.close()


class FailureTiming(TypedDict):
    elapsed: float
    max_gap: float
    dns_elapsed: float
    failures: int
    canceled: int


async def measure_failed_enqueues(url: str, namespace: str, count: int) -> FailureTiming:
    """Run in a killable subprocess, including any lingering producer threads."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    app = create_celery_app(broker_url=url, namespace=namespace)
    queue = CeleryJobQueue(app)
    gaps: list[float] = []
    running = True

    async def tick() -> None:
        previous = time.monotonic()
        while running:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - previous)
            previous = now

    async def enqueue() -> None:
        with pytest.raises(EnqueueError) as failure:
            await queue.enqueue(TranscriptionJob(voice_job_id=7))
        assert isinstance(failure.value.__cause__, OperationalError)
        raise failure.value

    ticker = asyncio.create_task(tick())
    started = time.monotonic()
    tasks = [asyncio.create_task(enqueue()) for _ in range(count)]
    try:
        await asyncio.sleep(0.1)
        if count > 1:
            tasks[0].cancel()
        dns_started = time.monotonic()
        await asyncio.wait_for(loop.getaddrinfo("localhost", 80), timeout=0.2)
        dns_elapsed = time.monotonic() - dns_started
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.monotonic() - started
        return {
            "elapsed": elapsed,
            "max_gap": max(gaps),
            "dns_elapsed": dns_elapsed,
            "failures": sum(isinstance(result, EnqueueError) for result in results),
            "canceled": sum(isinstance(result, asyncio.CancelledError) for result in results),
        }
    finally:
        running = False
        await ticker
        queue.close()
        app.close()


async def assert_failed_enqueues(url: str, namespace: str, count: int = 1) -> None:
    # This conservative bound includes the connection deadline's final overshoot,
    # redis-py's connect + read timeouts, and every configured publish retry.
    config_app = create_celery_app(broker_url=url, namespace=namespace)
    try:
        transport = config_app.conf.broker_transport_options
        policy = config_app.conf.task_publish_retry_policy
        attempt_budget = (
            config_app.conf.broker_connection_timeout
            + policy["interval_max"]
            + transport["socket_connect_timeout"]
            + transport["socket_timeout"]
        )
        retries = policy["max_retries"]
        single_bound = (retries + 1) * attempt_budget + retries * policy["interval_max"]
    finally:
        config_app.close()
    bound = math.ceil(count / 4) * single_bound
    script = (
        "import asyncio,json,sys; "
        "from tests.integration.test_celery_job_queue import measure_failed_enqueues; "
        "print(json.dumps(asyncio.run(measure_failed_enqueues(sys.argv[1],sys.argv[2],int(sys.argv[3])))))"
    )
    result = await run_process(
        sys.executable,
        "-c",
        script,
        url,
        namespace,
        str(count),
        cwd=BACKEND,
        timeout_seconds=bound + 5,
    )
    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)
    print(f"A3 count={count} bound={bound:.1f}s timing={timing}")
    assert timing["elapsed"] < bound + 1
    assert timing["max_gap"] < 0.2
    assert timing["dns_elapsed"] < 0.2
    assert timing["failures"] == count - int(count > 1)
    assert timing["canceled"] == int(count > 1)


async def test_closed_broker_is_bounded_and_loop_stays_responsive(
    redis_session: RedisSession,
) -> None:
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        await assert_failed_enqueues(f"redis://127.0.0.1:{port}/1", redis_session.child())


async def test_unroutable_broker_is_bounded_and_loop_stays_responsive(
    redis_session: RedisSession,
) -> None:
    await assert_failed_enqueues("redis://192.0.2.1:6379/1", redis_session.child())


@pytest.mark.parametrize("count", [1, 8])
async def test_frozen_broker_bounds_thread_batches_and_keeps_dns_available(
    redis_session: RedisSession, count: int
) -> None:
    async with tcp_proxy(redis_session.broker_url) as proxy:
        proxy.freeze()
        await assert_failed_enqueues(proxy.url, redis_session.child(), count)
