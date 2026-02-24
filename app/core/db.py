from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, Session, create_engine

from app.core.config import get_settings


def _to_sync_db_url(db_url: str) -> str:
    if db_url.startswith("postgres://"):
        return "postgresql://" + db_url[len("postgres://") :]
    if db_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + db_url[len("postgresql+asyncpg://") :]
    return db_url


def _to_async_db_url(db_url: str) -> str:
    if db_url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + db_url[len("sqlite:///") :]
    if db_url.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + db_url[len("postgresql+psycopg2://") :]
    if db_url.startswith("postgresql+psycopg://"):
        return "postgresql+asyncpg://" + db_url[len("postgresql+psycopg://") :]
    if db_url.startswith("postgres://"):
        return "postgresql+asyncpg://" + db_url[len("postgres://") :]
    if db_url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + db_url[len("postgresql://") :]
    return db_url


settings = get_settings()
SYNC_DB_URL = _to_sync_db_url(settings.DB_URL)
ASYNC_DB_URL = _to_async_db_url(settings.DB_URL)

engine: Engine = create_engine(SYNC_DB_URL, pool_pre_ping=True)
async_engine: AsyncEngine = create_async_engine(ASYNC_DB_URL, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)


def init_db() -> None:
    # Import models for SQLModel metadata registration.
    import app.models.sql_models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
