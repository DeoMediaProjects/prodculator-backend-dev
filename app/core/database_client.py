from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import MetaData, Table, and_, asc, create_engine, delete, desc, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import BinaryExpression

from app.core.config import Settings, get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.storage import StorageClient


@dataclass
class QueryResult:
    data: Any = None
    count: int | None = None


@dataclass
class _AuthUser:
    id: str
    email: str


@dataclass
class _AuthSession:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass
class AuthResponse:
    user: _AuthUser | None = None
    session: _AuthSession | None = None
    claims: dict[str, Any] | None = None


class _AdminAuth:
    def __init__(self, client: "DatabaseClient"):
        self.client = client

    def update_user_by_id(self, user_id: str, payload: dict[str, Any]) -> None:
        users = self.client._table("users")
        updates: dict[str, Any] = {}
        if "password" in payload:
            updates["password_hash"] = hash_password(payload["password"])
        if updates:
            stmt = update(users).where(users.c.id == user_id).values(**updates)
            self.client.session.execute(stmt)
            self.client.session.commit()


class AuthClient:
    def __init__(self, client: "DatabaseClient"):
        self.client = client
        self.admin = _AdminAuth(client)

    def sign_up(self, payload: dict[str, Any]) -> AuthResponse:
        email = (payload.get("email") or "").strip().lower()
        password = payload.get("password") or ""
        if not email or not password:
            raise ValueError("Email and password are required")

        users = self.client._table("users")
        existing = self.client.session.execute(
            select(users).where(func.lower(users.c.email) == email)
        ).first()
        if existing:
            raise ValueError("User already exists")

        user_id = str(uuid4())
        metadata = (payload.get("options") or {}).get("data") or {}
        insert_payload = {
            "id": user_id,
            "email": email,
            "password_hash": hash_password(password),
            "name": metadata.get("name") or None,
            "company": metadata.get("company") or None,
            "role": metadata.get("role") or None,
            "user_type": "free",
            "credits_remaining": 0,
            "plan": "free",
            "created_at": datetime.now(timezone.utc),
        }
        self.client.session.execute(insert(users).values(**insert_payload))
        self.client.session.commit()
        return self._issue_tokens(user_id, email, "free")

    def sign_in_with_password(self, payload: dict[str, Any]) -> AuthResponse:
        email = (payload.get("email") or "").strip().lower()
        password = payload.get("password") or ""
        users = self.client._table("users")

        row = self.client.session.execute(
            select(users).where(func.lower(users.c.email) == email)
        ).first()
        if not row:
            raise ValueError("Invalid email or password")
        user = dict(row._mapping)

        stored_hash = user.get("password_hash")
        if not stored_hash or not verify_password(password, stored_hash):
            raise ValueError("Invalid email or password")

        return self._issue_tokens(user["id"], user["email"], user.get("user_type", "free"))

    def sign_in_admin(self, email: str, password: str) -> AuthResponse:
        email = email.strip().lower()
        admins = self.client._table("admins")

        row = self.client.session.execute(
            select(admins).where(func.lower(admins.c.email) == email)
        ).first()
        if not row:
            raise ValueError("Invalid email or password")
        admin = dict(row._mapping)

        if not verify_password(password, admin["password_hash"]):
            raise ValueError("Invalid email or password")

        return self._issue_tokens(admin["id"], admin["email"], "admin")

    def get_admin(self, token: str) -> AuthResponse:
        claims = decode_token(token, self.client.settings)
        if claims.get("type") != "access":
            raise ValueError("Invalid or expired token")

        admins = self.client._table("admins")
        row = self.client.session.execute(
            select(admins).where(admins.c.id == claims["sub"])
        ).first()
        if not row:
            raise ValueError("Invalid or expired token")
        admin = dict(row._mapping)
        return AuthResponse(user=_AuthUser(id=admin["id"], email=admin["email"]), claims=claims)

    def refresh_admin_session(self, refresh_token: str) -> AuthResponse:
        claims = decode_token(refresh_token, self.client.settings)
        if claims.get("type") != "refresh":
            raise ValueError("Invalid refresh token")

        admins = self.client._table("admins")
        row = self.client.session.execute(
            select(admins).where(admins.c.id == claims["sub"])
        ).first()
        if not row:
            raise ValueError("Invalid refresh token")
        admin = dict(row._mapping)
        response = self._issue_tokens(admin["id"], admin["email"], "admin")
        response.claims = claims
        return response

    def sign_out(self) -> None:
        return None

    def get_user(self, token: str) -> AuthResponse:
        claims = decode_token(token, self.client.settings)
        if claims.get("type") != "access":
            raise ValueError("Invalid or expired token")

        users = self.client._table("users")
        row = self.client.session.execute(select(users).where(users.c.id == claims["sub"])).first()
        if not row:
            raise ValueError("Invalid or expired token")
        user = dict(row._mapping)
        return AuthResponse(user=_AuthUser(id=user["id"], email=user["email"]), claims=claims)

    def reset_password_email(self, _email: str, _options: dict[str, Any] | None = None) -> None:
        return None

    def resend(self, _payload: dict[str, Any]) -> None:
        return None

    def refresh_session(self, refresh_token: str) -> AuthResponse:
        claims = decode_token(refresh_token, self.client.settings)
        if claims.get("type") != "refresh":
            raise ValueError("Invalid refresh token")

        users = self.client._table("users")
        row = self.client.session.execute(select(users).where(users.c.id == claims["sub"])).first()
        if not row:
            raise ValueError("Invalid refresh token")
        user = dict(row._mapping)
        # Return old claims alongside new tokens so the caller can revoke the old refresh token
        response = self._issue_tokens(user["id"], user["email"], user.get("user_type", "free"))
        response.claims = claims
        return response

    def _issue_tokens(self, user_id: str, email: str, user_type: str = "free") -> AuthResponse:
        access_token, expires_in = create_access_token(user_id, user_type, self.client.settings)
        refresh_token = create_refresh_token(user_id, user_type, self.client.settings)
        return AuthResponse(
            user=_AuthUser(id=user_id, email=email),
            session=_AuthSession(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
            ),
        )


class TableQuery:
    def __init__(self, client: "DatabaseClient", table_name: str):
        self.client = client
        self.table_name = table_name
        self.table = client._table(table_name)

        self._action = "select"
        self._select_columns: list[str] | None = None
        self._count_requested = False
        self._count_head = False
        self._single = False
        self._filters: list[BinaryExpression] = []
        self._order_by: list[Any] = []
        self._limit: int | None = None
        self._offset: int | None = None
        self._payload: dict[str, Any] | None = None
        self._upsert_conflict: str | None = None

    def select(self, columns: str = "*", count: str | None = None, head: bool = False):
        if columns and columns != "*":
            self._select_columns = [c.strip() for c in columns.split(",") if c.strip()]
        self._count_requested = count == "exact"
        self._count_head = head
        return self

    def insert(self, payload: dict[str, Any]):
        self._action = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict[str, Any]):
        self._action = "update"
        self._payload = payload
        return self

    def upsert(self, payload: dict[str, Any], on_conflict: str | None = None):
        self._action = "upsert"
        self._payload = payload
        self._upsert_conflict = on_conflict
        return self

    def delete(self):
        self._action = "delete"
        return self

    def eq(self, key: str, value: Any):
        self._filters.append(self.table.c[key] == value)
        return self

    def gte(self, key: str, value: Any):
        self._filters.append(self.table.c[key] >= value)
        return self

    def lte(self, key: str, value: Any):
        self._filters.append(self.table.c[key] <= value)
        return self

    def in_(self, key: str, values: list[Any]):
        self._filters.append(self.table.c[key].in_(values))
        return self

    def ilike(self, key: str, value: str):
        self._filters.append(self.table.c[key].ilike(value))
        return self

    def order(self, key: str, desc: bool = False):
        self._order_by.append(self.table.c[key].desc() if desc else self.table.c[key].asc())
        return self

    def range(self, start: int, end: int):
        self._offset = start
        self._limit = max(0, end - start + 1)
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def single(self):
        self._single = True
        self._limit = 1
        return self

    def execute(self) -> QueryResult:
        if self._action == "select":
            return self._execute_select()
        if self._action == "insert":
            return self._execute_insert()
        if self._action == "update":
            return self._execute_update()
        if self._action == "upsert":
            return self._execute_upsert()
        if self._action == "delete":
            return self._execute_delete()
        raise ValueError(f"Unsupported action: {self._action}")

    def _apply_filters(self, stmt):
        if self._filters:
            stmt = stmt.where(and_(*self._filters))
        return stmt

    def _apply_order_and_limits(self, stmt):
        for order_expr in self._order_by:
            stmt = stmt.order_by(order_expr)
        if self._offset is not None:
            stmt = stmt.offset(self._offset)
        if self._limit is not None:
            stmt = stmt.limit(self._limit)
        return stmt

    def _project_row(self, row: dict[str, Any]) -> dict[str, Any]:
        if not self._select_columns:
            return row
        projected: dict[str, Any] = {}
        for col in self._select_columns:
            projected[col] = row.get(col)
        return projected

    def _execute_select(self) -> QueryResult:
        count_value: int | None = None
        if self._count_requested:
            count_stmt = self._apply_filters(select(func.count()).select_from(self.table))
            count_value = int(self.client.session.execute(count_stmt).scalar_one() or 0)
            if self._count_head:
                return QueryResult(data=None, count=count_value)

        stmt = self._apply_filters(select(self.table))
        stmt = self._apply_order_and_limits(stmt)
        rows = [self._project_row(dict(r._mapping)) for r in self.client.session.execute(stmt).all()]

        if self._single:
            return QueryResult(data=rows[0] if rows else None, count=count_value)
        return QueryResult(data=rows, count=count_value)

    def _execute_insert(self) -> QueryResult:
        if not self._payload:
            raise ValueError("Insert payload is required")
        stmt = insert(self.table).values(**self._payload)
        self.client.session.execute(stmt)
        self.client.session.commit()
        return self._reload_after_write(self._payload)

    def _execute_update(self) -> QueryResult:
        if self._payload is None:
            raise ValueError("Update payload is required")
        stmt = self._apply_filters(update(self.table).values(**self._payload))
        self.client.session.execute(stmt)
        self.client.session.commit()
        return self._reload_after_write(self._payload)

    def _execute_delete(self) -> QueryResult:
        stmt = self._apply_filters(delete(self.table))
        self.client.session.execute(stmt)
        self.client.session.commit()
        return QueryResult(data=[])

    def _execute_upsert(self) -> QueryResult:
        if not self._payload:
            raise ValueError("Upsert payload is required")

        if not self._upsert_conflict:
            return self._execute_insert()

        conflict_columns = [c.strip() for c in self._upsert_conflict.split(",") if c.strip()]

        # Generic upsert fallback: query by conflict keys, then update/insert.
        conflict_filters = [self.table.c[col] == self._payload.get(col) for col in conflict_columns]
        existing = self.client.session.execute(select(self.table).where(and_(*conflict_filters))).first()

        if existing:
            stmt = (
                update(self.table)
                .where(and_(*conflict_filters))
                .values(**self._payload)
            )
            self.client.session.execute(stmt)
        else:
            self.client.session.execute(insert(self.table).values(**self._payload))

        self.client.session.commit()
        return self._reload_after_write(self._payload)

    def _reload_after_write(self, payload: dict[str, Any]) -> QueryResult:
        if self._filters:
            stmt = self._apply_order_and_limits(self._apply_filters(select(self.table)))
        elif "id" in payload:
            stmt = select(self.table).where(self.table.c.id == payload["id"])
        else:
            stmt = select(self.table)
            for key in payload.keys():
                if key in self.table.c:
                    stmt = stmt.where(self.table.c[key] == payload[key])
                    break
            stmt = stmt.limit(1)

        rows = [self._project_row(dict(r._mapping)) for r in self.client.session.execute(stmt).all()]
        if self._single:
            return QueryResult(data=rows[0] if rows else None)
        return QueryResult(data=rows)


class DatabaseClient:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.metadata = MetaData()
        self.storage = StorageClient(self.settings)
        self.auth = AuthClient(self)

    def table(self, table_name: str) -> TableQuery:
        return TableQuery(self, table_name)

    def _table(self, table_name: str) -> Table:
        return Table(table_name, self.metadata, autoload_with=self.session.get_bind())


class _OwnedClient(DatabaseClient):
    def __init__(self, db_url: str):
        engine: Engine = create_engine(db_url, pool_pre_ping=True)
        self._session = Session(engine)
        super().__init__(self._session)

    def close(self):
        self.session.close()


def create_client(db_url: str | None = None, _unused_key: str | None = None) -> DatabaseClient:
    cfg = get_settings()
    if db_url:
        return _OwnedClient(db_url)
    return _OwnedClient(cfg.DB_URL)
