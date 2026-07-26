from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def build_engine(database_url: str | None = None) -> AsyncEngine:
    url = database_url or settings.database_url
    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "echo": False,
    }
    if url.startswith("mysql"):
        kwargs.update({"pool_recycle": 1800, "pool_size": 10, "max_overflow": 20})
    return create_async_engine(url, **kwargs)


engine = build_engine()
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all_for_test(target_engine: AsyncEngine | None = None) -> None:
    from app.db.models import import_all_models

    import_all_models()
    current_engine = target_engine or engine
    from app.db.base import Base

    async with current_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
