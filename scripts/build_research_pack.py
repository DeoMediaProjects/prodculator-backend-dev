"""Compile the outstanding verification gates into a research pack. Read-only.

Usage::

    DB_URL=… venv/Scripts/python scripts/build_research_pack.py --out research_pack

Produces one CSV per gate plus a README explaining what each column means and
what "done" looks like. Gates 1 and 2 are extracted from the live database;
gates 3 to 7 come from the frozen handoff snapshots, because their staging
tables are empty until the research lands.

WHAT THE COLUMNS ARE FOR
------------------------
Every sheet has the same five blank columns at the end — value, source_url,
source_basis, verified_on, verified_by — because they are what
``record_verifications.py`` reads. A filled sheet goes straight into the ledger
with no reshaping, and a reshaping step is where a subject_id gets mistyped.

Context columns come before them, prefixed ``ctx_``. They exist so the
researcher does not start from a blank page. They are NOT answers: a ``ctx_``
value is what the system currently believes, which in several of these gates is
precisely what is under review.

THE ONE RULE THIS FILE ENCODES
------------------------------
For the incentive gate, the legacy ``qualifying_spend_type`` column is included
as context and labelled as a guess. Copying it into the answer would launder an
old assumption into a new field and close the gate without verifying anything —
the ledger's own circular-source check exists to catch exactly that, and it
cannot catch a researcher who reads the hint and writes down a matching URL.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data" / "handoff_snapshots"

RESEARCH_COLUMNS = (
    "value",
    "source_url",
    "source_basis",
    "verified_on",
    "verified_by",
)


def _write(path: Path, rows: list[dict], context: list[str]) -> int:
    header = ["gate", "subject_id", "field", *[f"ctx_{c}" for c in context], *RESEARCH_COLUMNS]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(
                [row["gate"], row["subject_id"], row["field"]]
                + [row.get(c, "") for c in context]
                + ["", "", "", "", ""]
            )
    return len(rows)


# ── Gate 1: incentive engine classification ──────────────────────────────────

_CTX_INCENTIVE = [
    "programme",
    "territory",
    "rate_gross",
    "rate_type",
    "legacy_guess_qualifying_spend_type",
    "qs_basis",
    "source_url",
]


def incentive_rows(conn: sa.Connection) -> list[dict]:
    rows = []
    for r in conn.execute(
        sa.text(
            "SELECT id, program, territory, rate_gross, rate_type, "
            "qualifying_spend_type, qs_basis, source_url "
            "FROM incentive_programs "
            "WHERE qs_engine_type IS NULL AND lower(status) IN ('active','') "
            "ORDER BY territory, program"
        )
    ):
        rows.append(
            {
                "gate": "INCENTIVE_ENGINE_CLASSIFICATION",
                "subject_id": str(r[0]),
                "field": "qs_engine_type",
                "programme": r[1] or "",
                "territory": r[2] or "",
                "rate_gross": r[3] if r[3] is not None else "",
                "rate_type": r[4] or "",
                "legacy_guess_qualifying_spend_type": r[5] or "",
                "qs_basis": (r[6] or "")[:300],
                "source_url": r[7] or "",
            }
        )
    return rows


# ── Gate 1b: grants migration decisions the freeze left open ────────────────

_CTX_MIGRATION = [
    "legacy_title",
    "legacy_territory",
    "current_decision",
    "canonical_action",
    "verification_status",
    "resolved_live_id",
    "source_url",
]


def migration_decision_rows(conn: sa.Connection) -> list[dict]:
    """Legacy mappings whose decision is NEEDS_REVIEW.

    `v2_migration` parses a mapping's decision into a set of actions and
    reports "Mapping decision is unresolved and needs human review" when
    NEEDS_REVIEW is among them. Those rows are what a person has to settle:
    every other mapping already says what becomes of the record.

    The answer goes in `value` and must be one of the actions the migration
    encodes — CORRECT, RECLASSIFY, ARCHIVE, SPLIT, SUSPEND, REPLACE. Anything
    else is reported as unrecognised and changes nothing, which is the
    behaviour that makes a typo visible instead of silent.
    """
    if "grant_legacy_id_map" not in set(sa.inspect(conn).get_table_names()):
        return []
    rows = []
    for r in conn.execute(
        sa.text(
            "SELECT legacy_id, legacy_title, legacy_territory, decision, "
            "canonical_action, verification_status, resolved_live_id, "
            "official_source FROM grant_legacy_id_map "
            "WHERE upper(coalesce(decision, '')) LIKE '%NEEDS_REVIEW%' "
            "OR coalesce(decision, '') = '' ORDER BY legacy_title"
        )
    ):
        rows.append(
            {
                "gate": "GRANTS_MIGRATION_DECISION",
                "subject_id": str(r[0]),
                "field": "decision",
                "legacy_title": r[1] or "",
                "legacy_territory": r[2] or "",
                "current_decision": r[3] or "",
                "canonical_action": r[4] or "",
                "verification_status": r[5] or "",
                "resolved_live_id": r[6] or "",
                "source_url": r[7] or "",
            }
        )
    return rows


# ── Gate 2: grants split parents ─────────────────────────────────────────────

_CTX_SPLIT = ["parent_title", "territory", "funding_body", "source_url"]


def split_parent_rows(conn: sa.Connection) -> list[dict]:
    rows = []
    for r in conn.execute(
        sa.text(
            "SELECT g.id, g.title, g.territory, g.funding_body, g.website_url "
            "FROM grant_opportunities g "
            "WHERE g.lifecycle_state = 'SPLIT_PARENT' "
            "AND NOT EXISTS (SELECT 1 FROM grant_split_children s WHERE s.parent_id = g.id) "
            "ORDER BY g.title"
        )
    ):
        rows.append(
            {
                "gate": "GRANTS_SPLIT_PARENT",
                "subject_id": str(r[0]),
                "field": "successor_titles",
                "parent_title": r[1] or "",
                "territory": r[2] or "",
                "funding_body": r[3] or "",
                "source_url": r[4] or "",
            }
        )
    return rows


# ── Gates 3 to 7: from the frozen snapshots ──────────────────────────────────

_CTX_COMMERCIAL = [
    "company",
    "company_type",
    "territory_scope",
    "current_value",
    "source_basis",
    "profile_confidence",
    "source_url",
]


def commercial_rows() -> list[dict]:
    payload = json.loads(
        (SNAPSHOTS / "sales_distribution_v1_2026-09-16.json").read_text(encoding="utf-8")
    )
    rows = []
    for company in payload["companies"]:
        basis = str(company.get("source_basis") or "")
        base = {
            "gate": "COMMERCIAL_COMPANY_PROFILE",
            "subject_id": str(company.get("company_id")),
            "company": company.get("company_name") or "",
            "company_type": company.get("company_type") or "",
            "territory_scope": company.get("territory_scope") or "",
            "source_basis": basis,
            "profile_confidence": company.get("profile_confidence") or "",
            "source_url": company.get("source_url") or "",
        }
        if "director" in basis.lower():
            rows.append(
                {
                    **base,
                    "field": "primary_source_confirmation",
                    "current_value": "sourced from a directory listing, not the company",
                }
            )
        for field in ("acquisition_stage", "genre_specialties", "festival_market_signal"):
            value = str(company.get(field) or "").strip()
            if value.upper() in {"", "NONE", "UNKNOWN"}:
                rows.append({**base, "field": field, "current_value": "UNKNOWN"})
    return rows


_CTX_COMPARABLE = ["title", "handled_by", "source_url"]

#: What a comparable title needs before ``match_comparables`` will offer it.
#: Format and genres are the pair that matters — a title needs TWO sourced
#: similarities to a production before it counts as evidence, and those two are
#: the cheapest to establish from a public page. Origin and language are worth
#: one point each and are asked for in the same pass because the researcher is
#: already on the film's page.
COMPARABLE_FIELDS = ("format", "genres", "production_countries", "primary_languages")


def comparable_rows(conn) -> list[dict]:
    """One row per unclaimed attribute of each staged comparable title.

    Ordered by field and then title, so the whole catalogue's formats can be
    done in one pass rather than four fields at a time across 205 films.
    """
    import json

    import sqlalchemy as sa

    tables = set(sa.inspect(conn).get_table_names())
    if "commercial_comparable_profiles" not in tables:
        return []

    handlers: dict[str, str] = {}
    if {"commercial_comparable_relationships", "commercial_company_profiles"} <= tables:
        for row in conn.execute(sa.text(
            "SELECT r.comparable_id, c.name FROM commercial_comparable_relationships r "
            "JOIN commercial_company_profiles c ON c.id = r.target_id "
            "WHERE r.target_kind = 'COMPANY'"
        )):
            handlers.setdefault(str(row[0]), str(row[1] or ""))

    titles = []
    for row in conn.execute(sa.text(
        "SELECT id, title, source_url, claims FROM commercial_comparable_profiles "
        "ORDER BY title"
    )):
        claims = row[3]
        if isinstance(claims, str):
            try:
                claims = json.loads(claims)
            except ValueError:
                claims = {}
        titles.append((str(row[0]), str(row[1]), str(row[2] or ""), claims or {}))

    rows = []
    for field_name in COMPARABLE_FIELDS:
        for identity, title, source_url, claims in titles:
            # A field already claimed is not work. Reruns shrink.
            if field_name in claims:
                continue
            rows.append({
                "gate": "COMPARABLE_TITLE_PROFILE",
                "subject_id": identity,
                "field": field_name,
                "title": title,
                "handled_by": handlers.get(identity, ""),
                "source_url": source_url,
            })
    return rows


_CTX_MARKET = ["name", "host", "territory", "class", "current_value", "source_url"]


def market_rows(kind: str) -> list[dict]:
    payload = json.loads(
        (SNAPSHOTS / "markets_labs_wip_v1_2026-09-16.json").read_text(encoding="utf-8")
    )
    rows = []
    for track in payload:
        base = {
            "subject_id": str(track.get("id")),
            "name": track.get("name") or "",
            "host": track.get("host") or "",
            "territory": track.get("territory") or "",
            "class": track.get("class") or "",
            "source_url": track.get("source") or "",
        }
        if kind == "cycle":
            verification = str(track.get("verification") or "").upper()
            if verification == "PROGRAM_VERIFIED_CYCLE_PENDING" or not track.get("deadline"):
                rows.append(
                    {
                        **base,
                        "gate": "MARKET_CYCLE",
                        "field": "deadline",
                        "current_value": str(track.get("deadline") or "(none recorded)"),
                    }
                )
        else:
            gates = str(track.get("hard_gates") or "").strip()
            if gates:
                rows.append(
                    {
                        **base,
                        "gate": "MARKET_HARD_GATE",
                        "field": "hard_gates",
                        "current_value": gates[:400],
                    }
                )
    return rows


_CTX_FESTIVAL = [
    "festival",
    "location",
    "eligible_formats",
    "current_deadline_prose",
    "premiere_prose",
    "source_url",
]


def festival_rows(kind: str) -> list[dict]:
    payload = json.loads(
        (SNAPSHOTS / "festivals_v2_1_2026-09-15.json").read_text(encoding="utf-8")
    )
    rows = []
    for festival in payload:
        if str(festival.get("paid_match_eligible_v2")).lower() not in {"true", "1", "yes"}:
            continue
        base = {
            "subject_id": str(festival.get("id")),
            "festival": festival.get("name") or "",
            "location": festival.get("location") or "",
            "eligible_formats": str(festival.get("eligible_formats") or ""),
            "current_deadline_prose": str(
                festival.get("deadline_pattern") or festival.get("submission_deadline") or ""
            )[:200],
            "premiere_prose": str(festival.get("premiere_requirement") or "")[:200],
            "source_url": festival.get("website_url") or "",
        }
        if kind == "deadline":
            rows.append({**base, "gate": "FESTIVAL_SECTION", "field": "section_deadlines"})
        else:
            rows.append({**base, "gate": "FESTIVAL_SECTION", "field": "section_rules"})
    return rows


README = """# Prodculator v2 — source verification research pack

Seven gates. Each has a CSV. Fill the five blank columns on the right of every
row, then record them with:

    venv/Scripts/python scripts/record_verifications.py <file>.csv          # preflight
    venv/Scripts/python scripts/record_verifications.py <file>.csv --apply

Recording is not approving. Every claim lands as PENDING and reaches an engine
only after a named reviewer signs it off:

    venv/Scripts/python scripts/record_verifications.py \\
        --review GATE/SUBJECT_ID/FIELD --reviewer <name> [--qa-by <other name>]

## The five columns you fill

| Column | What goes in it |
| --- | --- |
| `value` | The answer. Allowed values are listed per gate below. |
| `source_url` | The official page you read it on. https, and the programme's own site — not a directory, not an aggregator, not our own admin. |
| `source_basis` | What that page actually says, in its words. One sentence is enough. It is what lets a reviewer check your reading without revisiting the page. |
| `verified_on` | The date you read it, `YYYY-MM-DD`. Not a future date. |
| `verified_by` | You. |

Columns prefixed `ctx_` are context, not answers. They are what the system
currently believes, and in several gates that belief is exactly what is under
review.

## A claim is rejected if

- it has no `source_url`, or the URL is http rather than https
- the URL points at our own database or admin (a record cannot verify itself)
- `source_basis` is blank
- `verified_on` is in the future
- for market hard gates and festival section rules, the person who signs the QA
  is the same person who made the claim

---

## 1. Incentive engine classification — `gate_1_incentive_classification.csv`

**Field:** `qs_engine_type` — which statutory formula the programme calculates on.

**Allowed values**

| Value | Means |
| --- | --- |
| `CORE_LOWER_OF` | Lower of local core expenditure and a percentage of global core (the UK AVEC shape) |
| `ELIGIBLE_LOCAL_SPEND` | A defined local eligible expenditure figure |
| `QUALIFIED_LABOUR` | A qualified labour base only |
| `QAPE` | Qualifying Australian Production Expenditure |
| `QNZPE` | Qualifying New Zealand Production Expenditure |
| `VFX_ONLY` | A visual effects base only |
| `PDV_ONLY` | A post, digital and visual effects base |
| `TIERED_SPEND` | Eligible local spend with rates banded by amount |
| `MULTI_BUCKET` | Several named cost buckets summed |
| `INVESTOR_TAX_SHELTER` | Not calculated from production spend at all |
| `COMPETITIVE_GRANT` | Awarded, not computed |
| `NO_PROGRAMME` | No claimable programme exists |

**Read the statute, not the hint.** `ctx_legacy_guess_qualifying_spend_type` is
the old column this work replaces. Copying it launders an old assumption into a
new field and closes the gate without verifying anything.

**Done when** the programme's official rules name the expenditure the rate
applies to, and `source_basis` quotes that sentence.

---

## 2. Grants split parents — `gate_2_grants_split_parents.csv`

**Field:** `successor_titles` — which records in the 253-row v2 master replace
this retired generic parent. Semicolon-separated exact titles.

These were retired because the master carries the programme as several specific
records instead of one generic one. An automatic title match could not find
them, and two similarity heuristics produced nonsense ("BC Arts Council" matched
"California Arts Council"), so this needs someone reading the master.

**Write `NONE` if the funding body genuinely has no record in the master.** That
is the important finding: it means a real programme has been retired and
producers can no longer see it.

**Done when** each named successor exists in the master, or `NONE` with a source
showing the programme ended.

---

## 3. Commercial company profiles — `gate_3_commercial_profiles.csv`

**Fields**

- `primary_source_confirmation` — the profile came from a directory listing.
  Confirm it against the company's own site. Value: `CONFIRMED` or `CONTRADICTED`,
  and put what differs in `source_basis`.
- `acquisition_stage` — at what stage they acquire. One or more of
  `development`, `pre_production`, `production`, `post_production`, `completed`.
- `genre_specialties` — semicolon-separated.
- `festival_market_signal` — festivals or markets they are documented attending.

**A published contact page is not evidence that they accept submissions.** If
the site says nothing about unsolicited material, that is `UNKNOWN`, not open.

**Done when** the company's own site supports the value.

---

## 4. Market cycles — `gate_4_market_cycles.csv`

**Field:** `deadline` — the current call's closing date, `YYYY-MM-DD`.

Transcription from the official call page. No second reviewer needed.

**If the next call is not yet announced, write `NOT_ANNOUNCED`.** Do not carry
last year's date forward, and do not infer one from a pattern.

**Done when** the date is on the programme's own page and `source_basis` quotes
the line it appears on.

---

## 5. Market hard gates — `gate_5_market_hard_gates.csv` · TWO PEOPLE

**Field:** `hard_gates` — the prose eligibility rules, as typed rules.

Format, one per line: `project_field operator expected`

Operators: `equals`, `one_of`, `at_least`, `at_most`, `greater_than`,
`less_than`, `overlaps`, `manual_confirmation`.

Example — "feature projects at development stage, director from an eligible
region" becomes:

    format one_of feature
    stage one_of development
    production_countries manual_confirmation eligible region per programme list

**Use `manual_confirmation` whenever the rule needs a human to judge it.** A
rule typed as something decidable when it is not is worse than leaving it
unstructured, because the engine will then act on it.

**Leave anything you cannot type out entirely.** An unstructured requirement
stays UNKNOWN, which the engine already handles.

**Requires independent QA:** a second person, not the author, reviews the typing.
The ledger rejects self-signed QA by identity.

---

## 6. Festival section deadlines — `gate_6_festival_section_deadlines.csv`

**Field:** `section_deadlines` — every section, with its own deadline.

Format, one per line: `Section name | YYYY-MM-DD`

One row per festival, but a festival has several sections and they close on
different dates — feature, short and episodic deadlines differ, which is why a
festival-level date cannot answer when *this* production must submit.

`ctx_current_deadline_prose` is what the snapshot holds, and on 121 records it
literally reads "Verify current submission windows on the…". That is the source
telling you it did not resolve them.

**Done when** every section a feature could enter has a dated deadline from the
festival's own site.

---

## 7. Festival section rules — `gate_7_festival_section_rules.csv` · TWO PEOPLE

**Field:** `section_rules` — per section, the eligibility rules, typed as in
gate 5.

Also record, per section, the premiere requirement as one of `WORLD`,
`INTERNATIONAL`, `NATIONAL`, `NONE`:

    Section name | premiere_requirement WORLD
    Section name | runtime_minutes at_most 40

**`NONE` means the festival states it imposes no premiere requirement.** It does
not mean the page was silent — silence is left unrecorded, and the engine treats
an unrecorded requirement as needing confirmation.

**Requires independent QA.**

---

## Order

1 first: it is the smallest gate, needs no second reviewer, and it is the only
one that makes the old-versus-v2 comparison produce anything real, because
Section 07 stays empty until programmes can calculate.

Then 2 and 3 — both bounded, neither needs a second reviewer.

Then 4, then 6 (transcription), then 5 and 7 (interpretation, two people).
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "research_pack")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(ROOT))
    counts: dict[str, int] = {}

    db_url = os.environ.get("DB_URL")
    if db_url:
        engine = sa.create_engine(db_url)
        with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                conn.execute(sa.text("SET TRANSACTION READ ONLY"))
            counts["gate_1_incentive_classification"] = _write(
                args.out / "gate_1_incentive_classification.csv",
                incentive_rows(conn),
                _CTX_INCENTIVE,
            )
            counts["gate_1b_grants_migration_decisions"] = _write(
                args.out / "gate_1b_grants_migration_decisions.csv",
                migration_decision_rows(conn),
                _CTX_MIGRATION,
            )
            counts["gate_2_grants_split_parents"] = _write(
                args.out / "gate_2_grants_split_parents.csv",
                split_parent_rows(conn),
                _CTX_SPLIT,
            )
    else:
        print("DB_URL not set — gates 1 and 2 need the live database; skipping them.\n")

    counts["gate_3_commercial_profiles"] = _write(
        args.out / "gate_3_commercial_profiles.csv", commercial_rows(), _CTX_COMMERCIAL
    )
    counts["gate_4_market_cycles"] = _write(
        args.out / "gate_4_market_cycles.csv", market_rows("cycle"), _CTX_MARKET
    )
    counts["gate_5_market_hard_gates"] = _write(
        args.out / "gate_5_market_hard_gates.csv", market_rows("gates"), _CTX_MARKET
    )
    counts["gate_6_festival_section_deadlines"] = _write(
        args.out / "gate_6_festival_section_deadlines.csv",
        festival_rows("deadline"),
        _CTX_FESTIVAL,
    )
    counts["gate_7_festival_section_rules"] = _write(
        args.out / "gate_7_festival_section_rules.csv",
        festival_rows("rules"),
        _CTX_FESTIVAL,
    )

    (args.out / "README.md").write_text(README, encoding="utf-8")

    print("Research pack written to", args.out)
    print()
    for name, count in counts.items():
        print(f"  {name + '.csv':44} {count:>4} rows")
    print(f"\n  {'TOTAL':44} {sum(counts.values()):>4} rows")
    print("\nStart with gate 1. See README.md.")


if __name__ == "__main__":
    main()
