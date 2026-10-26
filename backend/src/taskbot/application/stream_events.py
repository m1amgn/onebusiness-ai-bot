import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from taskbot.domain.errors import EventStreamError, InvalidTicketError
from taskbot.domain.events import AuthRevoked
from taskbot.domain.jobs import TicketClaims
from taskbot.domain.ports.messaging import EventSubscriber, EventSubscription
from taskbot.domain.ports.persistence import UnitOfWorkFactory

logger = logging.getLogger(__name__)

RECHECK_SECONDS = 20
RECHECK_TIMEOUT_SECONDS = 5

type EventType = Literal["task.created", "task.updated", "task.deleted", "auth.revoked"]


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")
    type: EventType


@dataclass(frozen=True, slots=True)
class Connected:
    pass


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    raw_json: str


type StreamItem = Connected | Event


async def _has_current_version(uows: UnitOfWorkFactory, claims: TicketClaims) -> bool:
    async with asyncio.timeout(RECHECK_TIMEOUT_SECONDS):
        async with uows() as uow:
            version = await uow.users.get_token_version(claims.user_id)
    return version is not None and version == claims.token_version


class UserEventStream:
    def __init__(
        self,
        subscription: EventSubscription,
        uows: UnitOfWorkFactory,
        claims: TicketClaims,
        monotonic: Callable[[], float],
        next_recheck: float,
    ) -> None:
        self._subscription = subscription
        self._uows = uows
        self._claims = claims
        self._monotonic = monotonic
        self._next_recheck = next_recheck

    async def items(self) -> AsyncIterator[StreamItem]:
        yield Connected()
        pending: str | None = None
        try:
            while True:
                now = self._monotonic()
                if now >= self._next_recheck:
                    self._next_recheck = now + RECHECK_SECONDS
                    if not await _has_current_version(self._uows, self._claims):
                        yield Event(
                            "auth.revoked",
                            AuthRevoked(user_id=self._claims.user_id).model_dump_json(),
                        )
                        return
                if pending is None:
                    pending = await self._subscription.next(
                        max(0, self._next_recheck - self._monotonic())
                    )
                    continue
                envelope = EventEnvelope.model_validate_json(pending)
                yield Event(envelope.type, pending)
                if envelope.type == "auth.revoked":
                    return
                pending = None
        except Exception:
            # Persistence has no domain error type. After headers, any stream or
            # database fault must close cleanly so the dashboard can reconnect.
            logger.warning(
                "User event stream ended after a stream or re-check failure", exc_info=True
            )


class StreamUserEvents:
    def __init__(
        self,
        subscriber: EventSubscriber,
        uows: UnitOfWorkFactory,
        monotonic: Callable[[], float],
    ) -> None:
        self._subscriber = subscriber
        self._uows = uows
        self._monotonic = monotonic

    @asynccontextmanager
    async def open(self, claims: TicketClaims) -> AsyncIterator[UserEventStream]:
        context = self._subscriber.subscribe(claims.user_id)
        subscription = await context.__aenter__()
        try:
            check_started = self._monotonic()
            try:
                current = await _has_current_version(self._uows, claims)
            except TimeoutError as error:
                raise EventStreamError("Initial token version check timed out") from error
            if not current:
                raise InvalidTicketError("Invalid realtime ticket")
            yield UserEventStream(
                subscription, self._uows, claims, self._monotonic, check_started + RECHECK_SECONDS
            )
        finally:
            try:
                await context.__aexit__(None, None, None)
            except Exception:
                logger.warning("User event subscription cleanup failed", exc_info=True)
