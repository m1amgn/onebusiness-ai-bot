import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import pytest

from taskbot.domain.errors import InvalidTicketError
from taskbot.domain.jobs import TicketClaims
from taskbot.domain.users import User
from tests.fakes.clock import FakeClock
from tests.fakes.persistence import FakeStore, FakeUnitOfWork, FakeUserRepository
from tests.fakes.stream import (
    FakeElapsed,
    FakeEventSubscriber,
    FakeEventSubscription,
    StreamStep,
)

CLAIMS = TicketClaims(user_id=7, token_version=3)
NOW = datetime(2026, 10, 26, tzinfo=UTC)


class ControlledStore(FakeStore):
    def __init__(self, elapsed: FakeElapsed) -> None:
        super().__init__()
        self.state.users[7] = User(7, 70, "A", 3, NOW, NOW)
        self.elapsed = elapsed
        self.block_phase: str | None = None
        self.error_phase: str | None = None
        self.blocked = asyncio.Event()
        self.cancelled = False
        self.read_started: list[float] = []
        self.query_seconds: float = 0

    def __call__(self) -> ControlledUnitOfWork:
        return ControlledUnitOfWork(self)

    async def check(self, phase: str) -> None:
        if phase == "query":
            self.read_started.append(self.elapsed())
            self.elapsed.advance(self.query_seconds)
        if self.error_phase == phase:
            raise RuntimeError("database unavailable")
        if self.block_phase == phase:
            self.blocked.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise


class ControlledUsers(FakeUserRepository):
    def __init__(self, uow: ControlledUnitOfWork, store: ControlledStore) -> None:
        super().__init__(uow.working, store)
        self.controlled_store = store

    async def get_token_version(self, user_id: int) -> int | None:
        await self.controlled_store.check("query")
        return await super().get_token_version(user_id)


class ControlledUnitOfWork(FakeUnitOfWork):
    def __init__(self, store: ControlledStore) -> None:
        super().__init__(store)
        self.controlled_store = store

    async def __aenter__(self) -> Self:
        await self.controlled_store.check("entry")
        await super().__aenter__()
        self.users = ControlledUsers(self, self.controlled_store)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self.controlled_store.check("exit")
        finally:
            await super().__aexit__(exc_type, exc, tb)


def setup_stream(
    *steps: StreamStep,
) -> tuple[FakeElapsed, ControlledStore, FakeEventSubscriber]:
    elapsed = FakeElapsed()
    return (
        elapsed,
        ControlledStore(elapsed),
        FakeEventSubscriber(FakeEventSubscription(elapsed, steps)),
    )


async def test_connected_precedes_exact_raw_events_and_revocation_ends_stream() -> None:
    from taskbot.application.stream_events import Connected, Event, StreamUserEvents

    # Task contents are the wire payload, not domain models to deserialize again.
    payloads = [
        '{ "type": "task.created", "task": {"id": 2}, "extra": [1, 2] }',
        '{"type":"task.updated","task":null}',
        '{"type":"task.deleted","task_id":2}',
        '{ "type" : "auth.revoked", "extra": true }',
    ]
    elapsed, store, subscriber = setup_stream(*(StreamStep(1, raw) for raw in payloads))
    async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
        items = [item async for item in stream.items()]
    assert items == [
        Connected(),
        Event("task.created", payloads[0]),
        Event("task.updated", payloads[1]),
        Event("task.deleted", payloads[2]),
        Event("auth.revoked", payloads[3]),
    ]
    assert subscriber.user_ids == [7]
    assert subscriber.closed
    assert store.read_started == [0]
    assert "uow.commit" not in store.calls.items


@pytest.mark.parametrize("missing", [False, True])
async def test_initial_check_after_subscription_refuses_stale_claims_and_closes(
    missing: bool,
) -> None:
    from taskbot.application.stream_events import StreamUserEvents

    elapsed, store, subscriber = setup_stream()

    def revoke() -> None:
        if missing:
            store.state.users.clear()
        else:
            store.state.users[7] = replace(store.state.users[7], token_version=4)

    subscriber.on_subscribe = revoke
    with pytest.raises(InvalidTicketError):
        async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS):
            pytest.fail("A stale ticket opened a stream")
    assert subscriber.closed
    assert subscriber.subscription.timeouts == []
    assert not store.lock.locked()


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("wall_rollback", [0, -60])
async def test_recheck_uses_elapsed_deadline_despite_traffic_and_wall_clock_rollback(
    missing: bool, wall_rollback: int
) -> None:
    from taskbot.application.stream_events import Connected, Event, StreamUserEvents

    elapsed, store, subscriber = setup_stream()
    wall_clock = FakeClock(NOW)

    def revoke() -> None:
        wall_clock.advance(wall_rollback)
        if missing:
            store.state.users.clear()
        else:
            store.state.users[7] = replace(store.state.users[7], token_version=4)

    raw = '{"type":"task.updated","task":{"id":2}}'
    subscriber.subscription.steps.extend(
        [StreamStep(5, raw, revoke), *(StreamStep(5, raw) for _ in range(3))]
    )
    async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
        items = [item async for item in stream.items()]
    assert items == [
        Connected(),
        Event("task.updated", raw),
        Event("task.updated", raw),
        Event("task.updated", raw),
        Event("auth.revoked", '{"type":"auth.revoked"}'),
    ]
    assert store.read_started == [0, 20]
    assert subscriber.subscription.timeouts == [20, 15, 10, 5]
    assert subscriber.closed


async def test_recheck_deadline_uses_previous_check_start_including_initial_check() -> None:
    from taskbot.application.stream_events import StreamUserEvents

    elapsed, store, subscriber = setup_stream(StreamStep(40, '{"type":"auth.revoked"}'))
    store.query_seconds = 7
    async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
        _ = [item async for item in stream.items()]
    assert store.read_started == [0, 20, 40, 60]
    assert subscriber.closed


@pytest.mark.parametrize("phase", ["entry", "query", "exit"])
async def test_first_check_timeout_covers_whole_uow_and_maps_to_stream_error(
    phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from taskbot.application import stream_events
    from taskbot.domain.errors import EventStreamError

    monkeypatch.setattr(stream_events, "RECHECK_TIMEOUT_SECONDS", 0.01)
    elapsed, store, subscriber = setup_stream()
    store.block_phase = phase
    async with asyncio.timeout(1):
        with pytest.raises(EventStreamError):
            async with stream_events.StreamUserEvents(subscriber, store, elapsed).open(CLAIMS):
                pytest.fail("The stalled first check opened a stream")
    assert store.cancelled
    assert subscriber.closed
    assert not store.lock.locked()


@pytest.mark.parametrize("phase", ["entry", "query", "exit"])
async def test_periodic_timeout_ends_without_forwarding_next_event_and_closes(
    phase: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.application import stream_events

    monkeypatch.setattr(stream_events, "RECHECK_TIMEOUT_SECONDS", 0.01)
    elapsed, store, subscriber = setup_stream(StreamStep(20, '{"type":"task.created"}'))
    with caplog.at_level(logging.WARNING):
        async with asyncio.timeout(1):
            async with stream_events.StreamUserEvents(subscriber, store, elapsed).open(
                CLAIMS
            ) as stream:
                store.block_phase = phase
                items = [item async for item in stream.items()]
    assert items == [stream_events.Connected()]
    assert store.cancelled
    assert subscriber.closed
    assert not store.lock.locked()
    assert len(caplog.records) == 1


@pytest.mark.parametrize("phase", ["entry", "query", "exit"])
async def test_periodic_database_fault_ends_stream_and_logs_once(
    phase: str, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.application.stream_events import Connected, StreamUserEvents

    elapsed, store, subscriber = setup_stream(StreamStep(20, '{"type":"task.created"}'))
    with caplog.at_level(logging.WARNING):
        async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
            store.error_phase = phase
            items = [item async for item in stream.items()]
    assert items == [Connected()]
    assert subscriber.closed
    assert not store.lock.locked()
    assert len(caplog.records) == 1


@pytest.mark.parametrize("gap", [False, True])
async def test_subscription_fault_ends_stream(gap: bool, caplog: pytest.LogCaptureFixture) -> None:
    from taskbot.application.stream_events import Connected, StreamUserEvents
    from taskbot.domain.errors import EventStreamError, EventStreamGapError

    error = EventStreamGapError("reconnected") if gap else EventStreamError("unavailable")
    elapsed, store, subscriber = setup_stream(StreamStep(1, error))
    with caplog.at_level(logging.WARNING):
        async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
            items = [item async for item in stream.items()]
    assert items == [Connected()]
    assert subscriber.closed
    assert len(caplog.records) == 1


@pytest.mark.parametrize("raw", ["not JSON", "[]", "{}", '{"type":"unknown"}', '{"type":3}'])
async def test_invalid_payload_ends_without_forwarding(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.application.stream_events import Connected, StreamUserEvents

    elapsed, store, subscriber = setup_stream(StreamStep(1, raw))
    with caplog.at_level(logging.WARNING):
        async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
            items = [item async for item in stream.items()]
    assert items == [Connected()]
    assert subscriber.closed
    assert len(caplog.records) == 1


async def test_subscription_entry_failure_propagates_before_stream_opens() -> None:
    from taskbot.application.stream_events import StreamUserEvents
    from taskbot.domain.errors import EventStreamError

    elapsed, store, subscriber = setup_stream()
    error = EventStreamError("subscription unavailable")
    subscriber.enter_error = error
    with pytest.raises(EventStreamError) as caught:
        async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS):
            pytest.fail("The failed subscription opened a stream")
    assert caught.value is error
    assert store.read_started == []


@pytest.mark.parametrize("close_error", ["stream", "unexpected"])
async def test_cleanup_failure_is_logged_and_does_not_escape(
    close_error: str, caplog: pytest.LogCaptureFixture
) -> None:
    from taskbot.application.stream_events import StreamUserEvents
    from taskbot.domain.errors import EventStreamError

    elapsed, store, subscriber = setup_stream(StreamStep(1, '{"type":"auth.revoked"}'))
    subscriber.close_error = (
        EventStreamError("cleanup unavailable")
        if close_error == "stream"
        else RuntimeError("unexpected cleanup failure")
    )
    with caplog.at_level(logging.WARNING):
        async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
            _ = [item async for item in stream.items()]
    assert subscriber.closed
    assert len(caplog.records) == 1


@pytest.mark.parametrize("phase", ["initial", "read", "recheck"])
async def test_cancellation_propagates_and_closes_subscription(phase: str) -> None:
    from taskbot.application.stream_events import StreamUserEvents
    from taskbot.domain.errors import EventStreamError

    steps = [StreamStep(20, None)] if phase == "recheck" else []
    elapsed, store, subscriber = setup_stream(*steps)
    subscriber.close_error = EventStreamError("cleanup cannot replace cancellation")
    if phase == "initial":
        store.block_phase = "query"

    async def consume() -> None:
        async with StreamUserEvents(subscriber, store, elapsed).open(CLAIMS) as stream:
            if phase == "recheck":
                store.block_phase = "query"
            _ = [item async for item in stream.items()]

    consumer = asyncio.create_task(consume())
    try:
        async with asyncio.timeout(1):
            if phase == "read":
                await subscriber.subscription.reading.wait()
            else:
                await store.blocked.wait()
    finally:
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
    assert subscriber.closed
    assert not store.lock.locked()
