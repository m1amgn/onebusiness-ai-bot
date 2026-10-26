from datetime import UTC, datetime

import pytest

from taskbot.application.realtime_tickets import ConsumeRealtimeTicket, IssueRealtimeTicket
from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.errors import InvalidTicketError
from taskbot.domain.jobs import TicketClaims
from taskbot.domain.users import AuthenticatedUser, TelegramIdentity
from tests.fakes.clock import FakeClock
from tests.fakes.messaging import FakeTicketStore
from tests.fakes.persistence import FakeStore


async def test_issue_preserves_authenticated_user_claims() -> None:
    tickets = FakeTicketStore()
    ticket = await IssueRealtimeTicket(tickets).execute(AuthenticatedUser(7, 3))
    assert await tickets.consume(ticket) == TicketClaims(user_id=7, token_version=3)


@pytest.mark.parametrize("version", [None, 0, 1])
async def test_consume_checks_user_and_version_without_commit(version: int | None) -> None:
    store = FakeStore()
    if version is not None:
        clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
        await RegisterOrGetUser(store, clock).execute(TelegramIdentity(7, "A"))
    tickets = FakeTicketStore()
    claims = TicketClaims(user_id=1, token_version=version or 1)
    ticket = await tickets.issue(claims)
    before = store.calls.items.count("uow.commit")
    consume = ConsumeRealtimeTicket(tickets, store)
    if version == 0:
        # The freshly registered user has version 0, while this ticket has version 1.
        with pytest.raises(InvalidTicketError):
            await consume.execute(ticket)
    elif version is None:
        with pytest.raises(InvalidTicketError):
            await consume.execute(ticket)
    else:
        claims = TicketClaims(user_id=1, token_version=0)
        ticket = await tickets.issue(claims)
        assert await consume.execute(ticket) == claims
    assert store.calls.items.count("uow.commit") == before
    with pytest.raises(InvalidTicketError):
        await consume.execute(ticket)


async def test_rotated_version_rejects_and_consumes_ticket() -> None:
    from taskbot.application.dashboard_token import IssueDashboardToken
    from tests.fakes.tokens import FakeTokenGenerator, FakeTokenHasher

    store = FakeStore()
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    register = RegisterOrGetUser(store, clock)
    issue = IssueDashboardToken(store, register, FakeTokenGenerator(), FakeTokenHasher(), clock)
    first = await issue.execute(TelegramIdentity(7, "A"))
    tickets = FakeTicketStore()
    ticket = await tickets.issue(TicketClaims(user_id=first.user_id, token_version=1))
    await issue.execute(TelegramIdentity(7, "A"))
    with pytest.raises(InvalidTicketError):
        await ConsumeRealtimeTicket(tickets, store).execute(ticket)
    assert await tickets.consume(ticket) is None
