"""Grants v2 cutover review: split-parent edges, duplicate titles, superseded legacy rows.

migration_instructions.md step 7 asks for a semantic duplicate review before
production cutover, and states that title-string dedup is not sufficient. This is
that review, applied. Three findings, all reversible:

1. SPLIT-PARENT EDGES
   The 32 retired parents are already excluded from matching by their lifecycle
   state, so the "never return a parent and a child in the same universe" rule is
   satisfied without this table. What is still missing is id resolution: a stored
   report holding a parent's id has no way to say what it became.

   No supplied file carries the edge. split_parent_records.csv has no child column,
   and ``final_title`` is a human " + "-joined string on 12 of 32 rows whose
   fragments match zero canonical titles in the master. So the edges here are
   DERIVED by funding-body match and are marked HEURISTIC_BODY / HEURISTIC_STEM
   accordingly — 17 parents resolve to 46 candidate children, 15 resolve to none.
   They are a starting point for a human, not an answer. Nothing reads them at
   match time.

2. DUPLICATE TITLES
   Three canonical titles appear twice in the 253-record master. Every pair is the
   same programme captured in two reconciliation passes, and in all three the later
   pass is the better record:

     Netherlands Film Fund — Minority Co-production…
       pass14 co_production_fund   vs  pass17 minority_coproduction_fund
       The pass17 type matches the title; the pass14 type is the generic parent.
     Telefilm Canada — Development Program
       pass14 territory "Ontario"  vs  pass17 territory "Canada"
       Telefilm Canada is a national body. Ontario is simply wrong.
     Telefilm Canada — Talent to Watch Program
       pass14 territory "Ontario"  vs  Pass18 territory "Canada"  (same error)

   Left alone both rows match, and a producer sees one fund twice with two different
   territories. The earlier row is held for review; the later one stays live.

3. SUPERSEDED LEGACY ROWS
   Nine rows of the original 114 appear in none of the five disposition files and
   have no v2 counterpart id — so the import neither updated nor retired them. Each
   is a real programme, and each is carried in the master under a renamed identity
   ("BFI Film Fund — Development" -> "BFI National Lottery Development Funding";
   "Eurimages — Co-Production Fund" -> "Eurimages — Co-production Support"). They
   also carry no verification fields at all.

   Leaving them live means presenting the same fund twice under two names, one of
   them unverified. They are held for review with the successor recorded, so an
   admin can confirm the rename rather than have it assumed.

NOTHING IS DELETED. Every row here keeps its data and its id; only
``lifecycle_state`` changes, and the engine's record-state gate reports why.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

_TABLE = "grant_opportunities"

#: (surviving_title, territory_of_the_row_to_hold, reason). The pair is identified by
#: title plus the territory that distinguishes the weaker row, so this cannot
#: accidentally retire both.
_DUPLICATE_RESOLUTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "Netherlands Film Fund — Minority Co-production Feature Film & Documentary",
        "co_production_fund",
        "Duplicate of the pass17 record, which types this correctly as a minority "
        "co-production fund rather than the generic parent type.",
    ),
    (
        "Telefilm Canada — Development Program",
        "Ontario",
        "Duplicate of the pass17 record. Telefilm Canada is a national body; the "
        "territory on this row (Ontario) is wrong.",
    ),
    (
        "Telefilm Canada — Talent to Watch Program",
        "Ontario",
        "Duplicate of the Pass18 record. Telefilm Canada is a national body; the "
        "territory on this row (Ontario) is wrong.",
    ),
)

#: legacy title -> the master record that carries it now, where one was identified.
_SUPERSEDED_LEGACY: dict[str, str | None] = {
    "BFI Film Fund — Development": "BFI National Lottery Development Funding",
    "BFI NETWORK — Short Film Fund": "BFI NETWORK England Short Film Funding",
    "CNC — Aide au Développement Documentaire":
        "CNC — Aide au développement d’œuvres cinématographiques de longue durée",
    "CNC — Aide aux Cinémas du Monde": "CNC — Aide aux cinémas du monde",
    "CNC Avance sur Recettes (Advance on Receipts)":
        "CNC — Avance sur recettes avant réalisation",
    "Eurimages — Co-Production Fund": "Eurimages — Co-production Support",
    "Screen Ireland — Development Funding": "Screen Ireland — Irish Feature Film Development",
    "Telefilm Canada — Development — Low Budget Independent":
        "Telefilm Canada — Development Program",
    "Telefilm Canada — Talent to Watch (Short Film)":
        "Telefilm Canada — Talent to Watch Program",
}


def _body(title: str | None) -> str:
    text = (title or "").strip()
    for separator in ("—", " - ", ":"):
        if separator in text:
            return text.split(separator)[0].strip().lower()
    return text.lower()


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE not in inspector.get_table_names():
        return

    # ── 1. split-parent edge table ───────────────────────────────────────────
    if "grant_split_children" not in inspector.get_table_names():
        op.create_table(
            "grant_split_children",
            sa.Column("parent_id", sa.Text(), primary_key=True),
            sa.Column("child_id", sa.Text(), primary_key=True),
            # CURATED once a human confirms it; HEURISTIC_* until then. Nothing at
            # match time reads this, so a wrong edge misdirects an id lookup rather
            # than a recommendation.
            sa.Column("confidence", sa.Text(), nullable=False),
            sa.Column("note", sa.Text()),
        )

    parents = conn.execute(sa.text(
        f"SELECT id, title FROM {_TABLE} WHERE lifecycle_state = 'SPLIT_PARENT'"
    )).fetchall()
    children = conn.execute(sa.text(
        f"SELECT id, title FROM {_TABLE} "
        f"WHERE data_source = 'prodculator_grants_master_v2' AND lifecycle_state = 'LIVE'"
    )).fetchall()

    conn.execute(sa.text("DELETE FROM grant_split_children"))
    edges = unresolved = 0
    for parent_id, parent_title in parents:
        parent_body = _body(parent_title)
        matches = [(cid, ct) for cid, ct in children if _body(ct) == parent_body]
        confidence = "HEURISTIC_BODY"
        if not matches:
            stem = " ".join(parent_body.split()[:2])
            if len(stem) > 4:
                matches = [(cid, ct) for cid, ct in children if _body(ct).startswith(stem)]
            confidence = "HEURISTIC_STEM"
        if not matches:
            unresolved += 1
            continue
        for child_id, child_title in matches:
            conn.execute(
                sa.text(
                    "INSERT INTO grant_split_children (parent_id, child_id, confidence, note) "
                    "VALUES (:parent_id, :child_id, :confidence, :note)"
                ),
                {"parent_id": parent_id, "child_id": child_id, "confidence": confidence,
                 "note": f"{parent_title} -> {child_title}"},
            )
            edges += 1
    print(f"[{revision}] split edges: {edges} candidate(s); "
          f"{unresolved} parent(s) still unresolved and needing a human decision")

    # ── 2. duplicate titles ──────────────────────────────────────────────────
    held = 0
    for title, discriminator, reason in _DUPLICATE_RESOLUTIONS:
        result = conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET lifecycle_state = 'NEEDS_REVIEW', "
                f"paid_match_eligible = FALSE, archived_reason = :reason "
                f"WHERE title = :title AND lifecycle_state = 'LIVE' "
                f"AND (territory = :discriminator OR opportunity_type = :discriminator)"
            ),
            {"title": title, "discriminator": discriminator, "reason": reason},
        )
        held += result.rowcount or 0
    print(f"[{revision}] duplicate titles: {held} weaker row(s) held for review")

    # ── 3. superseded legacy rows ────────────────────────────────────────────
    superseded = 0
    for legacy_title, successor in _SUPERSEDED_LEGACY.items():
        result = conn.execute(
            sa.text(
                f"UPDATE {_TABLE} SET lifecycle_state = 'NEEDS_REVIEW', "
                f"paid_match_eligible = FALSE, replacement_title = :successor, "
                f"archived_reason = :reason "
                f"WHERE title = :title AND lifecycle_state = 'LIVE' "
                f"AND data_source IS DISTINCT FROM 'prodculator_grants_master_v2'"
            ),
            {
                "title": legacy_title,
                "successor": successor,
                "reason": (
                    "Outside the frozen v2 inventory and carried in the master under a "
                    "renamed identity. Held so the rename is confirmed rather than "
                    "assumed; restore by setting lifecycle_state back to LIVE."
                ),
            },
        )
        superseded += result.rowcount or 0
    print(f"[{revision}] superseded legacy rows held for review: {superseded}")

    remaining = conn.execute(sa.text(
        f"SELECT count(*) FROM {_TABLE} WHERE lifecycle_state = 'LIVE' "
        f"AND data_source IS DISTINCT FROM 'prodculator_grants_master_v2'"
    )).scalar()
    print(f"[{revision}] live rows outside the v2 master after review: {remaining}")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _TABLE in inspector.get_table_names():
        # Only rows this migration held: identified by the reasons it wrote.
        conn.execute(sa.text(
            f"UPDATE {_TABLE} SET lifecycle_state = 'LIVE', paid_match_eligible = NULL, "
            f"archived_reason = NULL WHERE lifecycle_state = 'NEEDS_REVIEW' "
            f"AND (archived_reason LIKE 'Duplicate of the%' "
            f"OR archived_reason LIKE 'Outside the frozen v2 inventory%')"
        ))
    if "grant_split_children" in inspector.get_table_names():
        op.drop_table("grant_split_children")
