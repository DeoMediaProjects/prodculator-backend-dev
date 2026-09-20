"""Stage the frozen 101-company commercial freeze. Read-only without --apply.

Usage::

    venv/Scripts/python scripts/stage_commercial_catalogue.py
    DB_URL=… venv/Scripts/python scripts/stage_commercial_catalogue.py --apply

WHAT WAS MISSING
----------------
``prepare_commercial_handoff_snapshot.py`` wrote the freeze to disk and
``commercial_catalogue.py`` read three staging tables. Nothing joined them, so
the snapshot was a file nobody loaded and the tables stayed at zero rows — which
meant Section 10 and Section 12 returned nothing no matter how much research was
recorded, because the records the research describes did not exist.

This is the join. It reads the content-pinned snapshot and the verification
ledger, and writes what the two of them together support.

WHAT IT WILL NOT DO
-------------------
It does not turn prose into a claim. ``territory_scope`` and ``formats`` go
through the reviewed phrase tables in ``commercial_freeze``, and a phrase those
tables record as untypable produces no claim at all. ``genre_specialties`` and
``acquisition_stage`` are prose with no table, so they arrive only from a
VERIFIED ledger claim or not at all.

It does not approve a row the freeze itself hedged. Thirty-nine profiles were
read off a directory rather than the company's own site, and those stay
PENDING_SOURCE_REVIEW until the ledger carries a verified
``primary_source_confirmation`` of ``CONFIRMED`` for them. That is the whole
point of having asked.

And it does not approve a relationship whose endpoints are not both approved. A
dangling endpoint is not a small inconsistency here: ``parse_commercial_catalogue``
raises on it, the builder swallows the failure into a warning, and the entire
commercial section goes quiet for a reason nobody reading the report can see.

WHY IT UPDATES RATHER THAN REFUSING
-----------------------------------
``stage_engine_handoffs`` refuses when a staged row's content has changed,
because its source is a frozen file and a difference means the freeze moved.
Here one of the two inputs is the ledger, and the ledger is meant to change: a
claim reaching VERIFIED is exactly the event that should add a company's genres
or move it from PENDING to APPROVED. So an existing row is updated, and every
field that moves is printed — including, separately counted, any company that
loses approval, because that removes it from the matching universe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "data" / "handoff_snapshots" / "sales_distribution_v1_2026-09-16.json"

#: The reviewed freeze, over the exact bytes the repository stores. The phrase
#: tables in ``commercial_freeze`` are a review of the strings in THIS file, so
#: the pin is what makes them a review rather than a guess about a moving target.
SNAPSHOT_SHA256 = "ea7e1fc0cd475c78780bd64d470e380c8d33104547d863d4bdaee29792818ad3"

APPROVED = "APPROVED_SOURCE_REVIEW"
PENDING = "PENDING_SOURCE_REVIEW"

COMPANY_TABLE = "commercial_company_profiles"
COMPARABLE_TABLE = "commercial_comparable_profiles"
RELATIONSHIP_TABLE = "commercial_comparable_relationships"

#: Row states the freeze itself calls verified. ``PARTIALLY_VERIFIED`` and
#: ``IDENTITY_PROFILE_NEEDS_SOURCE_REFRESH`` are not among them, and both say so
#: in their own names.
_VERIFIED_PREFIX = "VERIFIED_"
#: The state that says the profile came from a directory rather than the
#: company. These need the ledger before they are approved.
_DIRECTORY_PROFILE = "VERIFIED_CURRENT_DIRECTORY_PROFILE"
#: Title-relationship states the freeze records. Both are verified readings of a
#: published source; the directory one is noted rather than discounted, because
#: a directory listing a company's own catalogue is reporting the company.
_VERIFIED_TITLE_STATES = {"VERIFIED", "VERIFIED_DIRECTORY_2026"}

#: Acquisition stages the research pack defined. A ledger value outside this
#: vocabulary is dropped rather than passed through: the engine compares stage
#: tokens, and an unrecognised one would simply never match while looking like
#: a recorded fact.
_ACQUISITION_STAGES = frozenset(
    {"development", "pre_production", "production", "post_production", "completed"}
)

#: Ledger fields this importer knows how to turn into a claim, and the claim
#: they become. ``festival_market_signal`` is deliberately absent: it is real
#: research and there is no scored field for it, and inventing one here would
#: put a value into the engine that nothing was designed to read.
_LEDGER_CLAIM_FIELDS = {
    "acquisition_stage": "acquisition_stages",
    "genre_specialties": "genres",
}


@dataclass
class StageReport:
    companies_seen: int = 0
    companies_approved: int = 0
    comparables_seen: int = 0
    comparables_approved: int = 0
    relationships_seen: int = 0
    relationships_approved: int = 0
    inserted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    #: Companies that were approved and no longer are. Counted apart because it
    #: is the one change here that makes the report say less than it did.
    approval_withdrawn: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    #: Why each unapproved company is unapproved, tallied.
    held_back: dict[str, int] = field(default_factory=dict)
    ledger_claims_used: dict[str, int] = field(default_factory=dict)
    ledger_claims_ignored: dict[str, int] = field(default_factory=dict)
    applied: bool = False


def _load_snapshot(path: Path = SNAPSHOT, *, verify_hash: bool = True) -> dict:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if verify_hash and digest != SNAPSHOT_SHA256:
        raise ValueError(
            f"{path.name}: content hash {digest} differs from the reviewed freeze. "
            "The phrase tables in commercial_freeze were reviewed against the "
            "pinned bytes; re-review them before moving the pin."
        )
    payload = json.loads(raw)
    if len(payload.get("companies") or []) != 101:
        raise ValueError("Expected 101 frozen commercial companies")
    return payload


def comparable_id(title: str) -> str:
    """One id per title, stable across runs and independent of row order."""
    return str(uuid5(NAMESPACE_URL, f"prodculator:commercial-comparable:{title.strip().casefold()}"))


def relationship_id(comparable: str, target_kind: str, target: str, kind: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{comparable}:{target_kind}:{target}:{kind}"))


def _as_date(value: Any) -> date:
    """A date column wants a date. SQLite refuses a string outright and Postgres
    coerces one, so passing the snapshot's text through would work in production
    and fail in the tests that are meant to prove production works."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _claim(value: Any, source_url: str, verified_on: str | date) -> dict:
    return {
        "value": value,
        "source_url": source_url,
        "verified_on": str(verified_on)[:10],
    }


def _ledger_claims(engine: sa.Engine | None, *, today: date):
    """VERIFIED commercial claims, keyed by company, or nothing.

    An absent ledger yields nothing rather than an error. A database that has
    not had the research table migrated into it can still stage the freeze, and
    the result is simply the freeze without the research on top.
    """
    if engine is None:
        return {}
    from app.modules.reports import verification_store
    from app.modules.reports.verification_ledger import (
        GATE_COMMERCIAL_PROFILE,
        readable_claims,
    )

    try:
        claims = verification_store.load_claims(engine, gate=GATE_COMMERCIAL_PROFILE)
    except verification_store.LedgerUnavailable:
        return {}
    by_subject: dict[str, dict[str, Any]] = {}
    for claim in readable_claims(claims, today=today):
        by_subject.setdefault(claim.subject_id, {})[claim.field] = claim
    return by_subject


def _list_from(value: Any, *, allowed: frozenset[str] | None = None) -> list[str]:
    items = [part.strip().lower() for part in str(value or "").split(";") if part.strip()]
    if allowed is not None:
        items = [item for item in items if item in allowed]
    return sorted(set(items))


def _company_rows(payload: dict, ledger: dict, report: StageReport) -> list[dict]:
    from app.modules.reports.commercial_freeze import (
        acquisition_formats,
        commercial_roles,
        is_current_brand,
        normalise_access_route,
        portfolio_group,
        rights_territories,
    )

    rows = []
    for raw in payload["companies"]:
        report.companies_seen += 1
        company_id = str(raw["company_id"])
        source_url = str(raw["source_url"])
        verified_on = str(raw["verification_date"])[:10]
        roles = commercial_roles(raw)
        current = is_current_brand(raw)

        claims: dict[str, dict] = {}
        if roles:
            claims["role"] = _claim(list(roles), source_url, verified_on)
        # ``active`` is written for a retired brand too, as False. It is a
        # sourced fact either way, and recording only the True case would leave
        # a retired company looking unresearched rather than closed.
        claims["active"] = _claim(current, source_url, verified_on)

        territories = rights_territories(raw)
        if territories:
            claims["rights_territories"] = _claim(list(territories), source_url, verified_on)
        formats = acquisition_formats(raw)
        if formats:
            claims["formats"] = _claim(list(formats), source_url, verified_on)

        for ledger_field, claim_field in _LEDGER_CLAIM_FIELDS.items():
            recorded = (ledger.get(company_id) or {}).get(ledger_field)
            if recorded is None:
                continue
            allowed = _ACQUISITION_STAGES if claim_field == "acquisition_stages" else None
            values = _list_from(recorded.value, allowed=allowed)
            if not values:
                report.ledger_claims_ignored[ledger_field] = (
                    report.ledger_claims_ignored.get(ledger_field, 0) + 1
                )
                continue
            claims[claim_field] = _claim(
                values, recorded.source_url, recorded.verified_on
            )
            report.ledger_claims_used[ledger_field] = (
                report.ledger_claims_used.get(ledger_field, 0) + 1
            )

        state, reviewed_on, reason = _company_review(raw, ledger, roles=roles, current=current)
        if state == APPROVED:
            report.companies_approved += 1
        else:
            report.held_back[reason] = report.held_back.get(reason, 0) + 1

        rows.append(
            {
                "id": company_id,
                "legacy_distributor_id": None,
                "name": str(raw["company_name"]),
                "claims": claims,
                # Never True. The freeze's acquisition scopes are prose by its
                # own account, so the rules are incomplete by definition, and
                # ``match_company`` surfaces that as a condition to confirm
                # rather than a silent gap.
                "rules_complete": False,
                "review_state": state,
                "reviewed_on": reviewed_on,
                "access_route": normalise_access_route(raw),
                "portfolio_group": portfolio_group(raw),
            }
        )
    return rows


def _company_review(
    raw: dict, ledger: dict, *, roles: tuple[str, ...], current: bool
) -> tuple[str, date | None, str]:
    """The review state one frozen company enters at, and why."""
    company_id = str(raw["company_id"])
    reviewed_on = _as_date(raw.get("last_verified_at") or raw["verification_date"])
    status = str(raw.get("verification_status") or "")

    if str(raw.get("paid_match_eligible") or "").upper() != "YES":
        return PENDING, None, "the freeze marks it not eligible for paid matching"
    if not status.startswith(_VERIFIED_PREFIX):
        return PENDING, None, f"the freeze records its profile as {status or 'unverified'}"
    if not current:
        # Recommending it by name would send a producer to a company that no
        # longer trades under it.
        return PENDING, None, "the brand no longer trades under its own name"
    if not roles:
        return PENDING, None, "no commercial role could be resolved"
    if status == _DIRECTORY_PROFILE:
        confirmation = (ledger.get(company_id) or {}).get("primary_source_confirmation")
        value = str(getattr(confirmation, "value", "") or "").strip().upper()
        if value != "CONFIRMED":
            return (
                PENDING,
                None,
                "profile came from a directory and no verified confirmation says "
                "the company's own site agrees",
            )
    return APPROVED, reviewed_on, ""


def _title_rows(
    payload: dict, company_rows: list[dict], report: StageReport
) -> tuple[list[dict], list[dict]]:
    by_name = {str(raw["company_name"]): str(raw["company_id"]) for raw in payload["companies"]}
    verified_on = {
        str(raw["company_id"]): _as_date(raw["verification_date"])
        for raw in payload["companies"]
    }
    approved_companies = {row["id"] for row in company_rows if row["review_state"] == APPROVED}

    comparables: dict[str, dict] = {}
    relationships: list[dict] = []
    for raw in payload["relationships"]:
        report.relationships_seen += 1
        title = str(raw["film_title"]).strip()
        target = by_name.get(str(raw["company_name"]))
        source_url = str(raw["source_url"])
        if not title or not target or not source_url.startswith("https://"):
            continue
        seen_on = verified_on[target]
        sourced = str(raw.get("verification_status") or "") in _VERIFIED_TITLE_STATES

        identity = comparable_id(title)
        if identity not in comparables:
            report.comparables_seen += 1
            comparables[identity] = {
                "id": identity,
                "legacy_comparable_id": None,
                "title": title,
                "source_url": source_url,
                "verified_on": seen_on,
                # Nothing. The freeze records which company handled the title
                # and not one attribute of the title itself, so a comparable
                # arrives with its identity sourced and its profile empty.
                "claims": {},
                "review_state": APPROVED if sourced else PENDING,
                "reviewed_on": seen_on if sourced else None,
            }
        # A relationship is only approved when BOTH endpoints are. A dangling
        # endpoint makes ``parse_commercial_catalogue`` raise, the builder
        # swallows that into a warning, and the whole commercial section goes
        # quiet for a reason the report cannot show.
        both_approved = (
            sourced
            and target in approved_companies
            and comparables[identity]["review_state"] == APPROVED
        )
        relationships.append(
            {
                "id": relationship_id(identity, "COMPANY", target, str(raw["relationship_type"])),
                "comparable_id": identity,
                "target_kind": "COMPANY",
                "target_id": target,
                # The freeze's own label, not a normalisation of it.
                # ``normalise_relationship_type`` answers what the label means
                # at read time; storing its answer would lose what was written.
                "relationship_type": str(raw["relationship_type"]),
                "source_url": source_url,
                "verified_on": seen_on,
                "review_state": APPROVED if both_approved else PENDING,
            }
        )

    report.comparables_approved = sum(
        1 for row in comparables.values() if row["review_state"] == APPROVED
    )
    report.relationships_approved = sum(
        1 for row in relationships if row["review_state"] == APPROVED
    )
    return list(comparables.values()), relationships


def build_rows(payload: dict, ledger: dict, report: StageReport):
    companies = _company_rows(payload, ledger, report)
    comparables, relationships = _title_rows(payload, companies, report)
    return companies, comparables, relationships


def _sync(conn, table_name: str, rows: list[dict], report: StageReport, *, apply: bool) -> None:
    table = sa.Table(table_name, sa.MetaData(), autoload_with=conn)
    columns = {column.name for column in table.columns}
    missing = {"access_route", "portfolio_group"} - columns
    if table_name == COMPANY_TABLE and missing:
        raise RuntimeError(
            f"{table_name} lacks {', '.join(sorted(missing))}; "
            "apply migration y4z5a6b7c8d9 first"
        )

    existing = {row["id"]: dict(row) for row in conn.execute(sa.select(table)).mappings()}
    inserts, updates = [], []
    for row in rows:
        payload = {key: value for key, value in row.items() if key in columns}
        prior = existing.get(row["id"])
        if prior is None:
            inserts.append(payload)
            continue
        moved = {
            key: (prior.get(key), value)
            for key, value in payload.items()
            if not _same(prior.get(key), value)
        }
        if not moved:
            continue
        updates.append(payload)
        for key, (was, now) in sorted(moved.items()):
            report.changes.append(f"{table_name} {row['id']} {key}: {was!r} -> {now!r}")
        if (
            table_name == COMPANY_TABLE
            and prior.get("review_state") == APPROVED
            and payload["review_state"] != APPROVED
        ):
            report.approval_withdrawn.append(row["id"])

    report.inserted[table_name] = len(inserts)
    report.updated[table_name] = len(updates)
    if not apply:
        return
    if inserts:
        conn.execute(table.insert(), inserts)
    for payload in updates:
        conn.execute(table.update().where(table.c.id == payload["id"]).values(**payload))


def _same(before: Any, after: Any) -> bool:
    """Whether a stored value and a derived one are the same fact.

    Dates come back as ``date`` and JSON comes back already decoded, so a
    literal comparison against the strings this module builds would report
    every row as changed on every run.
    """
    if isinstance(before, date) and not isinstance(before, bool):
        return str(before)[:10] == str(after or "")[:10]
    if isinstance(before, (dict, list)) or isinstance(after, (dict, list)):
        return json.dumps(before, sort_keys=True, default=str) == json.dumps(
            after, sort_keys=True, default=str
        )
    return before == after


def stage_commercial_catalogue(
    engine: sa.Engine,
    *,
    apply: bool = False,
    payload: dict | None = None,
    today: date | None = None,
) -> StageReport:
    today = today or date.today()
    payload = payload if payload is not None else _load_snapshot()
    report = StageReport(applied=apply)
    ledger = _ledger_claims(engine, today=today)
    companies, comparables, relationships = build_rows(payload, ledger, report)

    with engine.begin() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        required = {COMPANY_TABLE, COMPARABLE_TABLE, RELATIONSHIP_TABLE}
        if not required <= tables:
            raise RuntimeError("Apply the commercial staging migration before importing")
        # Companies first: a relationship's approval was decided against them,
        # and inserting the relationship first would briefly point at a row that
        # is not there.
        _sync(conn, COMPANY_TABLE, companies, report, apply=apply)
        _sync(conn, COMPARABLE_TABLE, comparables, report, apply=apply)
        _sync(conn, RELATIONSHIP_TABLE, relationships, report, apply=apply)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Insert and update staging rows")
    parser.add_argument("--show", type=int, default=15, help="How many field changes to list")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))

    db_url = os.environ.get("DB_URL")
    if not db_url:
        from app.core.config import get_settings

        db_url = get_settings().DB_URL
    engine = sa.create_engine(db_url)
    try:
        report = stage_commercial_catalogue(engine, apply=args.apply)
    except (RuntimeError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")

    print("Commercial freeze -> staging")
    print(f"  companies     : {report.companies_approved} approved of {report.companies_seen}")
    print(f"  comparables   : {report.comparables_approved} approved of {report.comparables_seen}")
    print(f"  relationships : {report.relationships_approved} approved of {report.relationships_seen}")
    print()
    for label, counts in (("inserted", report.inserted), ("updated", report.updated)):
        print(f"Rows {label}:")
        for table, count in counts.items():
            print(f"  {table:42} {count:>5}")
    print()
    if report.held_back:
        print("Why a company is not approved:")
        for reason, count in sorted(report.held_back.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>4}  {reason}")
        print()
    if report.ledger_claims_used or report.ledger_claims_ignored:
        print("Verified ledger claims:")
        for field_name, count in sorted(report.ledger_claims_used.items()):
            print(f"  {count:>4}  {field_name} -> claim")
        for field_name, count in sorted(report.ledger_claims_ignored.items()):
            print(f"  {count:>4}  {field_name} carried no value the engine reads")
        print()
    if report.approval_withdrawn:
        print(
            f"APPROVAL WITHDRAWN from {len(report.approval_withdrawn)} company/companies: "
            f"{', '.join(report.approval_withdrawn[:10])}"
        )
        print("  These leave the matching universe. Check why before applying.")
        print()
    if report.changes:
        print(f"Field changes on existing rows ({len(report.changes)}):")
        for line in report.changes[: args.show]:
            print(f"  {line}")
        print()
    if not args.apply:
        print("Preflight only. Re-run with --apply to write these rows.")
    else:
        print("Written. Nothing here is a recommendation; the engines decide that.")


if __name__ == "__main__":
    main()
