from datetime import datetime, timedelta

from taskbot.domain.ports.persistence import UnitOfWorkFactory
from taskbot.domain.users import TelegramIdentity


async def test_user_upsert_and_token_contract(uows: UnitOfWorkFactory, now: datetime) -> None:
    async with uows() as uow:
        first = await uow.users.upsert(TelegramIdentity(7, "A\x00"), now)
        assert first.created
        again = await uow.users.upsert(TelegramIdentity(7, "B"), now + timedelta(seconds=1))
        assert not again.created
        assert again.user.id == first.user.id
        assert again.user.display_name == "B"
        assert again.user.updated_at == now + timedelta(seconds=1)
        assert await uow.users.get(first.user.id) == again.user
        assert await uow.users.rotate_token(first.user.id, "hash1", now) == 1
        assert await uow.users.get_token_version(first.user.id) == 1
        auth = await uow.users.find_by_token_hash("hash1")
        assert auth is not None
        assert auth.user_id == first.user.id
        assert auth.token_version == 1
        assert await uow.users.rotate_token(first.user.id, "hash2", now) == 2
        await uow.users.upsert(TelegramIdentity(7, "C"), now)
        assert await uow.users.find_by_token_hash("hash1") is None
        assert await uow.users.find_by_token_hash("hash2") is not None
        assert await uow.users.get(999) is None
        assert await uow.users.get_token_version(999) is None
        assert await uow.users.rotate_token(999, "missing", now) is None
        await uow.commit()
    async with uows() as uow:
        assert await uow.users.get_token_version(first.user.id) == 2
