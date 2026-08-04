"""Regional eligibility vocabulary for fund matching.

PROD-FIX-008. A fund named for a specific region — the Busan International Film
Festival *Asian* Cinema Fund — was recommended as a "submit immediately" action
for a Nigeria/Benin-set West African production, with no restriction warning.
Tester 1 hit the same class of error on Cloaks (BFI Doc Society Fund and Fonds
Images Afrique surfaced for a project outside their scope).

The cause is a missing dimension rather than a bad record. The grants dataset
has ``nationality_required``, which the data team uses to mean "restricted to a
single country" — the Busan entry's own eligibility text reads "Asian filmmakers
(broad Asia-Pacific definition). No nationality restriction **within Asia**", so
``nationality_required = false`` is literally accurate. There was simply nowhere
to record that eligibility is bounded by *region*. Sixteen of the dataset's 114
funds state a regional bound in prose that no structured field captured.

This module supplies the vocabulary for the new ``eligible_regions`` field:

  * ``REGION_CONTAINMENT`` — which regions satisfy a declared requirement
  * ``regions_for_territory`` — the regions a production territory sits in

Deliberately fail-open: a territory this module does not recognise yields no
regions, and the matcher then declines to *assert* ineligibility rather than
dropping a fund the producer might genuinely qualify for. Excluding a real
opportunity is as much a failure as surfacing an impossible one.
"""
from __future__ import annotations

# Canonical region names. These extend the coarse `continent` values already in
# the grants dataset (Europe / Americas / Africa / Asia-Pacific / Global) with
# the sub-regions funds actually name in their eligibility rules.
AFRICA = "Africa"
ASIA = "Asia"
SOUTHEAST_ASIA = "Southeast Asia"
CENTRAL_ASIA = "Central Asia"
EUROPE = "Europe"
EASTERN_EUROPE = "Eastern Europe"
MIDDLE_EAST = "Middle East"
LATIN_AMERICA = "Latin America"
NORTH_AMERICA = "North America"
OCEANIA = "Oceania"

# A production in region X satisfies a fund requiring any region in
# REGION_CONTAINMENT[X]. Southeast Asia is inside Asia, so a Thai production
# qualifies for a pan-Asian fund; the reverse does not hold, which is why this
# is a directed map rather than a symmetric grouping.
REGION_CONTAINMENT: dict[str, frozenset[str]] = {
    AFRICA: frozenset({AFRICA}),
    ASIA: frozenset({ASIA}),
    SOUTHEAST_ASIA: frozenset({SOUTHEAST_ASIA, ASIA}),
    CENTRAL_ASIA: frozenset({CENTRAL_ASIA, ASIA}),
    EUROPE: frozenset({EUROPE}),
    EASTERN_EUROPE: frozenset({EASTERN_EUROPE, EUROPE}),
    MIDDLE_EAST: frozenset({MIDDLE_EAST}),
    LATIN_AMERICA: frozenset({LATIN_AMERICA}),
    NORTH_AMERICA: frozenset({NORTH_AMERICA}),
    OCEANIA: frozenset({OCEANIA}),
}

# Territory / country → the regions it belongs to. Keys are lowercased and
# cover both the platform's own territory labels (including sub-national ones
# such as "Ontario" and "Western Cape") and the country names that appear as a
# script's setting or a producer's home country.
#
# Geography only — this table encodes no eligibility judgement. Which regions a
# given fund accepts is recorded per fund in `eligible_regions`.
_TERRITORY_REGIONS: dict[str, frozenset[str]] = {}


def _add(regions: frozenset[str], *names: str) -> None:
    for n in names:
        _TERRITORY_REGIONS[n.lower()] = regions


_AFRICA = frozenset({AFRICA})
_add(
    _AFRICA,
    "Nigeria", "Benin", "Ghana", "Senegal", "Kenya", "Tanzania", "Uganda",
    "Rwanda", "Ethiopia", "Zimbabwe", "Zambia", "Botswana", "Namibia",
    "Mozambique", "Angola", "Cameroon", "Ivory Coast", "Côte d'Ivoire",
    "Burkina Faso", "Mali", "Niger", "Chad", "Guinea", "Sierra Leone",
    "Liberia", "Togo", "Gabon", "Congo", "Democratic Republic of the Congo",
    "South Africa", "Western Cape", "Gauteng", "KwaZulu-Natal",
    "Morocco", "Algeria", "Tunisia", "Libya", "Sudan", "Somalia",
)
# Egypt sits in both the African and Middle Eastern funding worlds.
_add(frozenset({AFRICA, MIDDLE_EAST}), "Egypt")

_add(
    frozenset({EUROPE}),
    "United Kingdom", "Scotland", "Wales", "Northern Ireland", "England",
    "Ireland", "France", "Île-de-France", "Ile-de-France", "Germany",
    "Bavaria", "Spain", "Canary Islands", "Portugal", "Italy", "Netherlands",
    "Belgium", "Luxembourg", "Austria", "Switzerland", "Denmark", "Sweden",
    "Norway", "Finland", "Iceland", "Malta", "Cyprus", "Greece",
)
_add(
    frozenset({EASTERN_EUROPE, EUROPE}),
    "Hungary", "Czech Republic", "Poland", "Romania", "Serbia", "Bulgaria",
    "Croatia", "Slovakia", "Slovenia", "Estonia", "Latvia", "Lithuania",
    "Ukraine", "Bosnia and Herzegovina", "North Macedonia", "Albania",
    "Montenegro", "Moldova", "Georgia",
)

_add(
    frozenset({NORTH_AMERICA}),
    "United States", "USA", "United States of America", "Canada",
    "California", "New York", "New Mexico", "Louisiana", "Illinois",
    "Georgia (USA)", "Ontario", "Quebec", "British Columbia", "Alberta",
    "Nova Scotia", "Manitoba",
)
_add(
    frozenset({LATIN_AMERICA}),
    "Mexico", "Brazil", "Argentina", "Chile", "Colombia", "Peru", "Uruguay",
    "Bolivia", "Ecuador", "Paraguay", "Venezuela", "Costa Rica", "Panama",
    "Guatemala", "Cuba", "Dominican Republic", "Puerto Rico",
)

_add(
    frozenset({ASIA}),
    "Japan", "South Korea", "Korea", "China", "Hong Kong", "Taiwan",
    "Mongolia", "India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal",
    "Bhutan", "Maldives",
)
_add(
    frozenset({SOUTHEAST_ASIA, ASIA}),
    "Singapore", "Malaysia", "Indonesia", "Thailand", "Philippines",
    "Vietnam", "Myanmar", "Cambodia", "Laos", "Brunei", "Timor-Leste",
)
_add(
    frozenset({CENTRAL_ASIA, ASIA}),
    "Kazakhstan", "Uzbekistan", "Kyrgyzstan", "Tajikistan", "Turkmenistan",
)
_add(
    frozenset({MIDDLE_EAST}),
    "United Arab Emirates", "UAE", "Saudi Arabia", "Qatar", "Kuwait",
    "Bahrain", "Oman", "Jordan", "Lebanon", "Israel", "Palestine", "Iraq",
    "Iran", "Syria", "Yemen", "Turkey",
)

_add(
    frozenset({OCEANIA}),
    "Australia", "New South Wales", "Victoria", "Queensland",
    "South Australia", "Western Australia", "New Zealand", "Fiji",
    "Papua New Guinea",
)


def regions_for_territory(territory: str | None) -> frozenset[str]:
    """Regions a territory belongs to, or an empty set if unrecognised.

    An empty result means "unknown", never "belongs nowhere" — callers must not
    treat it as grounds for exclusion.
    """
    if not territory:
        return frozenset()
    return _TERRITORY_REGIONS.get(str(territory).strip().lower(), frozenset())


def regions_for_territories(territories) -> frozenset[str]:
    """Union of regions across several territories, ignoring unknown ones."""
    out: set[str] = set()
    for t in territories or ():
        out |= regions_for_territory(t)
    return frozenset(out)


def satisfies(production_regions, required_regions) -> bool:
    """True if a production in ``production_regions`` meets ``required_regions``.

    Returns True when either side is empty: an unrestricted fund accepts
    everyone, and an unknown production location cannot be ruled ineligible.
    """
    required = {str(r).strip() for r in (required_regions or ()) if str(r).strip()}
    if not required:
        return True
    if not production_regions:
        return True

    reachable: set[str] = set()
    for region in production_regions:
        reachable |= REGION_CONTAINMENT.get(region, frozenset({region}))
    return bool(reachable & required)
