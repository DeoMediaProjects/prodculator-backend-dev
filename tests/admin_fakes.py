"""Shared in-memory fakes for admin router/service tests.

Mimics the subset of the Supabase query-builder the admin services rely on
(``eq`` / ``in_`` / ``ilike`` / ``order`` / ``range`` / ``limit`` / ``single`` /
``insert`` / ``update`` / ``delete`` plus ``count``+``head``) on top of a plain
in-memory dict store. Because the real service code runs against it unchanged,
the tests exercise genuine service logic and can surface real bugs.

Not collected by pytest (filename is not ``test_*``).
"""
from __future__ import annotations

from typing import Any


class FakeResult:
    def __init__(self, data: Any = None, count: int | None = None) -> None:
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, table_name: str, store: dict[str, list[dict]]) -> None:
        self.table_name = table_name
        self.store = store
        self.eq_filters: dict[str, Any] = {}
        self.in_filters: dict[str, list[Any]] = {}
        self.ilike_filters: list[tuple[str, str]] = []
        self._data: Any = None
        self._count = False
        self._head = False
        self._single = False
        self._delete = False
        self._offset = 0
        self._end: int | None = None
        self._limit: int | None = None

    # ── filters / projection ──────────────────────────────────────────────
    def select(self, *_args, **kwargs):
        if kwargs.get("count"):
            self._count = True
        if kwargs.get("head"):
            self._head = True
        return self

    def eq(self, key, value):
        self.eq_filters[key] = value
        return self

    def in_(self, key, values):
        self.in_filters[key] = list(values)
        return self

    def ilike(self, key, pattern):
        self.ilike_filters.append((key, str(pattern).strip("%")))
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def lte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start: int, end: int):
        self._offset = start
        self._end = end
        self._limit = None
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    # ── mutations ─────────────────────────────────────────────────────────
    def insert(self, payload):
        if isinstance(payload, list):
            payload = payload[0]
        row = {**payload}
        if "id" not in row:
            row["id"] = f"new-{len(self.store.get(self.table_name, [])) + 1}"
        self.store.setdefault(self.table_name, []).append(row)
        self._data = [row]
        return self

    def update(self, payload):
        self._data = dict(payload)
        return self

    def delete(self):
        # Deletion is deferred to execute() so chained .eq() filters apply.
        self._delete = True
        return self

    def single(self):
        self._single = True
        return self

    # ── execution ─────────────────────────────────────────────────────────
    def execute(self):
        if self._delete:
            rows = self._rows()
            self.store[self.table_name] = [
                r for r in self.store.get(self.table_name, []) if r not in rows
            ]
            if self._single:
                return FakeResult(data=rows[0] if rows else None)
            return FakeResult(data=rows)

        if self._count and self._head:
            return FakeResult(data=None, count=len(self._rows()))

        if isinstance(self._data, list):
            rows = self._data
        elif isinstance(self._data, dict):
            rows = self._rows()
            if rows:
                rows[0].update(self._data)
        else:
            rows = self._rows()

        if self._end is not None:
            rows = rows[self._offset : self._end + 1]
        elif self._limit is not None:
            rows = rows[self._offset : self._offset + self._limit]

        if self._single:
            return FakeResult(data=rows[0] if rows else None)
        return FakeResult(data=rows)

    def _rows(self) -> list[dict]:
        out = []
        for row in self.store.get(self.table_name, []):
            if not all(row.get(k) == v for k, v in self.eq_filters.items()):
                continue
            if not all(row.get(k) in vals for k, vals in self.in_filters.items()):
                continue
            if not all(
                row.get(k) is not None and needle.lower() in str(row.get(k)).lower()
                for k, needle in self.ilike_filters
            ):
                continue
            out.append(row)
        return out


class FakeSupabase:
    def __init__(self, store: dict[str, list[dict]] | None = None) -> None:
        self.store: dict[str, list[dict]] = store if store is not None else {}

    def table(self, name: str) -> FakeQuery:
        self.store.setdefault(name, [])
        return FakeQuery(name, self.store)
