"""fix_five_systematic_issues

Revision ID: o6p7q8r9s0t1
Revises: m4n5o6p7q8r9
Create Date: 2026-03-24

Fixes five systematic issues identified across the incentive programmes,
comparable productions, and schema:

 1. Add cap_basis column to incentive_programs + set IFTC cap_basis = 'core_costs'.
    The UK Independent Film Tax Credit (IFTC) calculates its cap against core
    production costs only (not total budget).  A new nullable TEXT column
    cap_basis is added; NULL means the default 'total_budget' behaviour applies.

 2. Add atl_exempt column to incentive_programs + set AVEC atl_exempt = true.
    AVEC (Audio-Visual Expenditure Credit) replaced Film Tax Relief from
    1 January 2024.  Unlike FTR, AVEC applies at a flat 34% to ALL qualifying
    UK expenditure with NO ATL/BTL distinction.
    Source: HMRC AVEC guidance.

 3. Mark stacking sub-territory credits as is_supplementary = true.
    10 programmes all have stackable_with pointing to a national/federal
    programme, meaning they stack ON TOP of the primary national incentive
    rather than being standalone.  Flagging is_supplementary = true makes this
    machine-readable so the report engine can model stacking correctly.

 4. Fix Joker comparable territory: 'Canada' → 'United States'.
    Joker (2019) was filmed in Newark NJ and New York City — not Canada.

 5. Create visa_requirements table + seed UK and US crew visa data for all
    territories that appear in incentive_programs.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "o6p7q8r9s0t1"
down_revision = "m4n5o6p7q8r9"
branch_labels = None
depends_on = None

# ── 3. Sub-territory supplementary programmes ─────────────────────────────────
_SUPPLEMENTARY_PROGRAMS = [
    ("British Columbia", "BC Production Services Tax Credit (PSTC)"),
    ("British Columbia", "BC Film Incentive BC Tax Credit (FIBC)"),
    ("Ontario", "Ontario OPSTC (Production Services Tax Credit)"),
    ("Quebec", "Quebec QPSTC (Production Services Tax Credit)"),
    ("New South Wales", "NSW Made in NSW Fund"),
    ("Bavaria", "FFF Bayern (Bavaria Film Fund)"),
    ("Western Cape", "Western Cape Film Commission Production Incentive"),
    ("Northern Ireland", "Northern Ireland Screen Fund"),
    ("Scotland", "Creative Scotland Production Growth Fund"),
    ("Wales", "Ffilm Cymru Wales Production Fund"),
]

# ── 5. Visa requirements seed data ────────────────────────────────────────────
# (base_country, destination, visa_required, work_permit_required, notes)
_VISA_ROWS = [
    # ── UK nationals ─────────────────────────────────────────────────────────
    (
        "United Kingdom", "Australia", False, True,
        "No visa required for UK nationals for stays up to 90 days (Electronic Travel Authority). "
        "Film crew work permits are separate — apply through Screen Australia/Home Affairs. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "British Columbia", False, True,
        "UK nationals do not require a visa for Canada (eTA required, C$7, applied online). "
        "Film crew work permits required for commercial productions — apply via IRCC. Allow 8–12 weeks.",
    ),
    (
        "United Kingdom", "Canada", False, True,
        "UK nationals do not require a visa for Canada (eTA required, C$7, applied online). "
        "Film crew work permits required for commercial productions — apply via IRCC. Allow 8–12 weeks.",
    ),
    (
        "United Kingdom", "Czech Republic", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for film crew employed in Czechia. Allow 4–6 weeks.",
    ),
    (
        "United Kingdom", "France", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit or short-stay work authorisation required for commercial crew. Allow 4–6 weeks.",
    ),
    (
        "United Kingdom", "Georgia (USA)", False, True,
        "UK nationals do not require a visa for the United States — ESTA (Electronic System for Travel Authorization) "
        "required (USD $21, valid 2 years). O-1B visa (individuals with extraordinary ability) or O-2 "
        "(essential support crew) typically required for employed film crew. Allow 8–16 weeks for O visa.",
    ),
    (
        "United Kingdom", "Germany", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "Hungary", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "Iceland", False, True,
        "Visa-free for UK nationals (EEA/Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "Ireland", False, False,
        "No visa required for UK nationals — Common Travel Area. "
        "No immigration controls between UK and Ireland. Work rights apply for UK nationals.",
    ),
    (
        "United Kingdom", "Louisiana", False, True,
        "UK nationals do not require a visa for the United States — ESTA required (USD $21). "
        "O-1B/O-2 visa typically required for employed film crew. Allow 8–16 weeks for O visa.",
    ),
    (
        "United Kingdom", "Malta", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "Morocco", False, True,
        "UK nationals do not require a visa for Morocco for stays up to 90 days. "
        "Work permit required for film crew. Apply through local production services company. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "Netherlands", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "New Mexico", False, True,
        "UK nationals do not require a visa for the United States — ESTA required. "
        "O-1B/O-2 visa typically required for employed film crew. Allow 8–16 weeks.",
    ),
    (
        "United Kingdom", "New South Wales", False, True,
        "No visa required for UK nationals for stays up to 90 days (eTA required). "
        "Film crew work permits are separate — apply through Home Affairs. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "New York", False, True,
        "UK nationals do not require a visa for the United States — ESTA required. "
        "O-1B/O-2 visa typically required for employed film crew. Allow 8–16 weeks.",
    ),
    (
        "United Kingdom", "New Zealand", False, True,
        "No visa required for UK nationals for stays up to 6 months (NZeTA required, NZD $17 online). "
        "Film crew work permits required. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "Northern Ireland", False, False,
        "UK nationals — no visa, no immigration controls. Northern Ireland is part of the United Kingdom.",
    ),
    (
        "United Kingdom", "Ontario", False, True,
        "UK nationals do not require a visa for Canada (eTA required). "
        "Film crew work permits required. Allow 8–12 weeks.",
    ),
    (
        "United Kingdom", "Quebec", False, True,
        "UK nationals do not require a visa for Canada (eTA required). "
        "Film crew work permits required. Allow 8–12 weeks.",
    ),
    (
        "United Kingdom", "Scotland", False, False,
        "UK nationals — no visa required. Scotland is part of the United Kingdom.",
    ),
    (
        "United Kingdom", "Serbia", False, True,
        "UK nationals do not require a visa for Serbia for stays up to 90 days. "
        "Work permit required for film crew. Apply through local production services company. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "South Africa", False, True,
        "UK nationals do not require a visa for South Africa for stays up to 30 days. "
        "Film crew work permits (General Work Visa) required for extended shoots — apply through "
        "South African High Commission. Allow 6–12 weeks.",
    ),
    (
        "United Kingdom", "Spain", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "United Kingdom", False, False,
        "UK nationals — no visa required. Home territory.",
    ),
    (
        "United Kingdom", "United States", False, True,
        "UK nationals do not require a visa for the United States — ESTA required (USD $21, applied online, "
        "valid 2 years). O-1B visa (extraordinary ability) or O-2 (essential support crew) typically required "
        "for employed film crew working in the US. Allow 8–16 weeks for O visa applications.",
    ),
    (
        "United Kingdom", "Wales", False, False,
        "UK nationals — no visa required. Wales is part of the United Kingdom.",
    ),
    (
        "United Kingdom", "Western Cape", False, True,
        "UK nationals do not require a visa for South Africa for stays up to 30 days. "
        "Film crew work permits required for extended shoots. Apply through South African High Commission. "
        "Allow 6–12 weeks.",
    ),
    (
        "United Kingdom", "California", False, True,
        "UK nationals do not require a visa for the United States — ESTA required. "
        "O-1B/O-2 visa typically required for employed film crew. Allow 8–16 weeks.",
    ),
    (
        "United Kingdom", "Illinois", False, True,
        "UK nationals do not require a visa for the United States — ESTA required. "
        "O-1B/O-2 visa typically required for employed film crew. Allow 8–16 weeks.",
    ),
    (
        "United Kingdom", "Bavaria", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew in Germany. Allow 4–8 weeks.",
    ),
    (
        "United Kingdom", "Portugal", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–6 weeks.",
    ),
    (
        "United Kingdom", "Belgium", False, True,
        "Visa-free for UK nationals (Schengen, 90 days in 180). "
        "Work permit required for employed film crew. Allow 4–6 weeks.",
    ),
    # ── US nationals ──────────────────────────────────────────────────────────
    (
        "United States", "Australia", False, True,
        "No visa required for US nationals for stays up to 90 days (ETA required). "
        "Film crew work permits required — apply through Home Affairs. Allow 4–8 weeks.",
    ),
    (
        "United States", "Canada", False, True,
        "US nationals do not require a visa or eTA for Canada. "
        "Film crew work permits required for commercial productions. Allow 8–12 weeks.",
    ),
    (
        "United States", "British Columbia", False, True,
        "US nationals do not require a visa or eTA for Canada. "
        "Film crew work permits required. Allow 8–12 weeks.",
    ),
    (
        "United States", "France", False, True,
        "Visa-free for US nationals (Schengen, 90 days in 180). "
        "Work permit required for employed crew. Allow 4–6 weeks.",
    ),
    (
        "United States", "Germany", False, True,
        "Visa-free for US nationals (Schengen, 90 days in 180). "
        "Work permit required for employed crew. Allow 4–8 weeks.",
    ),
    (
        "United States", "Hungary", False, True,
        "Visa-free for US nationals (Schengen, 90 days in 180). "
        "Work permit required for employed crew. Allow 4–8 weeks.",
    ),
    (
        "United States", "Ireland", False, True,
        "Visa-free for US nationals (90 days). "
        "Work rights require separate authorisation for crew. Allow 4–8 weeks.",
    ),
    (
        "United States", "New Zealand", False, True,
        "No visa required for US nationals for stays up to 3 months (NZeTA required). "
        "Film crew work permits required. Allow 4–8 weeks.",
    ),
    (
        "United States", "South Africa", False, True,
        "US nationals do not require a visa for South Africa for stays up to 30 days. "
        "Film crew work permits required for extended shoots. Allow 6–12 weeks.",
    ),
    (
        "United States", "United Kingdom", False, True,
        "US nationals do not require a visa for the UK for stays up to 6 months. "
        "Film crew work authorisation required for commercial productions — apply through UK Home Office. "
        "Allow 4–8 weeks.",
    ),
]

_VISA_SOURCE = "UK FCDO / US State Department / relevant embassy guidelines"
_VISA_VERIFIED = "2026-03-24"

_VISA_INSERT_SQL = """\
INSERT INTO visa_requirements (
    base_country, destination, visa_required, work_permit_required,
    notes, source, last_verified_at, created_at
) VALUES (
    :base_country, :destination, :visa_required, :work_permit_required,
    :notes, :source, :last_verified_at, NOW()
)
ON CONFLICT (base_country, destination) DO NOTHING
"""


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── 1. Add cap_basis column ────────────────────────────────────────────────
    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}
    if "cap_basis" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("cap_basis", sa.Text(), nullable=True),
        )

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET cap_basis        = :cap_basis,
            last_verified_at = '2026-03-24'
        WHERE territory = :territory
          AND program   = :program
          AND status    = :status
    """), {
        "cap_basis": "core_costs",
        "territory": "United Kingdom",
        "program": "UK Independent Film Tax Credit (IFTC)",
        "status": "active",
    })

    # ── 2. Add atl_exempt column ───────────────────────────────────────────────
    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}
    if "atl_exempt" not in existing_cols:
        op.add_column(
            "incentive_programs",
            sa.Column("atl_exempt", sa.Boolean(), nullable=True),
        )

    conn.execute(sa.text("""
        UPDATE incentive_programs
        SET atl_exempt       = true,
            last_verified_at = '2026-03-24'
        WHERE territory = :territory
          AND program   = :program
          AND status    = :status
    """), {
        "territory": "United Kingdom",
        "program": "Audio-Visual Expenditure Credit (AVEC)",
        "status": "active",
    })

    # ── 3. Mark sub-territory stacking programmes as supplementary ─────────────
    for territory, program in _SUPPLEMENTARY_PROGRAMS:
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET is_supplementary = true,
                last_verified_at = '2026-03-24'
            WHERE territory = :territory
              AND program   = :program
        """), {"territory": territory, "program": program})

    # ── 4. Fix Joker comparable territory ─────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE comparable_productions
        SET primary_territory = :territory,
            updated_at        = NOW()
        WHERE LOWER(title) = :title
    """), {"territory": "United States", "title": "joker"})

    # ── 5. Create visa_requirements table + seed data ──────────────────────────
    if "visa_requirements" not in inspector.get_table_names():
        op.create_table(
            "visa_requirements",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("base_country", sa.Text(), nullable=False),
            sa.Column("destination", sa.Text(), nullable=False),
            sa.Column("visa_required", sa.Boolean(), nullable=True),
            sa.Column(
                "work_permit_required",
                sa.Boolean(),
                nullable=True,
                server_default=sa.text("true"),
            ),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("source", sa.Text(), nullable=True),
            sa.Column("last_verified_at", sa.Date(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.UniqueConstraint("base_country", "destination", name="uq_visa_requirements_base_dest"),
        )

    for base_country, destination, visa_required, work_permit_required, notes in _VISA_ROWS:
        conn.execute(sa.text(_VISA_INSERT_SQL), {
            "base_country": base_country,
            "destination": destination,
            "visa_required": visa_required,
            "work_permit_required": work_permit_required,
            "notes": notes,
            "source": _VISA_SOURCE,
            "last_verified_at": _VISA_VERIFIED,
        })


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # ── 5. Drop visa_requirements table ───────────────────────────────────────
    if "visa_requirements" in inspector.get_table_names():
        op.drop_table("visa_requirements")

    # ── 4. Revert Joker primary_territory ─────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE comparable_productions
        SET primary_territory = :territory,
            updated_at        = NOW()
        WHERE LOWER(title) = :title
    """), {"territory": "Canada", "title": "joker"})

    # ── 3. Revert sub-territory stacking programmes ────────────────────────────
    for territory, program in _SUPPLEMENTARY_PROGRAMS:
        conn.execute(sa.text("""
            UPDATE incentive_programs
            SET is_supplementary = false
            WHERE territory = :territory
              AND program   = :program
        """), {"territory": territory, "program": program})

    # ── 2. Drop atl_exempt column ──────────────────────────────────────────────
    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}
    if "atl_exempt" in existing_cols:
        op.drop_column("incentive_programs", "atl_exempt")

    # ── 1. Drop cap_basis column ───────────────────────────────────────────────
    existing_cols = {c["name"] for c in inspector.get_columns("incentive_programs")}
    if "cap_basis" in existing_cols:
        op.drop_column("incentive_programs", "cap_basis")
