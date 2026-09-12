"""Grants Master v2: 253-record import, field-level verification, legacy reconciliation.

WHAT THIS DOES
--------------
1. Backs the current 114 rows up into ``grant_opportunities_v1_backup`` before
   touching anything (migration_instructions.md step 1).
2. Adds the v2 columns — routing, lifecycle, canonical status, field-level
   verification, per-project amount — all nullable, because null means "not stated"
   and a server default would invent an answer.
3. Loads the 253 master records into a staging table, converts their string-typed
   values into facts, and MERGES them into the live table.
4. Applies the dispositions: 23 ARCHIVED, 19 RECLASSIFIED, 32 SPLIT_PARENT rows are
   retained and marked, never deleted.
5. Persists the 88-row legacy id map so an old id in a stored report still resolves.

WHY IT MERGES RATHER THAN REPLACES
----------------------------------
This is the decision that matters most, and it is not what the brief implies.

The v2 master carries 20 fields. It has NO ``genre_tags``, NO ``continent``, NO
``budget_min_usd``/``budget_max_usd``, NO ``nationality_required``, NO
``co_production_required`` and NO ``eligible_regions``. Every one of those is an input
the matching engine scores or gates on.

So a straight replace would import better identity, verification and status data while
destroying the structured fields the engine reasons with — genre overlap, continent
affinity, verified budget fit, the nationality gate and the PROD-FIX-008 regional gate
would all go inert, and the engine would quietly degrade to four signals. The 114
existing rows are the only place that structured data exists.

The merge therefore takes v2 as authoritative for everything v2 states, and leaves the
legacy structured columns untouched where v2 is silent. New records simply have those
fields null, which reads as "not stated" and leaves the corresponding gates untested —
correct, and visible as a data gap rather than a silent scoring skew.

IDS
---
Ids are reconciled in three steps so existing references survive:
  1. the legacy map's ``resolved_live_id`` (or ``existing_id``) where the programme
     survived under a mapped identity;
  2. otherwise ``uuid5(NAMESPACE_URL, "prodculator:grant:{territory}:{title}")`` —
     byte-identical to the scheme that produced the current 114 ids, so a record whose
     territory and title are unchanged silently re-inherits its live id;
  3. and where that key collides inside the master, the opportunity type is appended.
     This fires exactly once, for the two Netherlands Film Fund minority co-production
     rows that differ only by type.

Revision ID: e1f2a3b4c5d6
Revises: d2e3f4a5b6c7
"""
from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

_DATA = Path(__file__).resolve().parent.parent / "data" / "grants_v2"
_TABLE = "grant_opportunities"
_BACKUP = "grant_opportunities_v1_backup"
_DATA_SOURCE = "prodculator_grants_master_v2"

#: Added by this migration. All nullable except the two that carry a safe default:
#: an unmarked row is a live grants record, which is what every existing row is.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, dict], ...] = (
    ("canonical_title", sa.Text(), {}),
    ("opportunity_type", sa.Text(), {}),
    ("routing", sa.Text(), {"server_default": "GRANTS_FUNDS"}),
    ("lifecycle_state", sa.Text(), {"server_default": "LIVE"}),
    ("current_status_raw", sa.Text(), {}),
    ("canonical_status", sa.Text(), {}),
    ("status_window_opens_at", sa.Date(), {}),
    ("status_window_closes_at", sa.Date(), {}),
    ("next_deadline", sa.Text(), {}),
    ("next_deadline_date", sa.Date(), {}),
    ("next_deadline_kind", sa.Text(), {}),
    ("max_amount_text", sa.Text(), {}),
    ("max_amount_value", sa.Numeric(18, 2), {}),
    ("max_amount_is_pool", sa.Boolean(), {}),
    ("eligibility_summary", sa.Text(), {}),
    ("key_rule", sa.Text(), {}),
    ("official_source", sa.Text(), {}),
    ("record_verified", sa.Boolean(), {}),
    ("official_source_verified", sa.Boolean(), {}),
    ("current_cycle_verified", sa.Boolean(), {}),
    ("verification_state", sa.Text(), {}),
    ("paid_match_eligible", sa.Boolean(), {}),
    ("source_pass", sa.Text(), {}),
    ("v2_imported_at", sa.DateTime(), {}),
    ("v2_migration_action", sa.Text(), {}),
    ("archived_reason", sa.Text(), {}),
    ("replacement_title", sa.Text(), {}),
    ("target_dataset", sa.Text(), {}),
    # The funder's own wording, kept verbatim. ``eligible_formats`` is a JSON column
    # and the master states formats as prose ("Fiction; Documentary/Unscripted;
    # Online", "Film; Television"), which is not JSON and would not load at all. So the
    # parsed canonical list goes into the JSON column the engine gates on, and the
    # source sentence is preserved here — Developer Guide §3: "Parse canonical values;
    # retain source wording."
    ("eligible_formats_raw", sa.Text(), {}),
    ("production_stage_raw", sa.Text(), {}),
)


def _existing_columns(conn, table: str) -> set[str]:
    inspector = sa.inspect(conn)
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _is_unknown(value) -> bool:
    """Blank, "[]" and None all mean "not stated" in this dataset."""
    if value is None:
        return True
    return str(value).strip().lower() in ("", "[]", "{}", "none", "null", "n/a")


def _to_bool(value):
    if _is_unknown(value):
        return None
    return str(value).strip().lower() in ("true", "t", "yes", "y", "1")


def _to_date(value):
    """Accepts both stored spellings: 'YYYY-MM-DD' and 'YYYY-MM-DDTHH:MM:SS'."""
    if _is_unknown(value):
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _load_master() -> list[dict]:
    payload = json.loads((_DATA / "grants_master_v2.json").read_text(encoding="utf-8"))
    records = payload["records"]
    # The count is asserted rather than trusted: a truncated data file would otherwise
    # import silently and look like a smaller database.
    assert len(records) == payload["record_count"] == 253, (
        f"expected 253 master records, found {len(records)}"
    )
    return records


def _load_csv(name: str) -> list[dict]:
    with (_DATA / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _assign_ids(records: list[dict], legacy_rows: list[dict]) -> dict[int, str]:
    """Index in the master -> the id that record will carry."""
    # (territory, title) -> the live id that survives for it.
    by_identity: dict[tuple[str, str], str] = {}
    for row in legacy_rows:
        resolved = (row.get("resolved_live_id") or "").strip()
        existing = (row.get("existing_id") or "").strip()
        survivor = resolved or existing
        # A SPLIT parent with no resolved id has no successor; reusing existing_id
        # here would resurrect a record the contract says to retire.
        if not resolved and "SPLIT" in (row.get("decision") or "").upper():
            continue
        if survivor:
            key = ((row.get("territory") or "").strip(),
                   (row.get("existing_title") or "").strip())
            by_identity.setdefault(key, survivor)

    def natural_key(record: dict) -> str:
        return (f"prodculator:grant:{record.get('territory', '')}:"
                f"{record.get('canonical_title', '')}")

    seen: dict[str, int] = {}
    for record in records:
        seen[natural_key(record)] = seen.get(natural_key(record), 0) + 1
    colliding = {key for key, count in seen.items() if count > 1}

    assigned: dict[int, str] = {}
    for index, record in enumerate(records):
        identity = ((record.get("territory") or "").strip(),
                    (record.get("canonical_title") or "").strip())
        if identity in by_identity:
            assigned[index] = by_identity[identity]
            continue
        key = natural_key(record)
        if key in colliding:
            key = f"{key}:{record.get('opportunity_type', '')}"
        assigned[index] = str(uuid.uuid5(uuid.NAMESPACE_URL, key))

    assert len(set(assigned.values())) == len(records), (
        "id assignment produced duplicates — refusing to import"
    )
    return assigned


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in inspector.get_table_names():
        return

    # ── 1. back up before anything ───────────────────────────────────────────
    if _BACKUP not in inspector.get_table_names():
        conn.execute(sa.text(
            f"CREATE TABLE {_BACKUP} AS SELECT * FROM {_TABLE}"
        ))
        kept = conn.execute(sa.text(f"SELECT count(*) FROM {_BACKUP}")).scalar()
        print(f"[{revision}] backed up {kept} legacy rows into {_BACKUP}")

    # ── 2. columns ───────────────────────────────────────────────────────────
    present = _existing_columns(conn, _TABLE)
    added = 0
    for name, type_, kwargs in _NEW_COLUMNS:
        if name in present:
            continue
        op.add_column(_TABLE, sa.Column(name, type_, nullable=True, **kwargs))
        added += 1
    print(f"[{revision}] added {added} v2 column(s)")

    # ── 3. supporting tables ─────────────────────────────────────────────────
    if "grant_legacy_id_map" not in inspector.get_table_names():
        op.create_table(
            "grant_legacy_id_map",
            sa.Column("legacy_id", sa.Text(), primary_key=True),
            sa.Column("legacy_title", sa.Text()),
            sa.Column("legacy_territory", sa.Text()),
            sa.Column("decision", sa.Text()),
            sa.Column("resolved_live_id", sa.Text()),
            sa.Column("verification_status", sa.Text()),
            sa.Column("canonical_action", sa.Text()),
            sa.Column("official_source", sa.Text()),
            sa.Column("imported_at", sa.DateTime()),
        )

    # ── 4. import the master ─────────────────────────────────────────────────
    from app.modules.grants.normalise import parse_format_list, parse_stage_list

    records = _load_master()
    legacy_rows = _load_csv("legacy_id_mapping.csv")
    ids = _assign_ids(records, legacy_rows)
    now = datetime.utcnow()

    live_ids = {
        row[0] for row in conn.execute(sa.text(f"SELECT id FROM {_TABLE}")).fetchall()
    }

    inserted = updated = 0
    for index, record in enumerate(records):
        grant_id = ids[index]
        title = (record.get("canonical_title") or "").strip()
        raw_status = record.get("current_status")
        deadline_raw = record.get("next_deadline")
        amount_raw = record.get("max_amount")

        # Only a purely numeric amount becomes a number. The prose values include
        # explicit anti-instructions ("do not hard-code EUR 1.2m without current
        # official cap evidence") whose first number is exactly the figure the source
        # forbids using.
        amount_text = None if _is_unknown(amount_raw) else str(amount_raw).strip()
        amount_value = None
        if amount_text and amount_text.replace(",", "").replace(" ", "").isdigit():
            amount_value = float(amount_text.replace(",", "").replace(" ", ""))

        values = {
            "canonical_title": title or None,
            "title": title or None,
            "opportunity_type": (record.get("opportunity_type") or "").strip() or None,
            "routing": (record.get("routing") or "GRANTS_FUNDS").strip() or "GRANTS_FUNDS",
            "current_status_raw": None if _is_unknown(raw_status) else str(raw_status).strip(),
            "next_deadline": None if _is_unknown(deadline_raw) else str(deadline_raw).strip(),
            "next_deadline_date": _to_date(deadline_raw),
            "eligibility_summary": (record.get("eligibility_summary") or "").strip() or None,
            "key_rule": (record.get("key_rule") or "").strip() or None,
            "official_source": (record.get("official_source") or "").strip() or None,
            "record_verified": _to_bool(record.get("record_verified")),
            "official_source_verified": _to_bool(record.get("official_source_verified")),
            "current_cycle_verified": _to_bool(record.get("current_cycle_verified")),
            "paid_match_eligible": _to_bool(record.get("paid_match_eligible")) or False,
            "source_pass": (record.get("source_pass") or "").strip() or None,
            "max_amount_text": amount_text,
            "max_amount_value": amount_value,
            "last_verified_at": _to_date(record.get("last_verified_at")),
            "data_source": _DATA_SOURCE,
            "v2_imported_at": now,
            "lifecycle_state": "LIVE",
        }
        # ``eligible_formats`` is a JSON column, so only the parsed canonical list may
        # go in it. Prose that names no recognised format parses to None, which reads
        # as "not stated" and leaves the format gate untested — the safe direction.
        formats = parse_format_list(record.get("eligible_formats"))
        stages = parse_stage_list(record.get("production_stage"))

        # v2 is silent on these, and silence is not an instruction to erase. Set only
        # where the record is new; an existing row keeps whatever it already holds.
        optional = {
            "funding_body": (record.get("funding_body") or "").strip() or None,
            "territory": (record.get("territory") or "").strip() or None,
            "currency": (record.get("currency") or "").strip() or None,
            "production_stage": ", ".join(stages) if stages else None,
            "eligible_formats": json.dumps(formats) if formats else None,
            "eligible_formats_raw": (record.get("eligible_formats") or "").strip() or None,
            "production_stage_raw": (record.get("production_stage") or "").strip() or None,
        }

        if grant_id in live_ids:
            payload = {k: v for k, v in values.items() if v is not None}
            for key, value in optional.items():
                if value is not None:
                    payload[key] = value
            assignments = ", ".join(f"{k} = :{k}" for k in payload)
            conn.execute(
                sa.text(f"UPDATE {_TABLE} SET {assignments} WHERE id = :row_id"),
                {**payload, "row_id": grant_id},
            )
            updated += 1
        else:
            payload = {**values, **optional, "id": grant_id}
            payload = {k: v for k, v in payload.items() if v is not None}
            columns = ", ".join(payload)
            placeholders = ", ".join(f":{k}" for k in payload)
            conn.execute(
                sa.text(f"INSERT INTO {_TABLE} ({columns}) VALUES ({placeholders})"),
                payload,
            )
            inserted += 1
    print(f"[{revision}] master import: {inserted} inserted, {updated} merged")

    # ── 5. dispositions: retained, marked, never deleted ─────────────────────
    disposition_counts: dict[str, int] = {}
    for row in _load_csv("dispositions.csv"):
        row_id = (row.get("id") or "").strip()
        if not row_id or row_id not in live_ids:
            # 'FR-CNC-A4D' is a synthetic key that was never in the table; an UPDATE
            # keyed on it would silently affect zero rows and report success.
            continue
        state = (row.get("lifecycle_state") or "").strip()
        conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET lifecycle_state = :state, "
                f"paid_match_eligible = :paid, v2_migration_action = :action, "
                f"archived_reason = :reason, replacement_title = :replacement, "
                f"target_dataset = :target WHERE id = :row_id"
            ),
            {
                "state": state, "paid": False,
                "action": (row.get("action") or "").strip() or None,
                "reason": (row.get("reason") or "").strip() or None,
                "replacement": (row.get("replacement_title") or "").strip() or None,
                "target": (row.get("target_dataset") or "").strip() or None,
                "row_id": row_id,
            },
        )
        disposition_counts[state] = disposition_counts.get(state, 0) + 1
    print(f"[{revision}] dispositions applied: {disposition_counts}")

    # ── 6. legacy id map ─────────────────────────────────────────────────────
    conn.execute(sa.text("DELETE FROM grant_legacy_id_map"))
    for row in legacy_rows:
        legacy_id = (row.get("existing_id") or "").strip()
        if not legacy_id:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO grant_legacy_id_map (legacy_id, legacy_title, "
                "legacy_territory, decision, resolved_live_id, verification_status, "
                "canonical_action, official_source, imported_at) VALUES "
                "(:legacy_id, :title, :territory, :decision, :resolved, :verification, "
                ":action, :source, :imported_at)"
            ),
            {
                "legacy_id": legacy_id,
                "title": (row.get("existing_title") or "").strip() or None,
                "territory": (row.get("territory") or "").strip() or None,
                "decision": (row.get("decision") or "").strip() or None,
                "resolved": (row.get("resolved_live_id") or "").strip() or None,
                "verification": (row.get("verification_status") or "").strip() or None,
                "action": (row.get("canonical_action") or "").strip() or None,
                "source": (row.get("official_source") or "").strip() or None,
                "imported_at": now,
            },
        )
    print(f"[{revision}] legacy id map: {len(legacy_rows)} rows")

    # ── 7. status normalisation, and the stale seeded status ─────────────────
    # canonical_status is computed by the engine at request time from
    # current_status_raw. It is materialised here too so an admin query can filter on
    # it, but the engine never trusts the stored value over its own recomputation —
    # that is the whole point of Logic Guide §2.
    from app.modules.grants.normalise import canonical_status, status_window

    rows = conn.execute(sa.text(
        f"SELECT id, current_status_raw, status FROM {_TABLE}"
    )).fetchall()
    for row_id, raw, legacy_status in rows:
        source = raw if not _is_unknown(raw) else legacy_status
        opens, closes = status_window(source)
        conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET canonical_status = :status, "
                f"status_window_opens_at = :opens, status_window_closes_at = :closes "
                f"WHERE id = :row_id"
            ),
            {"status": canonical_status(source), "opens": opens, "closes": closes,
             "row_id": row_id},
        )

    # days_until_deadline is a stored countdown that is wrong the day after it is
    # written. Developer Guide §12 requires it to be computed dynamically, so the
    # column is cleared rather than refreshed.
    conn.execute(sa.text(f"UPDATE {_TABLE} SET days_until_deadline = NULL"))

    # ── 8. assertions ────────────────────────────────────────────────────────
    total = conn.execute(sa.text(f"SELECT count(*) FROM {_TABLE}")).scalar()
    distinct = conn.execute(sa.text(f"SELECT count(DISTINCT id) FROM {_TABLE}")).scalar()
    assert total == distinct, f"duplicate ids after import: {total} rows, {distinct} ids"
    imported = conn.execute(sa.text(
        f"SELECT count(*) FROM {_TABLE} WHERE data_source = :src"
    ), {"src": _DATA_SOURCE}).scalar()
    assert imported == 253, f"expected 253 v2 records, found {imported}"
    unmapped = conn.execute(sa.text(
        f"SELECT count(*) FROM {_TABLE} WHERE canonical_status IS NULL"
    )).scalar()
    assert unmapped == 0, f"{unmapped} rows have no canonical status"
    print(f"[{revision}] verified: {total} rows, {imported} from the v2 master")


def downgrade() -> None:
    """Restores the 114-row backup and drops the v2 columns.

    The backup table is left in place. Dropping it would discard the only copy of the
    pre-migration state, which is the opposite of what a downgrade is for.
    """
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Columns first. `INSERT INTO live SELECT * FROM backup` matches by position, so
    # restoring while the live table still carries the extra v2 columns would fail on
    # the column count — or worse, on a permissive backend, shift every value one
    # column to the left.
    present = _existing_columns(conn, _TABLE)
    for name, _type, _kwargs in _NEW_COLUMNS:
        if name in present:
            op.drop_column(_TABLE, name)

    if _BACKUP in inspector.get_table_names() and _TABLE in inspector.get_table_names():
        conn.execute(sa.text(f"DELETE FROM {_TABLE}"))
        conn.execute(sa.text(f"INSERT INTO {_TABLE} SELECT * FROM {_BACKUP}"))

    if "grant_legacy_id_map" in inspector.get_table_names():
        op.drop_table("grant_legacy_id_map")
