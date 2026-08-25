"""Canonical jurisdiction IDs for incentive scenarios.

The v2 ingestion specification requires a scenario to identify its jurisdiction by
canonical ID, not by label:

    territory_id     country level, ISO 3166-1 alpha-2, for example GB
    subdivision_id   ISO 3166-2 where the programme exists below country level,
                     for example US-CA, CA-BC, ES-CN

The current territory registry stores sub-territories with the parent's ISO, so
British Columbia and Ontario both read ``CA`` and are indistinguishable by code.
That is fine for a label-driven picker and unsafe once the key selects a financial
formula, which is what the specification means by "current country and territory
validation is permissive".

This module adds the subdivision codes without editing the 53 member registry, and
gives the one resolver that scenario ingestion is allowed to use. Free text may
still drive creative and location analysis; it must never resolve to a programme.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.territories import Territory, resolve_territory

#: ISO 3166-2 subdivision codes for the sub-territories the registry holds.
#:
#: Only subdivisions that are, or could become, their own incentive jurisdiction
#: need a code. The UK nations are included because their regional funds are real
#: even though the statutory credit is UK wide, and a scenario that names Scotland
#: should not silently be read as a separate statutory regime.
_SUBDIVISION_CODES: dict[str, str] = {
    # United Kingdom, one statutory regime, nations kept distinct for regional funds
    "England": "GB-ENG",
    "Scotland": "GB-SCT",
    "Wales": "GB-WLS",
    "Northern Ireland": "GB-NIR",
    # United States, each state is its own programme
    "California": "US-CA",
    "New York": "US-NY",
    "Georgia (USA)": "US-GA",
    "Louisiana": "US-LA",
    "New Mexico": "US-NM",
    "Illinois": "US-IL",
    # Canada, provincial credits interact with the federal one
    "Ontario": "CA-ON",
    "British Columbia": "CA-BC",
    "Quebec": "CA-QC",
    "Alberta": "CA-AB",
    # Australia, offsets are federal; states carry their own selective funds
    "New South Wales": "AU-NSW",
    "Victoria": "AU-VIC",
    "Queensland": "AU-QLD",
    # South Africa, one national programme; provinces are location intelligence
    # only after the reconciliation merges (rows 46, 47, 49)
    "Western Cape": "ZA-WC",
    "Gauteng": "ZA-GP",
    "KwaZulu-Natal": "ZA-KZN",
    # Germany, federal funds; Länder carry selective support
    "Bavaria": "DE-BY",
    "Berlin": "DE-BE",
    # Spain, the Canary Islands are a legally distinct regime
    "Canary Islands": "ES-CN",
    # France, national TRIP; the regional row merges away (row 44)
    "Île-de-France": "FR-IDF",
}

#: Subdivisions that are their own statutory incentive jurisdiction, as opposed to
#: a region inside one. A scenario naming one of these resolves to a subdivision
#: level programme; anything else resolves to its country.
_STATUTORY_SUBDIVISIONS: frozenset[str] = frozenset({
    "US-CA", "US-NY", "US-GA", "US-LA", "US-NM", "US-IL",
    "CA-ON", "CA-BC", "CA-QC", "CA-AB",
    "ES-CN",
})


class UnknownJurisdiction(ValueError):
    """A scenario named a jurisdiction that cannot resolve to a programme."""


@dataclass(frozen=True)
class Jurisdiction:
    """A resolved, canonical scenario jurisdiction."""

    territory_id: str
    subdivision_id: str | None
    label: str
    #: True when the subdivision runs its own statutory programme, so the
    #: calculation is a subdivision level lookup rather than a country one.
    is_statutory_subdivision: bool

    @property
    def scenario_key(self) -> str:
        """Stable key for one scenario, used for deduplication and storage."""
        return self.subdivision_id or self.territory_id


def subdivision_code(territory: Territory) -> str | None:
    """The ISO 3166-2 code for a sub-territory, or None for a country."""
    if not territory.is_sub_territory:
        return None
    return _SUBDIVISION_CODES.get(territory.label)


def resolve_jurisdiction(name: str) -> Jurisdiction:
    """Resolve a label, ISO code or subdivision code to a canonical jurisdiction.

    Raises ``UnknownJurisdiction`` rather than passing the value through. The
    current country validator returns unrecognised input unchanged, which is safe
    for prose and unsafe here: a typo would become a scenario that silently
    matches no programme, and the report would show a territory with no financial
    analysis and no explanation.
    """
    if not name or not str(name).strip():
        raise UnknownJurisdiction(
            "A scenario needs a jurisdiction. An empty value cannot select a "
            "programme."
        )

    raw = str(name).strip()

    # A subdivision code supplied directly, for example US-CA.
    upper = raw.upper()
    for label, code in _SUBDIVISION_CODES.items():
        if code == upper:
            territory = resolve_territory(label)
            assert territory is not None
            return _build(territory)

    territory = resolve_territory(raw)
    if territory is None:
        raise UnknownJurisdiction(
            f"{raw!r} is not a recognised jurisdiction. Incentive scenarios must "
            f"use a canonical territory or subdivision, so a free-text location "
            f"cannot select a programme."
        )
    return _build(territory)


def _build(territory: Territory) -> Jurisdiction:
    code = subdivision_code(territory)
    if territory.is_sub_territory and territory.parent is not None:
        return Jurisdiction(
            territory_id=territory.parent.iso,
            subdivision_id=code,
            label=territory.label,
            is_statutory_subdivision=code in _STATUTORY_SUBDIVISIONS,
        )
    return Jurisdiction(
        territory_id=territory.iso,
        subdivision_id=None,
        label=territory.label,
        is_statutory_subdivision=False,
    )


def is_resolvable(name: str) -> bool:
    """Whether ``name`` can resolve, without raising. For validation messages."""
    try:
        resolve_jurisdiction(name)
    except UnknownJurisdiction:
        return False
    return True


def all_scenario_jurisdictions() -> list[Jurisdiction]:
    """Every jurisdiction a scenario may name, for the picker and for tests."""
    seen: dict[str, Jurisdiction] = {}
    for territory in Territory:
        jurisdiction = _build(territory)
        seen.setdefault(jurisdiction.scenario_key + jurisdiction.label, jurisdiction)
    return sorted(seen.values(), key=lambda j: j.label)
