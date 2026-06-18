from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine

from app.core.config import get_settings


def _to_sync_db_url(db_url: str) -> str:
    if db_url.startswith("postgres://"):
        return "postgresql://" + db_url[len("postgres://") :]
    if db_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + db_url[len("postgresql+asyncpg://") :]
    return db_url


settings = get_settings()
SYNC_DB_URL = _to_sync_db_url(settings.DB_URL)

# SQLite doesn't support connection pools; use NullPool for it.
_is_sqlite = SYNC_DB_URL.startswith("sqlite")
_pool_kwargs: dict = (
    {"pool_pre_ping": True}
    if _is_sqlite
    else {"pool_pre_ping": True, "pool_size": 20, "max_overflow": 10, "pool_timeout": 30, "pool_recycle": 3600}
)
engine: Engine = create_engine(SYNC_DB_URL, **_pool_kwargs)


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
