"""fix_incentive_rates_and_sources

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-03-14 12:00:00.000000

Applies all corrections from the Prodculator Incentive & Weather Engine
Implementation Guide v1.0:

SCHEMA ADDITIONS
  - vfx_uplift_pct  NUMERIC(5,2) — additional % for productions with
                    qualifying VFX content (e.g. 7.5 for Hungary)
  - programme_level TEXT         — 'national' | 'regional' | 'state'
                    (mirrors guide's programme_level field; distinct from
                    the existing 'scope' column which uses the same values
                    but was added later — kept in sync)

RATE / DATA CORRECTIONS
  UK — AVEC
    • rate updated to reflect BOTH tiers: "34% ATL + 25% BTL"
    • rate_gross kept at 34.0 (ATL tier, highest published gross rate)
    • rate_net updated to 25.5 (BTL effective net)
    • rate_tier_json added: ATL tier (34%) and BTL tier (25%)
    • source_url → canonical HMRC URL from guide
    • eligibility_notes added (plain-English two-tier explanation)

  UK — IFTC
    • source_url → canonical BFI URL from guide (was pointing to wrong page)

  UK — VFX Expenditure Credit (Uplift)
    • source_url → canonical HMRC URL from guide

  UK — Creative Scotland Production Growth Fund (regional)
    • source_url → canonical Creative Scotland URL from guide

  UK — Ffilm Cymru Wales Production Fund (regional)
    • source_url → canonical Wales Screen URL from guide

  UK — Northern Ireland Screen Fund (regional)
    • source_url already correct — confirmed, no change

  Hungary — Hungarian Film Incentive
    • vfx_uplift_pct = 7.5 (base 30% + 7.5% uplift = 37.5% for VFX)
    • rate updated to: "30% base (37.5% with qualifying VFX content)"
    • source_url → canonical NFI URL from guide

  South Africa — Foreign Film & TV Production Incentive (DTIC)
    • source_url → canonical DTIC URL from guide
    • qualifying_spend_min confirmed at 12,000,000 ZAR (was already
      correct in i3j4k5l6m7n8; guide flags previous reports that showed
      ZAR 500K — the DB value is already correct, source_url is not)

  Malta — Malta Film Tax Incentive (MFTI)
    • programme renamed to "Malta Audio-Visual GIP Rebate" per guide
    • source_url → canonical Malta Film Commission URL from guide
    • eligibility_notes updated to note ATL+BTL coverage

  Georgia (USA) — Georgia Entertainment Industry Investment Act
    • source_url → canonical Georgia Film Office URL from guide

  New Mexico — New Mexico Film Tax Credit
    • source_url → canonical NM Film URL from guide

  Ireland — Section 481 Tax Credit
    • source_url → canonical Revenue.ie URL from guide

  British Columbia — BC Film Incentive BC Tax Credit (FIBC)
    • source_url → Creative BC canonical URL (already correct, confirmed)
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "q1r2s3t4u5v6"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None

_NOW = datetime(2026, 3, 14, tzinfo=timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Schema additions
# ---------------------------------------------------------------------------

_NEW_COLUMNS = [
    # (column_name, sql_type, default)
    ("vfx_uplift_pct", "NUMERIC(5,2)", None),
    ("programme_level", "TEXT", "'national'"),
    ("eligibility_notes", "TEXT", None),
]

# ---------------------------------------------------------------------------
# Per-row corrections  —  keyed by (territory, program)
# ---------------------------------------------------------------------------
# Each dict contains only the fields being changed.  The upgrade() function
# builds a targeted UPDATE for each row so nothing else is touched.

_CORRECTIONS = [
    # ── UK — AVEC ─────────────────────────────────────────────────────────
    {
        "territory": "United Kingdom",
        "program": "Audio Visual Expenditure Credit (AVEC)",
        "rate": "34% ATL + 25% BTL on qualifying UK expenditure",
        # rate_gross stays 34.0 (ATL tier = headline rate)
        # rate_net   stays 25.5 (BTL effective net)
        "rate_tier_json": (
            '[{"label":"Above-the-Line qualifying spend (directors, writers, '
            'lead actors)","rate_gross":34,"rate_net":25.5},'
            '{"label":"Below-the-Line qualifying spend (all other UK crew '
            'and facilities)","rate_gross":25,"rate_net":18.75}]'
        ),
        "source_name": "HMRC",
        "source_url": "https://www.gov.uk/guidance/corporation-tax-the-film-tax-relief",
        "eligibility_notes": (
            "AVEC has TWO separate rates: 34% for Above-the-Line (ATL) qualifying "
            "expenditure (directors, writers, lead actors) and 25% for Below-the-Line "
            "(BTL) qualifying expenditure. The blended effective rate depends on the "
            "ATL/BTL budget split. AVEC and IFTC are mutually exclusive — choose one "
            "per project. Minimum 10% of core expenditure must be incurred in the UK."
        ),
        "programme_level": "national",
    },
    # ── UK — IFTC ──────────────────────────────────────────────────────────
    {
        "territory": "United Kingdom",
        "program": "Independent Film Tax Credit (IFTC)",
        "source_name": "HMRC / BFI",
        "source_url": "https://www.bfi.org.uk/apply-british-certification-tax-relief/independent-film-tax-credit",
        "eligibility_notes": (
            "Separate programme from AVEC — for independent films with a total budget "
            "below £15M (budget cap £23.5M). Offers up to 53% on first £15M of "
            "qualifying UK spend, then 34% above. BFI Cultural Test required (min 18 "
            "of 35 points). Theatrical release required — no direct-to-streaming. "
            "IFTC and AVEC are mutually exclusive on the same production."
        ),
        "programme_level": "national",
    },
    # ── UK — VFX Expenditure Credit ────────────────────────────────────────
    {
        "territory": "United Kingdom",
        "program": "VFX Expenditure Credit (Uplift)",
        "source_name": "HMRC / BFI",
        "source_url": "https://www.gov.uk/guidance/corporation-tax-the-film-tax-relief",
        "eligibility_notes": (
            "Applies only to UK core VFX expenditure. Mutually exclusive with IFTC — "
            "cannot claim both on the same production. Must pass BFI cultural test."
        ),
        "programme_level": "national",
    },
    # ── UK — Creative Scotland (regional) ──────────────────────────────────
    {
        "territory": "Scotland",
        "program": "Creative Scotland Production Growth Fund",
        "source_name": "Creative Scotland",
        "source_url": "https://www.creativescotland.com/funding/funding-programmes/broadcast-and-film",
        "programme_level": "regional",
        "eligibility_notes": (
            "£75,000–£300,000 non-repayable grant for productions shooting a minimum "
            "of 5 days in Scotland. Stackable simultaneously with UK AVEC or IFTC. "
            "Discretionary fund — competitive application. Apply before principal photography."
        ),
    },
    # ── UK — Wales Screen (regional) ───────────────────────────────────────
    {
        "territory": "Wales",
        "program": "Ffilm Cymru Wales Production Fund",
        "source_name": "Wales Screen / Ffilm Cymru Wales",
        "source_url": "https://wales.com/creative/creative-industries/screen",
        "programme_level": "regional",
        "eligibility_notes": (
            "Up to £500K grant/equity per project. Welsh shoot required. Welsh language "
            "or cultural element adds points to BFI Cultural Test. Stackable with AVEC or IFTC."
        ),
    },
    # ── UK — Northern Ireland Screen (regional) ────────────────────────────
    {
        "territory": "Northern Ireland",
        "program": "Northern Ireland Screen Fund",
        "source_name": "Northern Ireland Screen",
        "source_url": "https://www.northernirelandscreen.co.uk/funding/",
        "programme_level": "regional",
        "eligibility_notes": (
            "Up to £800K grant/equity. Must shoot in Northern Ireland. "
            "Stackable with AVEC or IFTC."
        ),
    },
    # ── Hungary — Film Incentive ────────────────────────────────────────────
    {
        "territory": "Hungary",
        "program": "Hungarian Film Incentive",
        "rate": "30% base (37.5% with qualifying VFX content)",
        # rate_gross stays 30.0 — base rate; vfx_uplift_pct = 7.5 gives 37.5%
        "vfx_uplift_pct": 7.5,
        "source_name": "NFI Hungary",
        "source_url": "https://nfi.hu/en/filming-in-hungary",
        "eligibility_notes": (
            "30% base rate on qualifying Hungarian expenditure. VFX uplift applies: "
            "37.5% total rate (30% + 7.5%) for productions where vfxIndicator is "
            "'moderate' or 'high'. No nationality restriction, no minimum spend, "
            "no cultural test — one of the most accessible incentives in Europe. "
            "HUF 3M per-person cap on above-the-line individual fees."
        ),
        "programme_level": "national",
    },
    # ── South Africa — DTIC ────────────────────────────────────────────────
    {
        "territory": "South Africa",
        "program": "Foreign Film & TV Production Incentive",
        # qualifying_spend_min already correct at 12,000,000 ZAR from i3j4k5l6m7n8
        "source_name": "DTIC South Africa",
        "source_url": "https://www.dtic.gov.za/incentives/film-and-television",
        "eligibility_notes": (
            "25% cash rebate on qualifying South African spend (35% with significant "
            "SA content / SA co-producer). Minimum qualifying spend ZAR 12,000,000 "
            "(not ZAR 500,000 — previous reports contained this error). "
            "Must use SA production services company. Apply to DTIC before principal photography."
        ),
        "programme_level": "national",
    },
    # ── Malta — GIP Rebate ─────────────────────────────────────────────────
    # Guide renames this from "Malta Film Tax Incentive (MFTI)" to
    # "Malta Audio-Visual GIP Rebate" and provides a corrected source URL.
    # We UPDATE the existing row (matched on old program name) and also
    # patch the program name itself.
    {
        "territory": "Malta",
        "program": "Malta Film Tax Incentive (MFTI)",           # match key (old name)
        "_new_program_name": "Malta Audio-Visual GIP Rebate",  # rename target
        "source_name": "Malta Film Commission",
        "source_url": "https://www.mfc.com.mt/filming-in-malta/incentives",
        "eligibility_notes": (
            "40% rebate on ALL qualifying Malta spend — uniquely covers both "
            "Above-the-Line (non-resident lead actor fees, director) AND "
            "Below-the-Line crew costs at the same rate. Most other territories "
            "exclude or cap ATL. This makes Malta disproportionately valuable for "
            "productions with high lead actor costs. Min €50K qualifying Malta spend. "
            "No cultural test required. Open to all producers."
        ),
        "programme_level": "national",
    },
    # ── Georgia (USA) ──────────────────────────────────────────────────────
    {
        "territory": "Georgia (USA)",
        "program": "Georgia Entertainment Industry Investment Act",
        "source_name": "Georgia Film Office",
        "source_url": "https://www.georgia.org/industries/film-entertainment/georgia-film-tv-production/production-tax-incentive",
        "programme_level": "state",
    },
    # ── New Mexico ─────────────────────────────────────────────────────────
    {
        "territory": "New Mexico",
        "program": "New Mexico Film Tax Credit",
        "source_name": "New Mexico Film Office",
        "source_url": "https://nmfilm.com/incentives/",
        "programme_level": "state",
    },
    # ── Ireland — Section 481 ──────────────────────────────────────────────
    {
        "territory": "Ireland",
        "program": "Section 481 Tax Credit",
        "source_name": "Revenue Commissioners Ireland",
        "source_url": "https://www.revenue.ie/en/companies-and-charities/reliefs-and-exemptions/film-relief/index.aspx",
        "eligibility_notes": (
            "32% tax credit on qualifying Irish expenditure. Open to Irish and foreign "
            "co-producers with Irish spend. Foreign producers must route through an "
            "Irish-registered production company or co-producer. Cultural test via "
            "Screen Ireland required. Min €1M project budget (guide), min €250K "
            "qualifying Irish spend (Revenue). Max eligible expenditure €70M per project."
        ),
        "programme_level": "national",
    },
    # ── British Columbia ────────────────────────────────────────────────────
    {
        "territory": "British Columbia",
        "program": "BC Film Incentive BC Tax Credit (FIBC)",
        "source_name": "Creative BC",
        "source_url": "https://www.creativebc.com/sector-development/motion-picture-tax-credits",
        "programme_level": "regional",
    },
    # ── Nigeria — no incentive ─────────────────────────────────────────────
    {
        "territory": "Nigeria",
        "program": "No National Cash Rebate",
        "source_name": "Nigerian Film Corporation",
        "source_url": "https://www.nfc.gov.ng",
        "programme_level": "national",
        "eligibility_notes": (
            "No national cash rebate or tax incentive for foreign film productions. "
            "No rebate amount should be estimated for Nigerian productions."
        ),
    },
]

# Fields that may appear in _CORRECTIONS entries and should be written to DB
_PATCHABLE_FIELDS = {
    "rate",
    "rate_gross",
    "rate_net",
    "rate_tier_json",
    "vfx_uplift_pct",
    "source_name",
    "source_url",
    "eligibility_notes",
    "programme_level",
}

# ---------------------------------------------------------------------------
# Previous (wrong) source_url values — used in downgrade()
# ---------------------------------------------------------------------------

_PREVIOUS_SOURCE_URLS = {
    ("United Kingdom", "Audio Visual Expenditure Credit (AVEC)"):
        "https://www.bfi.org.uk/apply-british-certification-tax-relief",
    ("United Kingdom", "Independent Film Tax Credit (IFTC)"):
        "https://www.gov.uk/government/publications/uk-independent-film-tax-credit",
    ("United Kingdom", "VFX Expenditure Credit (Uplift)"):
        "https://www.bfi.org.uk/apply-british-certification-tax-relief",
    ("Scotland", "Creative Scotland Production Growth Fund"):
        "https://www.creativescotland.com/funding/funding-programmes/screen",
    ("Wales", "Ffilm Cymru Wales Production Fund"):
        "https://ffilmcymruwales.com/funding",
    ("Hungary", "Hungarian Film Incentive"):
        "https://nfi.hu/en/filming-in-hungary/hungarian-film-incentive",
    ("South Africa", "Foreign Film & TV Production Incentive"):
        "https://www.thedtic.gov.za",
    ("Malta", "Malta Audio-Visual GIP Rebate"):   # will have been renamed by then
        "https://www.maltafilmcommission.com/incentives/",
    ("Georgia (USA)", "Georgia Entertainment Industry Investment Act"):
        "https://www.georgia.org/industries/film-entertainment",
    ("New Mexico", "New Mexico Film Tax Credit"):
        "https://nmfilm.com/film-incentive/",
    ("Ireland", "Section 481 Tax Credit"):
        "https://www.screenireland.ie/filming/section-481",
}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("incentive_programs")}

    # ── 1. Add new schema columns ──────────────────────────────────────────
    for col_name, col_type, col_default in _NEW_COLUMNS:
        if col_name not in columns:
            default_clause = f" DEFAULT {col_default}" if col_default else ""
            conn.execute(
                sa.text(
                    f"ALTER TABLE incentive_programs "         # noqa: S608
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}{default_clause}"
                )
            )

    # Re-read columns after additions
    inspector = sa.inspect(conn)
    columns = {col["name"] for col in inspector.get_columns("incentive_programs")}

    # ── 2. Apply per-row corrections ───────────────────────────────────────
    for correction in _CORRECTIONS:
        territory = correction["territory"]
        program = correction["program"]
        new_program_name = correction.get("_new_program_name")

        # Build SET clause from patchable fields present in this correction
        set_parts = []
        params: dict = {"territory": territory, "program": program, "now": _NOW}

        for field in _PATCHABLE_FIELDS:
            if field not in correction:
                continue
            if field not in columns:
                # Column not yet present (shouldn't happen after step 1, but safe)
                continue
            set_parts.append(f"{field} = :{field}")
            params[field] = correction[field]

        # Handle programme rename
        if new_program_name:
            set_parts.append("program = :new_program_name")
            params["new_program_name"] = new_program_name

        if not set_parts:
            continue

        set_parts.append("updated_at = :now")

        conn.execute(
            sa.text(
                f"UPDATE incentive_programs "                  # noqa: S608
                f"SET {', '.join(set_parts)} "
                f"WHERE territory = :territory AND program = :program"
            ),
            params,
        )

    # ── 3. Backfill programme_level for existing rows that lack it ─────────
    # Any row still NULL gets 'national' as a safe default.
    if "programme_level" in columns:
        conn.execute(
            sa.text(
                "UPDATE incentive_programs "                   # noqa: S608
                "SET programme_level = scope "
                "WHERE programme_level IS NULL AND scope IS NOT NULL"
            )
        )
        conn.execute(
            sa.text(
                "UPDATE incentive_programs "                   # noqa: S608
                "SET programme_level = 'national' "
                "WHERE programme_level IS NULL"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "incentive_programs" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("incentive_programs")}

    # Undo Malta rename
    conn.execute(
        sa.text(
            "UPDATE incentive_programs SET program = 'Malta Film Tax Incentive (MFTI)' "
            "WHERE territory = 'Malta' AND program = 'Malta Audio-Visual GIP Rebate'"
        )
    )

    # Restore previous source_urls
    for (territory, program), old_url in _PREVIOUS_SOURCE_URLS.items():
        conn.execute(
            sa.text(
                "UPDATE incentive_programs SET source_url = :url "
                "WHERE territory = :territory AND program = :program"
            ),
            {"url": old_url, "territory": territory, "program": program},
        )

    # Restore AVEC rate fields
    conn.execute(
        sa.text(
            "UPDATE incentive_programs SET "
            "rate = '34% of qualifying expenditure', "
            "rate_tier_json = NULL, "
            "eligibility_notes = NULL "
            "WHERE territory = 'United Kingdom' "
            "AND program = 'Audio Visual Expenditure Credit (AVEC)'"
        )
    )

    # Restore Hungary vfx_uplift_pct
    if "vfx_uplift_pct" in columns:
        conn.execute(
            sa.text(
                "UPDATE incentive_programs SET "
                "vfx_uplift_pct = NULL, "
                "rate = '30% of qualifying Hungarian expenditure' "
                "WHERE territory = 'Hungary' AND program = 'Hungarian Film Incentive'"
            )
        )

    # Drop added columns
    for col_name, _, _ in _NEW_COLUMNS:
        if col_name in columns:
            conn.execute(
                sa.text(
                    f"ALTER TABLE incentive_programs "          # noqa: S608
                    f"DROP COLUMN IF EXISTS {col_name}"
                )
            )
