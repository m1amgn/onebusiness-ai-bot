from dataclasses import dataclass
from datetime import datetime

from taskbot.domain.text import clean_inline


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_user_id: int
    display_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_name", clean_inline(self.display_name))


@dataclass(frozen=True, slots=True)
class User:
    id: int
    telegram_user_id: int
    display_name: str
    token_version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class UpsertedUser:
    user: User
    created: bool


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: int
    token_version: int
