"""PROD-FIX-008 — fund/genre-territory mismatch.

The Blacbet Master report (a Nigeria/Benin-set West African Macbeth adaptation)
recommended the Busan International Film Festival Asian Cinema Fund as a
near-term priority "that should be submitted immediately", with no restriction
warning. Tester 1 reported the same class of error on Cloaks, where the BFI Doc
Society Fund and Fonds Images Afrique were surfaced for a project outside their
scope.

The cause was a missing dimension rather than a bad record. `nationality_required`
means "restricted to a single country", and Busan's own entry reads "Asian
filmmakers (broad Asia-Pacific definition). No nationality restriction within
Asia" — accurate, but there was nowhere to record that eligibility is bounded by
REGION. Grant matching could filter on format, deadline, staleness,
single-country nationality, genre and budget, and nothing else.

Separately, `loc_continents` was initialised empty and never written, so the one
region-aware scoring signal that did exist was unreachable.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from datetime import date
from pathlib import Path

import pytest

from app.core.regions import regions_for_territory, satisfies
from app.modules.reports.matching import match_grants

_REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 8, 4)


def _fund(name: str, **overrides) -> dict:
    """A fund that passes every pre-existing gate, so only regions vary."""
    base = {
        "fund_name": name,
        "territory": "Global",
        "continent": "Global",
        "eligible_formats": ["feature", "documentary"],
        "genre_tags": ["Drama"],
        "deadline": "2026-10-15",
        "recurrence": "annual",
        "verified_at": "2026-07-04",
        "nationality_required": False,
        "budget_min_usd": None,
        "budget_max_usd": None,
    }
    base.update(overrides)
    return base


def _busan(**overrides) -> dict:
    fields = {
        "territory": "South Korea",
        "continent": "Asia-Pacific",
        "eligible_regions": json.dumps(["Asia"]),
    }
    fields.update(overrides)
    return _fund("Busan International Film Festival — Asian Cinema Fund", **fields)


def _afriff() -> dict:
    return _fund(
        "Africa International Film Festival (AFRIFF) — Short Film Fund",
        territory="Nigeria",
        continent="Africa",
        eligible_regions=json.dumps(["Africa"]),
    )


def _fonds_images_afrique() -> dict:
    return _fund(
        "Fonds Images Afrique — Institut Français",
        territory="Morocco",
        continent="Africa",
        eligible_regions=json.dumps(["Africa"]),
    )


def _unrestricted() -> dict:
    return _fund("DOC/NYC — Fund for Inclusion", territory="Global")


def _blacbet_master() -> dict:
    """Nigeria/Benin-set West African production, as reported."""
    return {
        "format": "feature",
        "genres": ["Drama"],
        "budget_usd": 2_000_000,
        "home_country": "Nigeria",
        "ranked_territories": ["Nigeria", "Western Cape"],
        "script_origin": "Nigeria",
    }


def _names(matches) -> set[str]:
    return {m["grant"]["fund_name"] for m in matches}


# ── The reported case ────────────────────────────────────────────────────────


def test_busan_is_not_recommended_for_a_west_african_production() -> None:
    matches, flags = match_grants(
        [_busan(), _afriff(), _unrestricted()], _blacbet_master(), today=TODAY
    )

    assert "Busan International Film Festival — Asian Cinema Fund" not in _names(matches)

    # Excluded visibly, so the data team can see why.
    mismatch = [f for f in flags if f["flag"] == "region_mismatch"]
    assert len(mismatch) == 1
    assert "Asia" in mismatch[0]["detail"]
    assert "Africa" in mismatch[0]["detail"]


def test_african_funds_still_reach_a_west_african_production() -> None:
    """The fix must not simply suppress regional funds.

    Shorts and regionally-scoped funds are where these productions have their
    genuine, low-risk opportunities — over-filtering would be its own failure.
    """
    matches, _ = match_grants(
        [_busan(), _afriff(), _unrestricted()], _blacbet_master(), today=TODAY
    )
    found = _names(matches)

    assert "Africa International Film Festival (AFRIFF) — Short Film Fund" in found
    assert "DOC/NYC — Fund for Inclusion" in found


def test_matching_region_is_badged_so_the_restriction_is_visible() -> None:
    """Even a valid match should say the fund is regionally bounded."""
    matches, _ = match_grants([_afriff()], _blacbet_master(), today=TODAY)

    badges = matches[0]["badges"]
    assert any("REGIONAL RESTRICTION" in b for b in badges)
    assert any("Africa" in b for b in badges)


# ── The Cloaks case from tester feedback ─────────────────────────────────────


def test_africa_fund_is_not_recommended_for_a_uk_production() -> None:
    cloaks = {
        "format": "short",
        "genres": ["Drama"],
        "budget_usd": 12_000,
        "home_country": "United Kingdom",
        "ranked_territories": ["United Kingdom"],
        "script_origin": "United Kingdom",
    }
    fund = _fonds_images_afrique()
    fund["eligible_formats"] = ["short", "feature", "documentary"]

    matches, flags = match_grants([fund], cloaks, today=TODAY)

    assert not _names(matches)
    assert [f["flag"] for f in flags] == ["region_mismatch"]


# ── Eligible productions are unaffected ──────────────────────────────────────


def test_busan_still_matches_an_asian_production() -> None:
    korean = {
        "format": "feature",
        "genres": ["Drama"],
        "budget_usd": 2_000_000,
        "home_country": "South Korea",
        "ranked_territories": ["South Korea"],
        "script_origin": "South Korea",
    }
    matches, flags = match_grants([_busan()], korean, today=TODAY)

    assert "Busan International Film Festival — Asian Cinema Fund" in _names(matches)
    assert not flags


def test_southeast_asian_production_qualifies_for_a_pan_asian_fund() -> None:
    """Region containment is directed: Southeast Asia sits inside Asia."""
    thai = {
        "format": "feature",
        "genres": ["Drama"],
        "budget_usd": 2_000_000,
        "home_country": "Thailand",
        "ranked_territories": ["Thailand"],
        "script_origin": "Thailand",
    }
    matches, _ = match_grants([_busan()], thai, today=TODAY)
    assert _names(matches)


def test_pan_asian_production_does_not_qualify_for_a_southeast_asian_fund() -> None:
    """...and not the other way round — a Japanese film is not Southeast Asian."""
    sgiff = _fund(
        "Singapore International Film Festival — Southeast Asian Film Fund",
        territory="Singapore",
        continent="Asia-Pacific",
        eligible_regions=json.dumps(["Southeast Asia"]),
    )
    japanese = {
        "format": "feature",
        "genres": ["Drama"],
        "budget_usd": 2_000_000,
        "home_country": "Japan",
        "ranked_territories": ["Japan"],
        "script_origin": "Japan",
    }
    matches, flags = match_grants([sgiff], japanese, today=TODAY)

    assert not _names(matches)
    assert [f["flag"] for f in flags] == ["region_mismatch"]


# ── Fail-open behaviour ──────────────────────────────────────────────────────


def test_unknown_origin_surfaces_the_fund_with_a_warning_rather_than_dropping_it() -> None:
    """Dropping a real opportunity is as much a failure as inventing one.

    When the production's origin is unrecognised the engine cannot assert
    ineligibility, so it shows the restriction instead of acting on it.
    """
    unknown = {
        "format": "feature",
        "genres": ["Drama"],
        "budget_usd": 2_000_000,
        "home_country": "",
        "ranked_territories": [],
        "script_origin": "Wakanda",
    }
    matches, flags = match_grants([_busan()], unknown, today=TODAY)

    assert _names(matches)
    assert any("REGIONAL RESTRICTION" in b for b in matches[0]["badges"])
    assert any("confirm your eligibility" in s for s in matches[0]["signals"])
    assert not flags


def test_malformed_eligible_regions_does_not_exclude_anyone() -> None:
    matches, _ = match_grants(
        [_busan(eligible_regions="{not json")], _blacbet_master(), today=TODAY
    )
    assert _names(matches)


def test_fund_without_eligible_regions_is_unrestricted() -> None:
    matches, _ = match_grants([_unrestricted()], _blacbet_master(), today=TODAY)
    assert _names(matches)


# ── The dead continent-affinity signal ───────────────────────────────────────


def test_continent_affinity_signal_actually_fires() -> None:
    """loc_continents was never populated, so this branch was unreachable."""
    african_fund = _fund(
        "Durban FilmMart — Development Finance Forum",
        territory="KwaZulu-Natal",   # not a ranked territory for this production
        continent="Africa",
    )
    matches, _ = match_grants([african_fund], _blacbet_master(), today=TODAY)

    assert matches, "fund should match on continent affinity"
    assert any("open across Africa" in s for s in matches[0]["signals"])


# ── Region vocabulary ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "territory,expected",
    [
        ("Nigeria", "Africa"),
        ("Benin", "Africa"),
        ("Western Cape", "Africa"),
        ("United Kingdom", "Europe"),
        ("South Korea", "Asia"),
        ("Singapore", "Southeast Asia"),
        ("Mexico", "Latin America"),
        ("Ontario", "North America"),
    ],
)
def test_territory_regions(territory: str, expected: str) -> None:
    assert expected in regions_for_territory(territory)


def test_unknown_territory_yields_no_regions_not_an_exclusion() -> None:
    assert regions_for_territory("Atlantis") == frozenset()
    # ...and an unknown origin therefore satisfies any requirement.
    assert satisfies(frozenset(), ["Asia"])


# ── Dataset ──────────────────────────────────────────────────────────────────


def test_every_mapped_fund_name_exists_in_the_grants_dataset() -> None:
    """A rename upstream would silently leave a fund unrestricted again."""
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "_regions_mig",
            _REPO_ROOT / "alembic" / "versions"
            / "j0e1f2a3b4c5_add_grant_eligible_regions.py",
        )
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]

    refresh = (
        _REPO_ROOT / "alembic" / "versions" / "ac4d5e6f7081_grants_v2_refresh.py"
    ).read_text(encoding="utf8")
    blob = refresh[refresh.index("_SOURCE_ROWS"):]
    blob = blob[blob.index("'''") + 3:]
    blob = blob[: blob.index("'''")]
    dataset_names = {r["fund_name"] for r in json.loads(blob)}

    missing = sorted(set(mig._FUND_REGIONS) - dataset_names)
    assert not missing, f"mapped funds absent from the dataset: {missing}"


def test_busan_is_mapped_to_asia() -> None:
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "_regions_mig2",
            _REPO_ROOT / "alembic" / "versions"
            / "j0e1f2a3b4c5_add_grant_eligible_regions.py",
        )
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            del sys.modules["alembic"]

    assert mig._FUND_REGIONS[
        "Busan International Film Festival — Asian Cinema Fund"
    ] == ["Asia"]
