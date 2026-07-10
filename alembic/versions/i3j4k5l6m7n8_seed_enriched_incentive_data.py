"""seed_enriched_incentive_data

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-03-10 13:00:00.000000

Populate enriched incentive fields for priority territories:
UK (AVEC, IFTC, VFX Uplift), South Africa, Hungary, Malta, Nigeria.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "i3j4k5l6m7n8"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None

# Each dict: match on (territory, program) to UPDATE existing rows,
# or INSERT if no match exists.
_SEED_DATA = [
    # ── UK — Audio Visual Expenditure Credit (AVEC) ──────────────────────
    {
        "territory": "United Kingdom",
        "program": "Audio Visual Expenditure Credit (AVEC)",
        "rate": "34% of qualifying expenditure",
        "rate_gross": 34.0,
        "rate_net": 25.5,
        "rate_type": "tax_credit",
        "rate_tier_json": None,
        "cap": "No cap on qualifying expenditure",
        "cap_amount": None,
        "cap_currency": "GBP",
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "qualifying_spend_min": None,
        "qualifying_spend_cap_pct": 80.0,
        "qualifying_spend_currency": "GBP",
        "payment_timeline_days_min": 42,
        "payment_timeline_days_max": 56,
        "payment_timeline_notes": "6-8 weeks from HMRC claim submission. BFI certification (12-16 weeks) required first.",
        "eligibility_rules_json": '[{"rule":"Must pass BFI cultural test or qualify as official co-production","required":true},{"rule":"Minimum 10% core expenditure in UK","required":true},{"rule":"Company must be liable to UK corporation tax","required":true}]',
        "expiry_date": None,
        "currency": "GBP",
        "warnings_json": None,
        "source_name": "HMRC / BFI",
        "source_url": "https://www.bfi.org.uk/apply-british-certification-tax-relief",
        "status": "active",
    },
    # ── UK — Independent Film Tax Credit (IFTC) ─────────────────────────
    {
        "territory": "United Kingdom",
        "program": "Independent Film Tax Credit (IFTC)",
        "rate": "53% on first £15M, 34% above (gross)",
        "rate_gross": 53.0,
        "rate_net": 39.75,
        "rate_type": "tax_credit",
        "rate_tier_json": '[{"label":"First £15M qualifying spend","rate_gross":53,"rate_net":39.75},{"label":"Above £15M qualifying spend","rate_gross":34,"rate_net":25.5}]',
        "cap": "Budget cap £23.5M",
        "cap_amount": 23500000.0,
        "cap_currency": "GBP",
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "qualifying_spend_min": None,
        "qualifying_spend_cap_pct": 80.0,
        "qualifying_spend_currency": "GBP",
        "payment_timeline_days_min": 42,
        "payment_timeline_days_max": 56,
        "payment_timeline_notes": "6-8 weeks from HMRC claim submission. BFI certification (12-16 weeks) required first.",
        "eligibility_rules_json": '[{"rule":"Budget cap of £23.5M","required":true},{"rule":"Theatrical release required (no direct-to-streaming)","required":true},{"rule":"Must pass BFI cultural test or qualify as official co-production","required":true},{"rule":"Minimum 10% core expenditure in UK","required":true},{"rule":"Company must be liable to UK corporation tax","required":true}]',
        "expiry_date": None,
        "currency": "GBP",
        "warnings_json": '["Budget cap £23.5M — films above this threshold use AVEC instead","Theatrical release required — direct-to-streaming titles do not qualify"]',
        "source_name": "HMRC / BFI",
        "source_url": "https://www.gov.uk/government/publications/uk-independent-film-tax-credit",
        "status": "active",
    },
    # ── UK — VFX Expenditure Credit (Uplift) ─────────────────────────────
    {
        "territory": "United Kingdom",
        "program": "VFX Expenditure Credit (Uplift)",
        "rate": "39% of qualifying VFX expenditure",
        "rate_gross": 39.0,
        "rate_net": 29.25,
        "rate_type": "tax_credit",
        "rate_tier_json": None,
        "cap": "No cap",
        "cap_amount": None,
        "cap_currency": "GBP",
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "qualifying_spend_min": None,
        "qualifying_spend_cap_pct": 80.0,
        "qualifying_spend_currency": "GBP",
        "payment_timeline_days_min": 42,
        "payment_timeline_days_max": 56,
        "payment_timeline_notes": "6-8 weeks from HMRC claim submission. BFI certification (12-16 weeks) required first.",
        "eligibility_rules_json": '[{"rule":"Applies only to UK core VFX expenditure","required":true},{"rule":"Cannot combine with IFTC — mutually exclusive","required":true},{"rule":"Must pass BFI cultural test","required":true}]',
        "expiry_date": None,
        "currency": "GBP",
        "warnings_json": '["Mutually exclusive with IFTC — cannot claim both on same production"]',
        "source_name": "HMRC / BFI",
        "source_url": "https://www.bfi.org.uk/apply-british-certification-tax-relief",
        "status": "active",
    },
    # ── South Africa — Foreign Film & TV Production Incentive ────────────
    {
        "territory": "South Africa",
        "program": "Foreign Film & TV Production Incentive",
        "rate": "25% of qualifying South African production expenditure",
        "rate_gross": 25.0,
        "rate_net": 25.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": None,
        "cap": "No formal cap (subject to DTIC annual budget)",
        "cap_amount": None,
        "cap_currency": "ZAR",
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "qualifying_spend_min": 12000000.0,
        "qualifying_spend_cap_pct": None,
        "qualifying_spend_currency": "ZAR",
        "payment_timeline_days_min": 270,
        "payment_timeline_days_max": 450,
        "payment_timeline_notes": "9-15 months post-production. DTIC approval backlog can extend timeline.",
        "eligibility_rules_json": '[{"rule":"Minimum qualifying SA spend of ZAR 12M","required":true},{"rule":"Must use South African production services company","required":true},{"rule":"Application to DTIC before principal photography","required":true}]',
        "expiry_date": None,
        "currency": "ZAR",
        "warnings_json": '["Payment timeline 9-15 months — budget cash flow accordingly","DTIC approval backlog can extend beyond 15 months","ZAR exchange rate volatility risk"]',
        "source_name": "DTIC South Africa",
        "source_url": "https://www.thedtic.gov.za",
        "status": "active",
    },
    # ── Hungary — Hungarian Film Incentive ───────────────────────────────
    {
        "territory": "Hungary",
        "program": "Hungarian Film Incentive",
        "rate": "30% of qualifying Hungarian expenditure",
        "rate_gross": 30.0,
        "rate_net": 30.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": None,
        "cap": "HUF 3M per-person cap; NFI annual budget ~HUF 70B",
        "cap_amount": None,
        "cap_currency": "HUF",
        "cap_per_person": 3000000.0,
        "cap_per_person_currency": "HUF",
        "qualifying_spend_min": None,
        "qualifying_spend_cap_pct": None,
        "qualifying_spend_currency": "HUF",
        "payment_timeline_days_min": 90,
        "payment_timeline_days_max": 180,
        "payment_timeline_notes": "3-6 months after NFI audit completion. Queue risk when annual budget cap approached.",
        "eligibility_rules_json": '[{"rule":"Must apply to NFI before principal photography","required":true},{"rule":"Cultural test or bilateral co-production treaty","required":true},{"rule":"Hungarian production service company required","required":true}]',
        "expiry_date": None,
        "currency": "HUF",
        "warnings_json": '["HUF 3M per-person cap on individual above-the-line fees","NFI annual budget cap ~HUF 70B — queue risk in busy years","Payment may be delayed if NFI audit backlog"]',
        "source_name": "NFI Hungary",
        "source_url": "https://nfi.hu/en/filming-in-hungary/hungarian-film-incentive",
        "status": "active",
    },
    # ── Malta — Malta Film Tax Incentive (MFTI) ──────────────────────────
    {
        "territory": "Malta",
        "program": "Malta Film Tax Incentive (MFTI)",
        "rate": "40% cash rebate on qualifying expenditure",
        "rate_gross": 40.0,
        "rate_net": 40.0,
        "rate_type": "cash_rebate",
        "rate_tier_json": None,
        "cap": "€12.5M ATL cap; total rebate cap varies by project",
        "cap_amount": 12500000.0,
        "cap_currency": "EUR",
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "qualifying_spend_min": None,
        "qualifying_spend_cap_pct": None,
        "qualifying_spend_currency": "EUR",
        "payment_timeline_days_min": 60,
        "payment_timeline_days_max": 120,
        "payment_timeline_notes": "2-4 months after Malta Film Commission audit. Relatively fast by European standards.",
        "eligibility_rules_json": '[{"rule":"Application to Malta Film Commission before production","required":true},{"rule":"Qualifying expenditure must be incurred in Malta","required":true},{"rule":"Cultural test or EU co-production qualification","required":true}]',
        "expiry_date": "2028-10-29",
        "currency": "EUR",
        "warnings_json": '["€12.5M ATL (above-the-line) expenditure cap","Programme expires 29 October 2028 — check renewal status for later productions","Limited local crew pool — key HODs may need to be brought in"]',
        "source_name": "Malta Film Commission",
        "source_url": "https://www.maltafilmcommission.com/incentives/",
        "status": "active",
    },
    # ── Nigeria — No National Cash Rebate ────────────────────────────────
    {
        "territory": "Nigeria",
        "program": "No National Cash Rebate",
        "rate": "0% — No formal national film production rebate",
        "rate_gross": 0.0,
        "rate_net": 0.0,
        # NULL, not 'none': l4m5n6o7p8q9's CHECK constraint permits NULL for
        # no-programme rows; the literal 'none' fails its pre-flight on a
        # clean-replay database.
        "rate_type": None,
        "rate_tier_json": None,
        "cap": "N/A",
        "cap_amount": None,
        "cap_currency": "NGN",
        "cap_per_person": None,
        "cap_per_person_currency": None,
        "qualifying_spend_min": None,
        "qualifying_spend_cap_pct": None,
        "qualifying_spend_currency": "NGN",
        "payment_timeline_days_min": None,
        "payment_timeline_days_max": None,
        "payment_timeline_notes": "N/A — No national cash rebate available.",
        "eligibility_rules_json": None,
        "expiry_date": None,
        "currency": "NGN",
        "warnings_json": '["No national cash rebate or tax incentive for foreign film productions","Nollywood domestic industry — limited formal infrastructure for international co-productions","Nascent film commission with no formalised incentive framework"]',
        "source_name": "Nigerian Film Corporation",
        "source_url": "https://www.nfc.gov.ng",
        "status": "active",
    },
]

# Fields that go into the UPDATE when matching an existing row
_ENRICHED_FIELDS = [
    "rate", "rate_gross", "rate_net", "rate_type", "rate_tier_json",
    "cap", "cap_amount", "cap_currency", "cap_per_person", "cap_per_person_currency",
    "qualifying_spend_min", "qualifying_spend_cap_pct", "qualifying_spend_currency",
    "payment_timeline_days_min", "payment_timeline_days_max", "payment_timeline_notes",
    "eligibility_rules_json", "expiry_date", "currency", "warnings_json",
    "source_name", "source_url", "status",
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    for seed in _SEED_DATA:
        territory = seed["territory"]
        program = seed["program"]

        # Check if a row already exists for this territory + program
        result = conn.execute(
            sa.text(
                "SELECT id FROM incentive_programs "
                "WHERE territory = :territory AND program = :program "
                "LIMIT 1"
            ),
            {"territory": territory, "program": program},
        )
        row = result.fetchone()

        if row:
            # Update existing row with enriched data
            set_clauses = ", ".join(f"{f} = :{f}" for f in _ENRICHED_FIELDS)
            params = {f: seed.get(f) for f in _ENRICHED_FIELDS}
            params["row_id"] = row[0]
            conn.execute(
                sa.text(
                    f"UPDATE incentive_programs SET {set_clauses}, "  # noqa: S608
                    f"updated_at = NOW() WHERE id = :row_id"
                ),
                params,
            )
        else:
            # Insert new row
            from uuid import uuid4

            now = datetime.now(timezone.utc).isoformat()
            all_fields = ["id", "territory", "program"] + _ENRICHED_FIELDS + [
                "created_at", "updated_at",
            ]
            placeholders = ", ".join(f":{f}" for f in all_fields)
            cols = ", ".join(all_fields)
            params = {f: seed.get(f) for f in _ENRICHED_FIELDS}
            params["id"] = str(uuid4())
            params["territory"] = territory
            params["program"] = program
            params["created_at"] = now
            params["updated_at"] = now
            conn.execute(
                sa.text(f"INSERT INTO incentive_programs ({cols}) VALUES ({placeholders})"),  # noqa: S608
                params,
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    # Clear enriched fields back to NULL for seeded rows
    for seed in _SEED_DATA:
        territory = seed["territory"]
        program = seed["program"]

        set_null = ", ".join(f"{f} = NULL" for f in _ENRICHED_FIELDS if f not in ("rate", "cap", "status", "source_url"))
        conn.execute(
            sa.text(
                f"UPDATE incentive_programs SET {set_null} "  # noqa: S608
                f"WHERE territory = :territory AND program = :program"
            ),
            {"territory": territory, "program": program},
        )
