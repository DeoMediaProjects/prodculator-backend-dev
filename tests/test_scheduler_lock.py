"""Tests for gating the background scheduler to a single worker via a Postgres
advisory lock."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app.core.scheduler as scheduler


def _fake_engine(dialect: str, acquired: bool):
    """Build a fake SQLAlchemy engine whose connection's advisory-lock query
    returns `acquired`."""
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = acquired
    engine = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
    engine.connect = MagicMock(return_value=conn)
    return engine, conn


class TestAcquireSingletonLock:
    def teardown_method(self):
        scheduler._lock_conn = None

    def test_non_postgres_is_a_noop_and_allows_start(self):
        engine, _ = _fake_engine("sqlite", acquired=False)
        with patch("app.core.db.engine", engine):
            assert scheduler._acquire_singleton_lock() is True
        # No connection held for sqlite.
        assert scheduler._lock_conn is None

    def test_postgres_lock_acquired_holds_connection(self):
        engine, conn = _fake_engine("postgresql", acquired=True)
        with patch("app.core.db.engine", engine):
            assert scheduler._acquire_singleton_lock() is True
        # The connection is kept open to hold the lock.
        assert scheduler._lock_conn is conn
        conn.close.assert_not_called()

    def test_postgres_lock_not_acquired_closes_connection(self):
        engine, conn = _fake_engine("postgresql", acquired=False)
        with patch("app.core.db.engine", engine):
            assert scheduler._acquire_singleton_lock() is False
        assert scheduler._lock_conn is None
        conn.close.assert_called_once()

    def test_error_fails_closed(self):
        engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        engine.connect = MagicMock(side_effect=RuntimeError("db down"))
        with patch("app.core.db.engine", engine):
            assert scheduler._acquire_singleton_lock() is False
        assert scheduler._lock_conn is None

    def test_release_unlocks_and_closes(self):
        engine, conn = _fake_engine("postgresql", acquired=True)
        with patch("app.core.db.engine", engine):
            scheduler._acquire_singleton_lock()
        assert scheduler._lock_conn is conn

        scheduler._release_singleton_lock()
        # pg_advisory_unlock issued, connection closed, state cleared.
        assert conn.execute.call_count >= 2  # lock + unlock
        conn.close.assert_called_once()
        assert scheduler._lock_conn is None

    def test_release_is_safe_when_no_lock_held(self):
        scheduler._lock_conn = None
        scheduler._release_singleton_lock()  # must not raise
        assert scheduler._lock_conn is None
