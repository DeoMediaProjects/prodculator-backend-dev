"""Read only explicitly reviewed commercial staging records.

Every matching claim carries its own source and verification date. Legacy rows
and unreviewed staging rows never silently enter the v2 matching universe.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from urllib.parse import urlparse

import sqlalchemy as sa

from app.modules.reports.commercial_freeze import ACCESS_LABELS, ACCESS_UNKNOWN
from app.modules.reports.commercial_strategy import (
    CompanyProfile,
    ComparableProfile,
    ComparableRelationship,
    SourcedValue,
)

_APPROVED = "APPROVED_SOURCE_REVIEW"
_COMPANY_CLAIMS = {
    "role", "active", "formats", "content_forms", "techniques", "acquisition_stages",
    "minimum_budget_gbp", "maximum_budget_gbp", "production_origins", "required_languages",
    "rights_territories", "genres", "channels",
}
_COMPARABLE_CLAIMS = {
    "format", "genres", "tone", "themes", "content_form", "budget_gbp",
    "production_countries", "primary_languages", "target_audience", "release_profile",
}
_RELATIONSHIP_KINDS = {"COMPANY", "FESTIVAL", "MARKET_LAB_WIP"}
_LIST_CLAIMS = {
    "formats", "content_forms", "techniques", "acquisition_stages", "production_origins",
    "required_languages", "rights_territories", "genres", "channels", "themes",
    "production_countries", "primary_languages", "target_audience", "release_profile",
}
_TEXT_CLAIMS = {"format", "tone", "content_form"}
_NUMBER_CLAIMS = {"minimum_budget_gbp", "maximum_budget_gbp", "budget_gbp"}


@dataclass(frozen=True)
class CommercialCatalogue:
    companies: tuple[CompanyProfile, ...]
    comparables: tuple[ComparableProfile, ...]


def _date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(value)


def _source(value: str, verified_on: date, today: date) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and verified_on <= today


def _valid_claim_value(field: str, value: object) -> bool:
    if field in _LIST_CLAIMS:
        return isinstance(value, list) and bool(value) and all(
            isinstance(item, str) and bool(item.strip()) for item in value
        )
    if field in _TEXT_CLAIMS:
        return isinstance(value, str) and bool(value.strip())
    if field in _NUMBER_CLAIMS:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    if field == "active":
        return isinstance(value, bool)
    if field == "role":
        values = value if isinstance(value, list) else [value]
        return bool(values) and all(item in {"sales_agent", "distributor"} for item in values)
    return False


def _claims(raw: object, allowed: set[str], *, today: date) -> dict[str, SourcedValue]:
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("Commercial profile has malformed or unknown claims")
    claims = {}
    for field, payload in raw.items():
        if not isinstance(payload, dict):
            raise ValueError(f"Commercial claim {field} is not structured")
        try:
            claim = SourcedValue(
                payload["value"], payload["source_url"], _date(payload["verified_on"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Commercial claim {field} lacks source evidence") from exc
        if not _valid_claim_value(field, claim.value) or not claim.known(today):
            raise ValueError(f"Commercial claim {field} lacks usable source evidence")
        claims[field] = claim
    return claims


def parse_commercial_catalogue(
    company_rows: list[dict], comparable_rows: list[dict], relationship_rows: list[dict],
    *, today: date,
) -> CommercialCatalogue:
    companies = []
    for row in company_rows:
        if row.get("review_state") != _APPROVED:
            continue
        claims = _claims(row.get("claims"), _COMPANY_CLAIMS, today=today)
        if not row.get("id") or not row.get("name") or not {"role", "active"} <= claims.keys():
            raise ValueError("Approved company lacks identity, role or active evidence")
        if row.get("reviewed_on") is None or _date(row["reviewed_on"]) > today:
            raise ValueError("Approved company has invalid review date")
        # Two fields the scorer reads that are columns rather than claims: they
        # are normalisations of the freeze's own vocabulary, not assertions with
        # their own source, and giving them a SourcedValue would invent a
        # provenance nobody recorded. An unrecognised route reads as unknown —
        # the state, not the absence — because a route nobody established is
        # never an open door.
        route = str(row.get("access_route") or "").strip() or ACCESS_UNKNOWN
        companies.append(CompanyProfile(
            id=row["id"], name=row["name"], rules_complete=bool(row.get("rules_complete")),
            access_route=route if route in ACCESS_LABELS else ACCESS_UNKNOWN,
            portfolio_group=str(row.get("portfolio_group") or "").strip() or None,
            **claims,
        ))

    comparables = []
    for row in comparable_rows:
        if row.get("review_state") != _APPROVED:
            continue
        verified_on = _date(row["verified_on"])
        if (
            not row.get("id") or not row.get("title")
            or not _source(row.get("source_url") or "", verified_on, today)
            or row.get("reviewed_on") is None or _date(row["reviewed_on"]) > today
        ):
            raise ValueError("Approved comparable lacks usable title evidence")
        claims = _claims(row.get("claims"), _COMPARABLE_CLAIMS, today=today)
        comparables.append(ComparableProfile(
            id=row["id"], title=row["title"], source_url=row["source_url"],
            verified_on=verified_on, **claims,
        ))

    by_comparable = {item.id: [] for item in comparables}
    company_ids = {item.id for item in companies}
    for row in relationship_rows:
        if row.get("review_state") != _APPROVED:
            continue
        verified_on = _date(row["verified_on"])
        kind = row.get("target_kind")
        if (
            row.get("comparable_id") not in by_comparable
            or kind not in _RELATIONSHIP_KINDS
            or not row.get("target_id") or not row.get("relationship_type")
            or (kind == "COMPANY" and row["target_id"] not in company_ids)
            or not _source(row.get("source_url") or "", verified_on, today)
        ):
            raise ValueError("Approved comparable relationship lacks verified endpoints")
        by_comparable[row["comparable_id"]].append(ComparableRelationship(
            target_kind=kind, target_id=row["target_id"],
            relationship_type=row["relationship_type"],
            source_url=row["source_url"], verified_on=verified_on,
        ))
    return CommercialCatalogue(
        tuple(companies),
        tuple(replace(item, relationships=tuple(by_comparable[item.id])) for item in comparables),
    )


def load_commercial_catalogue(engine: sa.Engine, *, today: date) -> CommercialCatalogue:
    """Read staging only; no side effects and no legacy-row auto-promotion."""
    with engine.connect() as conn:
        required = {
            "commercial_company_profiles", "commercial_comparable_profiles",
            "commercial_comparable_relationships",
        }
        if not required <= set(sa.inspect(conn).get_table_names()):
            raise RuntimeError("Apply the commercial staging migration first")
        metadata = sa.MetaData()
        company = sa.Table("commercial_company_profiles", metadata, autoload_with=conn)
        comparable = sa.Table("commercial_comparable_profiles", metadata, autoload_with=conn)
        relationship = sa.Table("commercial_comparable_relationships", metadata, autoload_with=conn)
        return parse_commercial_catalogue(
            [dict(row) for row in conn.execute(sa.select(company)).mappings()],
            [dict(row) for row in conn.execute(sa.select(comparable)).mappings()],
            [dict(row) for row in conn.execute(sa.select(relationship)).mappings()],
            today=today,
        )
