from taskbot.domain.errors import InvalidTicketError
from taskbot.domain.jobs import TicketClaims
from taskbot.domain.ports.messaging import TicketStore
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.users import AuthenticatedUser


class IssueRealtimeTicket:
    def __init__(self, tickets: TicketStore) -> None:
        self._tickets = tickets

    async def execute(self, user: AuthenticatedUser) -> str:
        return await self._tickets.issue(
            TicketClaims(user_id=user.user_id, token_version=user.token_version)
        )


class ConsumeRealtimeTicket:
    def __init__(self, tickets: TicketStore, uows: UnitOfWorkFactory) -> None:
        self._tickets = tickets
        self._uows = uows

    async def execute(self, ticket: str) -> TicketClaims:
        claims = await self._tickets.consume(ticket)
        if claims is None:
            raise InvalidTicketError("Invalid realtime ticket")
        async with self._uows() as uow:
            version = await uow.users.get_token_version(claims.user_id)
        if version is None or version != claims.token_version:
            raise InvalidTicketError("Invalid realtime ticket")
        return claims
