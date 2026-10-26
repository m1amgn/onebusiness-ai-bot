from datetime import datetime

from sqlalchemy import Boolean, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from taskbot.domain.time import require_aware
from taskbot.domain.users import AuthenticatedUser, TelegramIdentity, UpsertedUser, User
from taskbot.infrastructure.sqlalchemy.mapping import user_from_row
from taskbot.infrastructure.sqlalchemy.queries import one
from taskbot.infrastructure.sqlalchemy.tables import UserRow, concrete_table


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, identity: TelegramIdentity, now: datetime) -> UpsertedUser:
        require_aware(now)
        statement = insert(concrete_table(UserRow)).values(
            telegram_user_id=identity.telegram_user_id,
            display_name=identity.display_name,
            token_version=0,
            created_at=now,
            updated_at=now,
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[UserRow.telegram_user_id],
            set_={
                "display_name": statement.excluded.display_name,
                "updated_at": statement.excluded.updated_at,
            },
        ).returning(*UserRow.__table__.c, literal_column("(xmax = 0)", Boolean).label("inserted"))
        row = await one(self._session, upsert)
        if row is None:
            raise RuntimeError("Unconditional user upsert returned no row")
        return UpsertedUser(user_from_row(row), bool(row["inserted"]))

    async def get(self, user_id: int) -> User | None:
        row = await one(self._session, select(*UserRow.__table__.c).where(UserRow.id == user_id))
        return None if row is None else user_from_row(row)

    async def find_by_token_hash(self, token_hash: str) -> AuthenticatedUser | None:
        row = await one(
            self._session,
            select(UserRow.id, UserRow.token_version).where(
                UserRow.dashboard_token_hash == token_hash
            ),
        )
        return None if row is None else AuthenticatedUser(int(row["id"]), int(row["token_version"]))

    async def rotate_token(self, user_id: int, token_hash: str, now: datetime) -> int | None:
        require_aware(now)
        row = await one(
            self._session,
            update(concrete_table(UserRow))
            .where(UserRow.id == user_id)
            .values(
                dashboard_token_hash=token_hash,
                token_version=UserRow.token_version + 1,
                updated_at=now,
            )
            .returning(UserRow.token_version),
        )
        return None if row is None else int(row["token_version"])

    async def get_token_version(self, user_id: int) -> int | None:
        row = await one(self._session, select(UserRow.token_version).where(UserRow.id == user_id))
        return None if row is None else int(row["token_version"])
