"""The frozen 100-point strategic fit contract, and the portfolio it selects.

Two behaviours carry most of the weight here. An unknown component scores zero
against the full 100 rather than being renormalised away, so a thin profile
cannot outrank a well-sourced one by having less known about it. And a score is
a modelled fit, never an acquisition probability — nothing in these tests or the
module they cover converts one into the other.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.modules.reports.commercial_freeze import (
    ACCESS_CONTACT_PUBLISHED,
    ACCESS_DIRECT,
    ACCESS_NO_UNSOLICITED,
    ACCESS_REPRESENTATIVE_ONLY,
    ACCESS_UNKNOWN,
    RELATIONSHIP_DISTRIBUTION,
    RELATIONSHIP_SALES,
    RELATIONSHIP_UNTYPED,
    UnreviewedPhrase,
    acquisition_formats,
    commercial_roles,
    is_current_brand,
    normalise_access_route,
    normalise_relationship_type,
    portfolio_group,
    rights_territories,
)
from app.modules.reports.commercial_scoring import (
    ACCESS_ROUTE_UNKNOWN,
    COMPONENT_WEIGHTS,
    MAX_SCORE,
    NOT_SUITABLE,
    POTENTIAL_MATCH_NEEDS_CONFIRMATION,
    STRATEGIC_MATCH,
    ComponentScore,
    access_component,
    overlap_component,
    resolve_match_state,
    score_strategic_fit,
    select_portfolio,
)

SNAPSHOT = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "handoff_snapshots"
    / "sales_distribution_v1_2026-09-16.json"
)


# ── The frozen weights ───────────────────────────────────────────────────────


def test_the_weights_total_one_hundred():
    assert MAX_SCORE == 100


def test_the_weights_are_the_ones_the_implementation_note_freezes():
    assert COMPONENT_WEIGHTS == {
        "commercial_comparable_evidence": 25,
        "genre_content_audience_fit": 20,
        "territory_rights_fit": 15,
        "format_scale_fit": 10,
        "festival_market_intersection": 10,
        "slate_similarity": 10,
        "lifecycle_stage_fit": 5,
        "access_path_quality": 5,
    }


def test_a_component_weighted_off_contract_is_rejected():
    with pytest.raises(ValueError, match="frozen at 25"):
        score_strategic_fit(
            {
                "commercial_comparable_evidence": ComponentScore(
                    "commercial_comparable_evidence", 40, 1.0
                )
            }
        )


def test_an_invented_component_is_rejected():
    with pytest.raises(ValueError, match="not a frozen score component"):
        score_strategic_fit({"vibes": ComponentScore("vibes", 10, 1.0)})


def test_a_fraction_outside_its_weight_is_rejected():
    with pytest.raises(ValueError, match="share of its weight"):
        score_strategic_fit(
            {
                "lifecycle_stage_fit": ComponentScore(
                    "lifecycle_stage_fit", 5, 1.5
                )
            }
        )


# ── The UNKNOWN rule ─────────────────────────────────────────────────────────


def test_a_company_with_nothing_known_scores_zero():
    fit = score_strategic_fit({})
    assert fit.score == 0
    assert fit.components_known == 0
    assert len(fit.components) == 8


def test_an_unknown_component_is_not_renormalised_out_of_the_denominator():
    """The rule that stops a thin profile outranking a well-sourced one.

    Two known components at full marks are 30 of 100, not 100 of 100.
    """
    fit = score_strategic_fit(
        {
            "territory_rights_fit": ComponentScore("territory_rights_fit", 15, 1.0),
            "format_scale_fit": ComponentScore("format_scale_fit", 10, 1.0),
            "lifecycle_stage_fit": ComponentScore("lifecycle_stage_fit", 5, 1.0),
        }
    )
    assert fit.score == 30


def test_a_thin_profile_cannot_outrank_a_well_sourced_one():
    thin = score_strategic_fit(
        {"territory_rights_fit": ComponentScore("territory_rights_fit", 15, 1.0)}
    )
    sourced = score_strategic_fit(
        {
            key: ComponentScore(key, weight, 0.5)
            for key, weight in COMPONENT_WEIGHTS.items()
        }
    )
    assert sourced.score > thin.score
    assert sourced.components_known > thin.components_known


def test_known_with_no_overlap_differs_from_unknown():
    """Both score zero points and they are not the same claim."""
    known = overlap_component(
        "genre_content_audience_fit", ["drama"], ["horror"], evidence_prefix="Genre"
    )
    unknown = overlap_component(
        "genre_content_audience_fit", None, ["horror"], evidence_prefix="Genre"
    )
    assert known.points == unknown.points == 0
    assert known.is_known
    assert not unknown.is_known


def test_an_empty_company_scope_is_unknown_not_a_universal_match():
    component = overlap_component(
        "format_scale_fit", ["feature"], [], evidence_prefix="Format"
    )
    assert not component.is_known


def test_a_full_house_scores_one_hundred():
    fit = score_strategic_fit(
        {
            key: ComponentScore(key, weight, 1.0)
            for key, weight in COMPONENT_WEIGHTS.items()
        }
    )
    assert fit.score == 100
    assert fit.components_known == 8


# ── Access routes ────────────────────────────────────────────────────────────


def test_a_public_contact_is_not_an_open_door():
    """The implementation note's explicit prohibition, over half the catalogue."""
    for raw in ("PUBLIC_ACQUISITIONS_CONTACT", "PUBLIC_CONTACT_AVAILABLE"):
        assert (
            normalise_access_route({"access_route_status": raw})
            == ACCESS_CONTACT_PUBLISHED
        )


def test_only_direct_open_says_a_producer_may_submit():
    assert normalise_access_route({"access_route_status": "DIRECT_OPEN"}) == ACCESS_DIRECT


def test_a_market_route_is_representative_only():
    assert (
        normalise_access_route({"access_route_status": "REPRESENTATIVE_OR_MARKET_ROUTE"})
        == ACCESS_REPRESENTATIVE_ONLY
    )


def test_a_stated_refusal_outranks_a_published_contact():
    """The narrower fact is the one a producer must act on."""
    row = {
        "access_route_status": "PUBLIC_ACQUISITIONS_CONTACT",
        "unsolicited_policy": "DOES_NOT_ACCEPT_UNSOLICITED",
    }
    assert normalise_access_route(row) == ACCESS_NO_UNSOLICITED


def test_unrecognised_prose_stays_unknown():
    row = {"access_route_status": "UNKNOWN", "submission_route": "Acquisitions team exists"}
    assert normalise_access_route(row) == ACCESS_UNKNOWN


def test_an_unknown_route_is_an_unknown_component():
    assert not access_component(ACCESS_UNKNOWN).is_known


def test_a_stated_refusal_is_a_known_component_scoring_zero():
    component = access_component(ACCESS_NO_UNSOLICITED)
    assert component.is_known
    assert component.points == 0


def test_a_direct_route_scores_the_full_component():
    assert access_component(ACCESS_DIRECT).points == 5


# ── Relationship semantics ───────────────────────────────────────────────────


def test_sales_labels_normalise_together():
    for raw in ("SALES_HANDLED", "WORLD_SALES", "SALES"):
        assert normalise_relationship_type(raw) == {RELATIONSHIP_SALES}


def test_a_distribution_relationship_is_not_a_sales_relationship():
    """Inferring one from the other is what the implementation note forbids."""
    assert normalise_relationship_type("DISTRIBUTOR") == {RELATIONSHIP_DISTRIBUTION}
    assert RELATIONSHIP_SALES not in normalise_relationship_type("US_DISTRIBUTOR")


def test_a_compound_label_keeps_both_limbs():
    assert normalise_relationship_type("WORLD_SALES_DISTRIBUTION") == {
        RELATIONSHIP_SALES,
        RELATIONSHIP_DISTRIBUTION,
    }


def test_production_is_not_a_commercial_route():
    assert normalise_relationship_type("WORLD_SALES_PRODUCTION") == {RELATIONSHIP_SALES}


def test_an_unrecognised_label_is_untyped_not_defaulted():
    assert normalise_relationship_type("SOMETHING_NEW") == {RELATIONSHIP_UNTYPED}
    assert normalise_relationship_type(None) == {RELATIONSHIP_UNTYPED}


# ── Match states ─────────────────────────────────────────────────────────────


def _fit(score: int, *, unknowns: bool = False):
    components = {
        key: ComponentScore(key, weight, None if unknowns else 0.0)
        for key, weight in COMPONENT_WEIGHTS.items()
    }
    components["commercial_comparable_evidence"] = ComponentScore(
        "commercial_comparable_evidence", 25, min(score / 25, 1.0)
    )
    return score_strategic_fit(components)


def test_a_hard_gate_failure_outranks_any_score():
    state = resolve_match_state(
        _fit(25), access_route=ACCESS_DIRECT, hard_gate_failed=True,
        conditions_to_confirm=(),
    )
    assert state == NOT_SUITABLE


def test_a_thinly_supported_company_is_not_offered():
    state = resolve_match_state(
        _fit(5), access_route=ACCESS_DIRECT, hard_gate_failed=False,
        conditions_to_confirm=(),
    )
    assert state == NOT_SUITABLE


def test_an_unestablished_route_is_its_own_state():
    """Different from 'needs confirmation': it sends a producer to other work."""
    state = resolve_match_state(
        _fit(25), access_route=ACCESS_UNKNOWN, hard_gate_failed=False,
        conditions_to_confirm=(),
    )
    assert state == ACCESS_ROUTE_UNKNOWN


def test_a_stated_refusal_reports_as_an_access_state_not_as_unsuitable():
    state = resolve_match_state(
        _fit(25), access_route=ACCESS_NO_UNSOLICITED, hard_gate_failed=False,
        conditions_to_confirm=(),
    )
    assert state == ACCESS_ROUTE_UNKNOWN


def test_an_outstanding_condition_downgrades_a_strategic_match():
    state = resolve_match_state(
        _fit(25), access_route=ACCESS_DIRECT, hard_gate_failed=False,
        conditions_to_confirm=("rights territory scope not verified",),
    )
    assert state == POTENTIAL_MATCH_NEEDS_CONFIRMATION


def test_a_fully_sourced_company_with_a_route_is_a_strategic_match():
    fit = score_strategic_fit(
        {key: ComponentScore(key, weight, 1.0) for key, weight in COMPONENT_WEIGHTS.items()}
    )
    state = resolve_match_state(
        fit, access_route=ACCESS_DIRECT, hard_gate_failed=False,
        conditions_to_confirm=(),
    )
    assert state == STRATEGIC_MATCH


# ── Portfolio selection ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Candidate:
    name: str
    score: int
    group: str
    route: str
    state: str = STRATEGIC_MATCH


def _select(candidates, entitlement=5):
    return select_portfolio(
        candidates,
        entitlement=entitlement,
        state_of=lambda c: c.state,
        score_of=lambda c: c.score,
        group_of=lambda c: c.group,
        route_of=lambda c: c.route,
        name_of=lambda c: c.name,
    )


def test_a_parent_and_its_label_take_one_slot():
    chosen = _select(
        [
            _Candidate("Parent", 90, "MUBI_GROUP", ACCESS_DIRECT),
            _Candidate("Label", 85, "MUBI_GROUP", ACCESS_DIRECT),
            _Candidate("Other", 70, "OTHER", ACCESS_DIRECT),
        ],
        entitlement=2,
    )
    assert [c.name for c in chosen] == ["Parent", "Other"]


def test_the_higher_scoring_row_takes_the_group_slot():
    chosen = _select(
        [
            _Candidate("Label", 85, "MUBI_GROUP", ACCESS_DIRECT),
            _Candidate("Parent", 90, "MUBI_GROUP", ACCESS_DIRECT),
        ],
        entitlement=1,
    )
    assert [c.name for c in chosen] == ["Parent"]


def test_routes_diversify_so_a_producer_is_not_left_with_no_options():
    chosen = _select(
        [
            _Candidate("A", 90, "A", ACCESS_UNKNOWN),
            _Candidate("B", 88, "B", ACCESS_UNKNOWN),
            _Candidate("C", 60, "C", ACCESS_DIRECT),
        ],
        entitlement=2,
    )
    assert {c.name for c in chosen} == {"A", "C"}


def test_diversification_never_drops_the_strongest_candidate():
    chosen = _select(
        [
            _Candidate("Top", 99, "A", ACCESS_UNKNOWN),
            _Candidate("Mid", 50, "B", ACCESS_DIRECT),
            _Candidate("Low", 30, "C", ACCESS_REPRESENTATIVE_ONLY),
        ],
        entitlement=1,
    )
    assert [c.name for c in chosen] == ["Top"]


def test_results_are_presented_in_rank_order():
    chosen = _select(
        [
            _Candidate("Weak", 40, "A", ACCESS_DIRECT),
            _Candidate("Strong", 90, "B", ACCESS_UNKNOWN),
        ],
        entitlement=2,
    )
    assert [c.name for c in chosen] == ["Strong", "Weak"]


def test_a_confirmed_match_ranks_above_a_higher_scoring_unconfirmed_one():
    chosen = _select(
        [
            _Candidate("Unconfirmed", 95, "A", ACCESS_DIRECT,
                       POTENTIAL_MATCH_NEEDS_CONFIRMATION),
            _Candidate("Confirmed", 60, "B", ACCESS_DIRECT, STRATEGIC_MATCH),
        ],
        entitlement=2,
    )
    assert [c.name for c in chosen] == ["Confirmed", "Unconfirmed"]


def test_an_unsuitable_company_never_occupies_a_slot():
    chosen = _select(
        [
            _Candidate("No", 95, "A", ACCESS_DIRECT, NOT_SUITABLE),
            _Candidate("Yes", 40, "B", ACCESS_DIRECT),
        ],
        entitlement=5,
    )
    assert [c.name for c in chosen] == ["Yes"]


def test_the_universe_is_ranked_before_the_package_depth_applies():
    """5 and 10 are the same ranking truncated, never two different searches."""
    candidates = [
        _Candidate(f"C{i:02d}", 100 - i, f"G{i}", ACCESS_DIRECT) for i in range(20)
    ]
    five = _select(candidates, entitlement=5)
    ten = _select(candidates, entitlement=10)
    assert [c.name for c in five] == [c.name for c in ten[:5]]


def test_no_entitlement_returns_nothing():
    assert _select([_Candidate("A", 90, "A", ACCESS_DIRECT)], entitlement=0) == ()


# ── Against the real freeze ──────────────────────────────────────────────────


def test_every_frozen_access_label_normalises():
    """No raw label in the reviewed workbook falls through to a guess."""
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    routes = {normalise_access_route(row) for row in payload["companies"]}
    assert routes <= {
        ACCESS_DIRECT,
        ACCESS_CONTACT_PUBLISHED,
        ACCESS_REPRESENTATIVE_ONLY,
        ACCESS_NO_UNSOLICITED,
        ACCESS_UNKNOWN,
    }


def test_most_of_the_freeze_is_not_an_open_access_route():
    """A sanity check on the catalogue, and on the rule that governs it.

    Only three of 101 companies state they accept direct submissions. If this
    ever rises sharply without the source changing, a published contact has
    probably started being read as an invitation again.
    """
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    routes = [normalise_access_route(row) for row in payload["companies"]]
    assert routes.count(ACCESS_DIRECT) == 3
    assert routes.count(ACCESS_CONTACT_PUBLISHED) == 56


def test_every_frozen_relationship_type_normalises_to_a_known_activity():
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    untyped = [
        row
        for row in payload["relationships"]
        if normalise_relationship_type(row.get("relationship_type"))
        == {RELATIONSHIP_UNTYPED}
    ]
    assert untyped == []


def test_the_freeze_groups_its_three_parent_label_pairs():
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    groups: dict[str, int] = {}
    for row in payload["companies"]:
        key = portfolio_group(row)
        groups[key] = groups.get(key, 0) + 1
    shared = {key: count for key, count in groups.items() if count > 1}
    assert len(shared) == 3


def test_the_freeze_carries_one_retired_brand():
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    retired = [row for row in payload["companies"] if not is_current_brand(row)]
    assert len(retired) == 1


# ── Roles and scope phrases ──────────────────────────────────────────────────


def test_hybrid_companies_carry_both_roles():
    assert commercial_roles({"company_roles": "HYBRID_SALES_DISTRIBUTION"}) == (
        "distributor",
        "sales_agent",
    )


def test_finance_and_production_contribute_no_role():
    """A company that also finances is not thereby a third kind of route."""
    assert commercial_roles({"company_roles": "SALES_FINANCE_PRODUCTION"}) == (
        "sales_agent",
    )


def test_an_unknown_role_token_yields_nothing():
    assert commercial_roles({"company_roles": "TALENT_AGENCY"}) == ()
    assert commercial_roles({}) == ()


def test_company_type_is_only_a_fallback():
    """The reviewed column wins; the compound one answers when it is blank."""
    row = {"company_roles": "INTERNATIONAL_SALES", "company_type": "DISTRIBUTOR_STUDIO"}
    assert commercial_roles(row) == ("sales_agent",)
    assert commercial_roles({"company_type": "DISTRIBUTOR_STUDIO"}) == ("distributor",)


def test_a_worldwide_scope_types_and_a_hedged_one_does_not():
    """The distinction the tables exist for, in one pair.

    "Worldwide" is a rights scope. "Worldwide / major territories" is the same
    word qualified into something narrower, and a prefix or substring match
    would read them as the same claim.
    """
    assert rights_territories({"territory_scope": "Worldwide"}) == ("worldwide",)
    assert rights_territories({"territory_scope": "Worldwide / major territories"}) == ()


def test_scope_lookup_ignores_case_and_spacing_only():
    assert rights_territories({"territory_scope": "  WORLDWIDE  "}) == ("worldwide",)


def test_a_hedged_format_limb_is_dropped_and_the_definite_ones_kept():
    row = {"formats": "Feature Film; Documentary; Animation where stated"}
    assert acquisition_formats(row) == ("documentary", "feature")


def test_a_blank_scope_is_unknown_rather_than_unreviewed():
    assert rights_territories({"territory_scope": ""}) == ()
    assert acquisition_formats({}) == ()


def test_a_phrase_nobody_reviewed_stops_the_import():
    """The pin moved and a scope nobody read is about to be imported."""
    with pytest.raises(UnreviewedPhrase):
        rights_territories({"territory_scope": "Mars and the outer colonies"})
    with pytest.raises(UnreviewedPhrase):
        acquisition_formats({"formats": "Holograms"})


def test_every_frozen_scope_phrase_has_been_reviewed():
    """No row in the pinned freeze reaches the importer undecided."""
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for row in payload["companies"]:
        rights_territories(row)
        acquisition_formats(row)


def test_every_frozen_company_resolves_to_a_commercial_role():
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert all(commercial_roles(row) for row in payload["companies"])


def test_the_typed_scope_coverage_of_the_freeze():
    """What the tables actually yield, so a silent drop is visible.

    Eighty of 101 rows state a rights scope and 98 state a format. The gap is
    not a defect — it is the prose the freeze warned about — but a change in
    either number means a phrase was retyped, and that is a review decision
    rather than a refactor.
    """
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    companies = payload["companies"]
    assert sum(1 for row in companies if rights_territories(row)) == 80
    assert sum(1 for row in companies if acquisition_formats(row)) == 98
