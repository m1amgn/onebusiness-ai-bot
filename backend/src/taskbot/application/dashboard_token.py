from dataclasses import dataclass, field

from taskbot.application.register_user import RegisterOrGetUser
from taskbot.domain.errors import InvalidTokenError
from taskbot.domain.events import AuthRevoked, DomainEvent
from taskbot.domain.limits import MAX_TOKEN_LENGTH
from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.ports.services import Clock, TokenGenerator, TokenHasher
from taskbot.domain.users import AuthenticatedUser, TelegramIdentity


def dashboard_link(base_url: str, token: str) -> str:
    return f"{base_url}/#token={token}"


@dataclass(frozen=True, slots=True)
class IssuedToken:
    user_id: int
    token: str = field(repr=False)
    token_version: int
    events: tuple[DomainEvent, ...]


class IssueDashboardToken:
    def __init__(
        self,
        uows: UnitOfWorkFactory,
        register: RegisterOrGetUser,
        generator: TokenGenerator,
        hasher: TokenHasher,
        clock: Clock,
    ) -> None:
        self._uows = uows
        self._register = register
        self._generator = generator
        self._hasher = hasher
        self._clock = clock

    async def execute(self, identity: TelegramIdentity) -> IssuedToken:
        async with self._uows() as uow:
            registered = await self._register.within(uow, identity)
            token = self._generator.generate()
            version = await uow.users.rotate_token(
                registered.user.id, self._hasher.hash(token), self._clock.now()
            )
            if version is None:
                raise RuntimeError("Registered user disappeared")
            uow.record(AuthRevoked(user_id=registered.user.id))
            events = await uow.commit()
        return IssuedToken(registered.user.id, token, version, events)


class AuthenticateToken:
    def __init__(self, uows: UnitOfWorkFactory, hasher: TokenHasher) -> None:
        self._uows = uows
        self._hasher = hasher

    async def execute(self, token: str) -> AuthenticatedUser:
        if not token or not token.isascii() or len(token) > MAX_TOKEN_LENGTH:
            raise InvalidTokenError("Invalid dashboard token")
        token_hash = self._hasher.hash(token)
        async with self._uows() as uow:
            result = await uow.users.find_by_token_hash(token_hash)
        if result is None:
            raise InvalidTokenError("Invalid dashboard token")
        return result
