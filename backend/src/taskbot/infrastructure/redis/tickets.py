import logging
import re

from pydantic import ValidationError
from redis.asyncio import Redis

from taskbot.domain.jobs import TicketClaims
from taskbot.domain.ports.services import TokenGenerator
from taskbot.infrastructure.redis.client import namespaced

logger = logging.getLogger(__name__)
TICKET_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)


class RedisTicketStore:
    def __init__(
        self, client: Redis, tokens: TokenGenerator, namespace: str, ttl_seconds: int = 30
    ) -> None:
        self._client = client
        self._tokens = tokens
        self._namespace = namespace
        self._ttl_seconds = ttl_seconds

    def _key(self, ticket: str) -> str:
        return namespaced(self._namespace, "ticket:" + ticket)

    async def issue(self, claims: TicketClaims) -> str:
        ticket = self._tokens.generate()
        created = await self._client.set(
            self._key(ticket), claims.model_dump_json(), ex=self._ttl_seconds, nx=True
        )
        if created is None:
            raise RuntimeError("Realtime ticket collision")
        return ticket

    async def consume(self, ticket: str) -> TicketClaims | None:
        if TICKET_PATTERN.fullmatch(ticket) is None:
            return None
        try:
            value = await self._client.getdel(self._key(ticket))
            if value is None:
                return None
            return TicketClaims.model_validate_json(value)
        except ValidationError, UnicodeDecodeError:
            logger.warning("Discarded invalid realtime ticket claims")
            return None
