from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, RootModel

from taskbot.application.stream_events import Connected, UserEventStream
from taskbot.domain.errors import InvalidTicketError
from taskbot.domain.events import AuthRevoked, TaskCreated, TaskDeleted, TaskUpdated
from taskbot.domain.limits import TICKET_TTL_SECONDS
from taskbot.entrypoints.api.auth import CurrentUser
from taskbot.entrypoints.api.container import Services
from taskbot.entrypoints.api.models import ApiError, json_error_response

router = APIRouter(prefix="/api", responses={401: {"model": ApiError}})


class RealtimeTicket(BaseModel):
    ticket: str
    expires_in: int


class ServerEvent(
    RootModel[
        Annotated[
            TaskCreated | TaskUpdated | TaskDeleted | AuthRevoked, Field(discriminator="type")
        ]
    ]
):
    pass


@router.post("/realtime/tickets", status_code=201)
async def issue_realtime_ticket(user: CurrentUser, services: Services) -> RealtimeTicket:
    ticket = await services.issue_ticket.execute(user)
    return RealtimeTicket(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


async def open_stream(
    services: Services, ticket: str | None = None
) -> AsyncIterator[UserEventStream]:
    if ticket is None:
        raise InvalidTicketError("Invalid realtime ticket")
    claims = await services.consume_ticket.execute(ticket)
    async with services.stream_events.open(claims) as stream:
        yield stream


@router.get(
    "/events",
    response_class=EventSourceResponse,
    responses={200: {"model": ServerEvent}, 401: json_error_response(), 503: json_error_response()},
)
async def events(
    stream: Annotated[UserEventStream, Depends(open_stream)],
) -> AsyncIterator[ServerSentEvent]:
    async for item in stream.items():
        if isinstance(item, Connected):
            yield ServerSentEvent(comment="connected")
        else:
            yield ServerSentEvent(raw_data=item.raw_json)
