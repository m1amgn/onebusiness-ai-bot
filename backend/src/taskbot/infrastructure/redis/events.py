import asyncio

from redis.asyncio import Redis

from taskbot.domain.events import DomainEvent
from taskbot.infrastructure.redis.client import user_channel


class RedisEventPublisher:
    def __init__(self, client: Redis, namespace: str) -> None:
        self._client = client
        self._namespace = namespace

    async def publish(self, event: DomainEvent) -> None:
        async with asyncio.timeout(1):
            await self._client.publish(
                user_channel(self._namespace, event.user_id), event.model_dump_json()
            )
