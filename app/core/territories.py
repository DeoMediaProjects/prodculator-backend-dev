"""Canonical territory registry for the Prodculator platform.

Every place the codebase references a country or sub-territory should import
from this module to guarantee consistent naming.

Usage examples
--------------
    from app.core.territories import Territory, resolve_territory

    t = Territory.UNITED_KINGDOM
    t.label          # "United Kingdom"
    t.iso            # "GB"
    t.parent         # None  (top-level country)

    sub = Territory.SCOTLAND
    sub.label        # "Scotland"
    sub.iso          # "GB"
    sub.parent       # Territory.UNITED_KINGDOM

    # Resolve any user-supplied string (case-insensitive, alias-aware)
    resolve_territory("UK")          # Territory.UNITED_KINGDOM
    resolve_territory("usa")         # Territory.UNITED_STATES
    resolve_territory("GB")          # Territory.UNITED_KINGDOM
    resolve_territory("New York")    # Territory.NEW_YORK
"""

from __future__ import annotations

import enum
from typing import NamedTuple


class _TerritoryInfo(NamedTuple):
    label: str
    iso: str
    aliases: tuple[str, ...]
    parent: str | None  # enum member name of parent, or None


class Territory(enum.Enum):
    """Canonical territory list.

    Each member's *value* is a ``_TerritoryInfo`` named-tuple so we can attach
    ``label``, ``iso``, ``aliases``, and ``parent`` without subclassing.
    """

    # ── Top-level countries ─────────────────────────────────────────────
    UNITED_KINGDOM = _TerritoryInfo(
        label="United Kingdom",
        iso="GB",
        aliases=("UK", "GB", "Great Britain", "Britain"),
        parent=None,
    )
    IRELAND = _TerritoryInfo(
        label="Ireland",
        iso="IE",
        aliases=("IE", "Republic of Ireland", "Eire"),
        parent=None,
    )
    UNITED_STATES = _TerritoryInfo(
        label="United States",
        iso="US",
        aliases=("US", "USA", "United States of America", "America"),
        parent=None,
    )
    CANADA = _TerritoryInfo(
        label="Canada",
        iso="CA",
        aliases=("CA",),
        parent=None,
    )
    FRANCE = _TerritoryInfo(
        label="France",
        iso="FR",
        aliases=("FR",),
        parent=None,
    )
    GERMANY = _TerritoryInfo(
        label="Germany",
        iso="DE",
        aliases=("DE", "Deutschland"),
        parent=None,
    )
    SPAIN = _TerritoryInfo(
        label="Spain",
        iso="ES",
        aliases=("ES", "España"),
        parent=None,
    )
    ITALY = _TerritoryInfo(
        label="Italy",
        iso="IT",
        aliases=("IT", "Italia"),
        parent=None,
    )
    AUSTRALIA = _TerritoryInfo(
        label="Australia",
        iso="AU",
        aliases=("AU", "AUS"),
        parent=None,
    )
    NEW_ZEALAND = _TerritoryInfo(
        label="New Zealand",
        iso="NZ",
        aliases=("NZ", "Aotearoa"),
        parent=None,
    )
    SOUTH_AFRICA = _TerritoryInfo(
        label="South Africa",
        iso="ZA",
        aliases=("ZA", "RSA"),
        parent=None,
    )
    CZECH_REPUBLIC = _TerritoryInfo(
        label="Czech Republic",
        iso="CZ",
        aliases=("CZ", "Czechia"),
        parent=None,
    )
    HUNGARY = _TerritoryInfo(
        label="Hungary",
        iso="HU",
        aliases=("HU", "Magyarország"),
        parent=None,
    )
    ICELAND = _TerritoryInfo(
        label="Iceland",
        iso="IS",
        aliases=("IS", "Ísland"),
        parent=None,
    )
    MALTA = _TerritoryInfo(
        label="Malta",
        iso="MT",
        aliases=("MT",),
        parent=None,
    )
    NIGERIA = _TerritoryInfo(
        label="Nigeria",
        iso="NG",
        aliases=("NG", "Nollywood"),
        parent=None,
    )
    BELGIUM = _TerritoryInfo(
        label="Belgium",
        iso="BE",
        aliases=("BE", "Belgique", "België"),
        parent=None,
    )
    INDIA = _TerritoryInfo(
        label="India",
        iso="IN",
        aliases=("IN",),
        parent=None,
    )
    JAPAN = _TerritoryInfo(
        label="Japan",
        iso="JP",
        aliases=("JP",),
        parent=None,
    )
    MOROCCO = _TerritoryInfo(
        label="Morocco",
        iso="MA",
        aliases=("MA", "Maroc"),
        parent=None,
    )
    NETHERLANDS = _TerritoryInfo(
        label="Netherlands",
        iso="NL",
        aliases=("NL", "Holland", "The Netherlands"),
        parent=None,
    )
    PORTUGAL = _TerritoryInfo(
        label="Portugal",
        iso="PT",
        aliases=("PT",),
        parent=None,
    )
    ROMANIA = _TerritoryInfo(
        label="Romania",
        iso="RO",
        aliases=("RO",),
        parent=None,
    )
    SERBIA = _TerritoryInfo(
        label="Serbia",
        iso="RS",
        aliases=("RS",),
        parent=None,
    )
    SINGAPORE = _TerritoryInfo(
        label="Singapore",
        iso="SG",
        aliases=("SG",),
        parent=None,
    )
    SOUTH_KOREA = _TerritoryInfo(
        label="South Korea",
        iso="KR",
        aliases=("KR", "Korea", "Republic of Korea"),
        parent=None,
    )

    # ── UK sub-territories (nations) ────────────────────────────────────
    ENGLAND = _TerritoryInfo(
        label="England",
        iso="GB",
        aliases=(),
        parent="UNITED_KINGDOM",
    )
    SCOTLAND = _TerritoryInfo(
        label="Scotland",
        iso="GB",
        aliases=(),
        parent="UNITED_KINGDOM",
    )
    WALES = _TerritoryInfo(
        label="Wales",
        iso="GB",
        aliases=("Cymru",),
        parent="UNITED_KINGDOM",
    )
    NORTHERN_IRELAND = _TerritoryInfo(
        label="Northern Ireland",
        iso="GB",
        aliases=("NI",),
        parent="UNITED_KINGDOM",
    )

    # ── US sub-territories (states) ─────────────────────────────────────
    CALIFORNIA = _TerritoryInfo(
        label="California",
        iso="US",
        aliases=("CA-US",),  # avoid clash with Canada "CA"
        parent="UNITED_STATES",
    )
    NEW_YORK = _TerritoryInfo(
        label="New York",
        iso="US",
        aliases=("NY",),
        parent="UNITED_STATES",
    )
    GEORGIA_USA = _TerritoryInfo(
        label="Georgia (USA)",
        iso="US",
        aliases=("GA",),
        parent="UNITED_STATES",
    )
    LOUISIANA = _TerritoryInfo(
        label="Louisiana",
        iso="US",
        aliases=("LA-US",),  # avoid clash with Los Angeles shorthand
        parent="UNITED_STATES",
    )
    NEW_MEXICO = _TerritoryInfo(
        label="New Mexico",
        iso="US",
        aliases=("NM",),
        parent="UNITED_STATES",
    )
    ILLINOIS = _TerritoryInfo(
        label="Illinois",
        iso="US",
        aliases=("IL",),
        parent="UNITED_STATES",
    )

    # ── Canada sub-territories (provinces) ──────────────────────────────
    ONTARIO = _TerritoryInfo(
        label="Ontario",
        iso="CA",
        aliases=("ON",),
        parent="CANADA",
    )
    BRITISH_COLUMBIA = _TerritoryInfo(
        label="British Columbia",
        iso="CA",
        aliases=("BC",),
        parent="CANADA",
    )
    QUEBEC = _TerritoryInfo(
        label="Quebec",
        iso="CA",
        aliases=("QC", "Québec"),
        parent="CANADA",
    )
    ALBERTA = _TerritoryInfo(
        label="Alberta",
        iso="CA",
        aliases=("AB",),
        parent="CANADA",
    )

    # ── Australia sub-territories (states) ──────────────────────────────
    NEW_SOUTH_WALES = _TerritoryInfo(
        label="New South Wales",
        iso="AU",
        aliases=("NSW",),
        parent="AUSTRALIA",
    )
    VICTORIA_AU = _TerritoryInfo(
        label="Victoria",
        iso="AU",
        aliases=("VIC",),
        parent="AUSTRALIA",
    )
    QUEENSLAND = _TerritoryInfo(
        label="Queensland",
        iso="AU",
        aliases=("QLD",),
        parent="AUSTRALIA",
    )

    # ── South Africa sub-territories (provinces) ────────────────────────
    WESTERN_CAPE = _TerritoryInfo(
        label="Western Cape",
        iso="ZA",
        aliases=("WC",),
        parent="SOUTH_AFRICA",
    )
    GAUTENG = _TerritoryInfo(
        label="Gauteng",
        iso="ZA",
        aliases=("GP",),
        parent="SOUTH_AFRICA",
    )
    KWAZULU_NATAL = _TerritoryInfo(
        label="KwaZulu-Natal",
        iso="ZA",
        aliases=("KZN",),
        parent="SOUTH_AFRICA",
    )

    # ── Germany sub-territories (states) ────────────────────────────────
    BAVARIA = _TerritoryInfo(
        label="Bavaria",
        iso="DE",
        aliases=("Bayern", "BY"),
        parent="GERMANY",
    )
    BERLIN = _TerritoryInfo(
        label="Berlin",
        iso="DE",
        aliases=("BE",),
        parent="GERMANY",
    )

    # ── Spain sub-territories (regions) ─────────────────────────────────
    CANARY_ISLANDS = _TerritoryInfo(
        label="Canary Islands",
        iso="ES",
        aliases=("Canarias",),
        parent="SPAIN",
    )

    # ── France sub-territories (regions) ────────────────────────────────
    ILE_DE_FRANCE = _TerritoryInfo(
        label="Île-de-France",
        iso="FR",
        aliases=("Ile-de-France", "IDF", "Paris Region"),
        parent="FRANCE",
    )

    # ── Special / supra-national ────────────────────────────────────────
    EUROPEAN_UNION = _TerritoryInfo(
        label="European Union",
        iso="EU",
        aliases=("EU",),
        parent=None,
    )

    # ── Convenience properties ──────────────────────────────────────────

    @property
    def label(self) -> str:
        return self.value.label

    @property
    def iso(self) -> str:
        return self.value.iso

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.value.aliases

    @property
    def parent(self) -> Territory | None:
        name = self.value.parent
        if name is None:
            return None
        return Territory[name]

    @property
    def is_country(self) -> bool:
        """True if this is a top-level country (not a sub-territory)."""
        return self.value.parent is None and self.iso != "EU"

    @property
    def is_sub_territory(self) -> bool:
        return self.value.parent is not None

    # ── Class-level helpers ─────────────────────────────────────────────

    @classmethod
    def countries(cls) -> list[Territory]:
        """Return all top-level country members (no sub-territories, no EU)."""
        return [t for t in cls if t.is_country]

    @classmethod
    def sub_territories_of(cls, country: Territory) -> list[Territory]:
        """Return all sub-territories belonging to a given country."""
        return [t for t in cls if t.parent == country]


# ── Lookup index (built once at import time) ────────────────────────────

_LOOKUP: dict[str, Territory] = {}


def _build_lookup() -> None:
    """Populate a case-insensitive lookup table:
    label -> Territory, each alias -> Territory, iso -> Territory (countries only).
    """
    for t in Territory:
        _LOOKUP[t.label.lower()] = t
        for alias in t.aliases:
            key = alias.lower()
            # Don't overwrite a more-specific match (e.g. "CA" should be Canada,
            # not California whose alias is "CA-US")
            if key not in _LOOKUP:
                _LOOKUP[key] = t
        # ISO codes map to the *country-level* member only
        if t.is_country:
            _LOOKUP[t.iso.lower()] = t


_build_lookup()


def resolve_territory(name: str) -> Territory | None:
    """Resolve a user-supplied territory string to a Territory enum member.

    Case-insensitive.  Recognises canonical labels, ISO codes, and aliases.
    Returns ``None`` if no match is found.
    """
    if not name:
        return None
    return _LOOKUP.get(name.strip().lower())


# ── Derived mappings (replace old hand-maintained dicts) ────────────────

def territory_to_iso() -> dict[str, str]:
    """Return {label -> iso} for every territory, equivalent to the old
    ``_TERRITORY_TO_ISO`` dict in ``reports/service.py``."""
    return {t.label: t.iso for t in Territory}


def iso_to_territory() -> dict[str, str]:
    """Return {iso -> label} for top-level countries only."""
    return {t.iso: t.label for t in Territory if t.is_country}
