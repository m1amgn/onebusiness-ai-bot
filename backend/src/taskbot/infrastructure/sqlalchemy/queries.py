from sqlalchemy import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable


async def one(session: AsyncSession, statement: Executable) -> RowMapping | None:
    return (await session.execute(statement)).mappings().one_or_none()


async def many(session: AsyncSession, statement: Executable) -> list[RowMapping]:
    return list((await session.execute(statement)).mappings().all())
