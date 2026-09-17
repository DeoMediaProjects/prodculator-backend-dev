"""Exercise the additive migration and handoff import against an isolated DB."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import alembic
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from scripts import stage_engine_handoffs
from scripts.stage_engine_handoffs import stage_handoffs

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/r7s8t9u0v1w2_engine_v2_staging_tables.py"
)
SNAPSHOTS = Path(__file__).resolve().parents[1] / "data/handoff_snapshots"


def _run_migration(engine: sa.Engine, direction: str) -> None:
    spec = importlib.util.spec_from_file_location(f"_engine_stage_{direction}", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    with engine.begin() as conn:
        previous = alembic.op
        alembic.op = Operations(MigrationContext.configure(conn))
        try:
            spec.loader.exec_module(module)
            getattr(module, direction)()
        finally:
            alembic.op = previous


def _engine(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'stage.db'}")
    metadata = sa.MetaData()
    sa.Table(
        "film_festivals",
        metadata,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text()),
    )
    metadata.create_all(engine)
    first_festival = json.loads(
        (SNAPSHOTS / "festivals_v2_1_2026-09-15.json").read_text(encoding="utf-8")
    )[0]
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO film_festivals (id, name) VALUES (:id, :name)"),
            {"id": first_festival["id"], "name": "Admin-edited name"},
        )
    _run_migration(engine, "upgrade")
    return engine


def _count(engine: sa.Engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def test_staging_is_dry_run_by_default_and_preserves_live_festival(tmp_path):
    engine = _engine(tmp_path)
    result = stage_handoffs(engine)
    assert result.festival_records == 380
    assert result.market_records == 203
    assert result.new_handoff_records == 583
    assert result.new_market_tracks == 203
    assert result.matched_live_festival_ids == 1
    assert result.unmatched_live_festival_ids == 0
    assert not result.applied
    assert _count(engine, "engine_handoff_records") == 0
    assert _count(engine, "market_tracks") == 0
    assert _count(engine, "film_festivals") == 1


def test_additive_migration_can_replay_without_changing_live_rows(tmp_path):
    engine = _engine(tmp_path)
    _run_migration(engine, "upgrade")
    assert _count(engine, "film_festivals") == 1
    assert _count(engine, "engine_handoff_records") == 0


def test_apply_is_additive_replay_safe_and_never_seeds_runtime_rules(tmp_path):
    engine = _engine(tmp_path)
    first = stage_handoffs(engine, apply=True)
    assert first.applied
    assert _count(engine, "engine_handoff_records") == 583
    assert _count(engine, "market_tracks") == 203
    assert _count(engine, "opportunity_cycles") == 0
    assert _count(engine, "opportunity_rules") == 0
    with engine.begin() as conn:
        assert (
            conn.execute(sa.text("SELECT name FROM film_festivals")).scalar_one()
            == "Admin-edited name"
        )
        conn.execute(
            sa.text("UPDATE market_tracks SET name = 'Admin-edited track' WHERE id = 'MLWIP-0001'")
        )
    replay = stage_handoffs(engine, apply=True)
    assert replay.new_handoff_records == 0
    assert replay.new_market_tracks == 0
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT name FROM market_tracks WHERE id = 'MLWIP-0001'")
            ).scalar_one()
            == "Admin-edited track"
        )


def test_changed_snapshot_conflicts_without_overwriting(tmp_path):
    engine = _engine(tmp_path)
    stage_handoffs(engine, apply=True)
    festivals = json.loads(
        (SNAPSHOTS / "festivals_v2_1_2026-09-15.json").read_text(encoding="utf-8")
    )
    festivals[0]["name"] = "Changed source name"
    with pytest.raises(ValueError, match="Staged source record changed"):
        stage_handoffs(engine, apply=True, festival_rows=festivals)
    assert _count(engine, "engine_handoff_records") == 583


def test_unreviewed_snapshot_bytes_are_rejected(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setitem(
        stage_engine_handoffs.SNAPSHOT_HASHES,
        "festivals_v2_1_2026-09-15.json",
        "0" * 64,
    )
    with pytest.raises(ValueError, match="content hash differs"):
        stage_handoffs(engine, apply=True)
    assert _count(engine, "engine_handoff_records") == 0


def test_downgrade_refuses_populated_staging_tables(tmp_path):
    engine = _engine(tmp_path)
    stage_handoffs(engine, apply=True)
    with pytest.raises(RuntimeError, match="Refusing to drop populated"):
        _run_migration(engine, "downgrade")
    assert _count(engine, "engine_handoff_records") == 583
