from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str, *, nullpool: bool = False) -> AsyncEngine:
    kwargs: dict = {"pool_pre_ping": True}
    if nullpool:
        kwargs = {"poolclass": NullPool}
    if settings.USE_PGBOUNCER:
        # PgBouncer transaction pooling cannot keep server-side prepared statements.
        kwargs["connect_args"] = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    return create_async_engine(url, **kwargs)


engine = make_engine(settings.DATABASE_URL)
read_engine = make_engine(settings.READ_DATABASE_URL) if settings.READ_DATABASE_URL else engine

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
ReadSessionLocal = async_sessionmaker(read_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_read_session() -> AsyncIterator[AsyncSession]:
    async with ReadSessionLocal() as session:
        yield session
