import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, RedisDsn, field_validator


class RedisSettingsMixin(BaseModel):
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    celery_broker_url: RedisDsn | None = None
    redis_namespace: str = ""

    @field_validator("redis_namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        if value and re.fullmatch(r"[a-z0-9_]{1,64}:([a-z0-9_]{1,32}:)?", value) is None:
            raise ValueError("Invalid Redis namespace")
        return value

    @property
    def broker_url(self) -> str:
        if self.celery_broker_url is not None:
            return str(self.celery_broker_url)
        parts = urlsplit(str(self.redis_url))
        # Kombu rejects redis-py query options, but SSL options carry certificate policy.
        query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key.startswith("ssl_")
            ]
        )
        return urlunsplit(parts._replace(path="/1", query=query, fragment=""))
