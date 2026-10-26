from taskbot.domain.ports.persistence import UnitOfWork, UnitOfWorkFactory
from taskbot.domain.ports.services import Clock
from taskbot.domain.users import TelegramIdentity, UpsertedUser


class RegisterOrGetUser:
    def __init__(self, uows: UnitOfWorkFactory, clock: Clock) -> None:
        self._uows = uows
        self._clock = clock

    async def within(self, uow: UnitOfWork, identity: TelegramIdentity) -> UpsertedUser:
        return await uow.users.upsert(identity, self._clock.now())

    async def execute(self, identity: TelegramIdentity) -> UpsertedUser:
        async with self._uows() as uow:
            result = await self.within(uow, identity)
            await uow.commit()
        return result
