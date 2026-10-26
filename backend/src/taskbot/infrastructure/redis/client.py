from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff


def create_redis_client(url: str, *, client_name: str | None = None) -> Redis:
    client: Redis = Redis.from_url(
        url,
        client_name=client_name,
        socket_connect_timeout=2,
        socket_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry=Retry(ExponentialWithJitterBackoff(base=0.1, cap=1), 3),
        decode_responses=True,
    )
    return client


def namespaced(namespace: str, key: str) -> str:
    return namespace + key


def user_channel(namespace: str, user_id: int) -> str:
    return namespaced(namespace, f"events:user:{user_id}")
