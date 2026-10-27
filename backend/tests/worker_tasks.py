"""Celery registrations and lifecycle hooks exclusively for isolated worker tests."""

import asyncio
import json
import os
import signal
import threading
import time
from uuid import uuid4

from billiard.exceptions import SoftTimeLimitExceeded
from celery import signals
from celery.app.task import Task

from taskbot.domain.events import AuthRevoked
from taskbot.entrypoints.worker import app, runtime
from taskbot.entrypoints.worker.container import WorkerContainer


def record(log: str, marker: str, *, event: str, **fields: object) -> None:
    row = {"time": time.time(), "pid": os.getpid(), "marker": marker, "event": event, **fields}
    # One O_APPEND write keeps sibling records separate in the notification pool.
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(row) + "\n").encode())
    finally:
        os.close(descriptor)


@app.task(name="s3test.sleep", bind=True)
def sleep(self: Task, seconds: float, marker: str, log: str) -> None:
    redelivered = bool(self.request.delivery_info.get("redelivered", False))
    record(log, marker, event="start", redelivered=redelivered)
    runtime.run_async(asyncio.sleep(seconds))
    record(log, marker, event="end", redelivered=redelivered)


@app.task(name="s3test.loop_identity")
def loop_identity(log: str) -> None:
    loop = runtime.current_loop()
    marker = vars(loop).setdefault("_taskbot_test_marker", uuid4().hex)
    record(log, "loop", event="loop", loop_marker=marker, pending=len(asyncio.all_tasks(loop)))


@app.task(name="s3test.publish")
def publish(user_id: int, log: str) -> None:
    publisher = runtime.current_container().publisher
    assert publisher is not None
    runtime.run_async(publisher.publish(AuthRevoked(user_id=user_id)))
    record(log, "published", event="published")


@app.task(name="s3test.soft_limit", soft_time_limit=1, time_limit=3)
def soft_limit(log: str) -> None:
    async def waiting() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            record(log, "cancelled", event="cancelled")

    try:
        runtime.run_async(waiting())
    except SoftTimeLimitExceeded:
        record(log, "soft_limit", event="soft_limit")


def delay_replacement(**kwargs: object) -> None:
    path = os.environ.get("TASKBOT_TEST_CHILD_STARTS")
    if path is None:
        return
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        time.sleep(float(os.environ.get("TASKBOT_TEST_REPLACEMENT_INIT_DELAY", "0")))
    else:
        os.close(descriptor)


def orphan_guard(**kwargs: object) -> None:
    parent = int(os.environ.get("TASKBOT_TEST_PARENT_PID", "0"))
    if parent <= 1:
        return

    def watch() -> None:
        while True:
            time.sleep(0.5)
            try:
                os.kill(parent, 0)
            except ProcessLookupError:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=watch, name="test-worker-orphan-guard", daemon=True).start()


async def fail_build(container: WorkerContainer) -> None:
    # Build first: the failure exercises ownership of an already-created Redis client.
    await original_build(container)
    raise RuntimeError("injected child runtime failure")


original_build = runtime.build_container
if os.environ.get("TASKBOT_TEST_FAIL_CHILD_RUNTIME") == "1":
    runtime.build_container = fail_build
if timeout := os.environ.get("TASKBOT_TEST_ALIVE_TIMEOUT"):
    app.conf.worker_proc_alive_timeout = int(timeout)
signals.worker_process_init.connect(delay_replacement, weak=False)
signals.worker_ready.connect(orphan_guard, weak=False)
