import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass
class FakeElapsed:
    value: float = 0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass
class StreamStep:
    after: float
    payload: str | Exception | None
    action: Callable[[], None] | None = None


class FakeEventSubscription:
    def __init__(self, elapsed: FakeElapsed, steps: Iterable[StreamStep] = ()) -> None:
        self.elapsed = elapsed
        self.steps = deque(steps)
        self.timeouts: list[float] = []
        self.reading = asyncio.Event()

    async def next(self, timeout_seconds: float) -> str | None:
        self.timeouts.append(timeout_seconds)
        self.reading.set()
        if not self.steps:
            await asyncio.Event().wait()
            return None
        step = self.steps[0]
        if step.after > timeout_seconds:
            step.after -= timeout_seconds
            self.elapsed.advance(timeout_seconds)
            return None
        self.steps.popleft()
        self.elapsed.advance(step.after)
        if step.action is not None:
            step.action()
        if isinstance(step.payload, Exception):
            raise step.payload
        return step.payload


class FakeEventSubscriber:
    def __init__(self, subscription: FakeEventSubscription) -> None:
        self.subscription = subscription
        self.user_ids: list[int] = []
        self.closed = False
        self.on_subscribe: Callable[[], None] | None = None
        self.enter_error: Exception | None = None
        self.close_error: Exception | None = None

    @asynccontextmanager
    async def subscribe(self, user_id: int) -> AsyncIterator[FakeEventSubscription]:
        self.user_ids.append(user_id)
        try:
            if self.enter_error is not None:
                raise self.enter_error
            if self.on_subscribe is not None:
                self.on_subscribe()
            yield self.subscription
        finally:
            self.closed = True
            if self.close_error is not None:
                raise self.close_error
