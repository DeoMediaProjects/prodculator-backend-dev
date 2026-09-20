"""Comparing a legacy report against the v2 orchestration of the same run.

The temptation is to treat this as a diff that should come out empty. It should
not: the two shapes are expected to disagree, and the disagreements are the
deliverable. A comparison that flagged a withdrawn rebate as a failure would
train whoever reads it to ignore the output.
"""
from __future__ import annotations

from app.modules.reports.version_comparison import (
    COMMITTED_FINANCE_WITHDRAWN,
    FIGURE_ADDED,
    FIGURE_WITHDRAWN,
    ROUTING_MOVED,
    SECTION_EMPTIED,
    compare,
    render,
)


def _payload(**sections):
    """A v2 payload with the named sections carrying the given records."""
    keys = [
        "tax_incentive_analysis",
        "grant_funding_opportunities",
        "industry_development_market_strategy",
        "festival_strategy",
        "sales_distribution_strategy",
        "comparable_productions",
    ]
    return {
        "sections": [
            {
                "section_key": key,
                "blocks": (
                    [{"data": {"recommendations": sections[key]}}]
                    if sections.get(key)
                    else []
                ),
            }
            for key in keys
        ],
        "financial_readiness": {
            "documented_committed_finance": sections.get("committed", []),
            "conditional_statutory_benefits": [],
            "selective_pipeline_opportunities": [],
            "strategic_access_opportunities": [],
        },
    }


# ── The regression, in one comparison ────────────────────────────────────────


def test_a_withdrawn_rebate_is_reported_as_expected_not_as_a_failure():
    """Legacy quotes £5.74M; v2 says it has no basis. The rebuild working."""
    legacy = {
        "incentiveEstimates": [{"territory": "UK", "confirmedIncentive": "£5.74M"}],
    }
    result = compare(
        legacy,
        _payload(
            tax_incentive_analysis=[
                {"programme": "AVEC", "amount": None,
                 "calculation_status": "REQUIRES_COST_BREAKDOWN"}
            ]
        ),
    )
    withdrawn = [f for f in result.findings if f.kind == FIGURE_WITHDRAWN]
    assert withdrawn
    assert withdrawn[0].legacy_value == "£5.74M"
    assert withdrawn[0].expected
    assert not result.has_unexpected


def test_a_finance_market_moved_out_of_grants_is_expected():
    legacy = {
        "fundingOpportunities": [
            {"name": "Film London Production Finance Market"},
            {"name": "A national production fund"},
        ]
    }
    result = compare(
        legacy,
        _payload(
            grant_funding_opportunities=[{"name": "A national production fund"}],
            industry_development_market_strategy=[
                {"name": "Film London Production Finance Market"}
            ],
        ),
    )
    moved = [f for f in result.findings if f.kind == ROUTING_MOVED]
    assert [f.legacy_value for f in moved] == [
        "Film London Production Finance Market"
    ]
    assert not result.has_unexpected


def test_a_net_position_with_no_committed_finance_is_expected():
    legacy = {"executiveSummary": {"headlineNetBudget": "approximately £24,262,500"}}
    result = compare(legacy, _payload())
    withdrawn = [f for f in result.findings if f.kind == COMMITTED_FINANCE_WITHDRAWN]
    assert withdrawn
    assert withdrawn[0].expected


def test_a_documented_award_stops_the_committed_finance_finding():
    legacy = {"executiveSummary": {"headlineNetBudget": "£24,262,500"}}
    result = compare(
        legacy, _payload(committed=[{"label": "An awarded grant"}])
    )
    assert not [f for f in result.findings if f.kind == COMMITTED_FINANCE_WITHDRAWN]


# ── What needs a human ───────────────────────────────────────────────────────


def test_a_figure_only_v2_carries_needs_review():
    """The rebuild removes unsupported claims; it does not add new ones."""
    result = compare(
        {"incentiveEstimates": []},
        _payload(tax_incentive_analysis=[{"programme": "AVEC", "note": "£9,000,000"}]),
    )
    added = [f for f in result.findings if f.kind == FIGURE_ADDED]
    assert added
    assert not added[0].expected
    assert result.has_unexpected


def test_a_section_emptied_with_no_rule_behind_it_needs_review():
    legacy = {"comparables": [{"title": "A Film"}, {"title": "Another"}]}
    result = compare(legacy, _payload())
    emptied = [f for f in result.findings if f.kind == SECTION_EMPTIED]
    assert emptied
    assert emptied[0].where == "comparable_productions"
    assert emptied[0].legacy_value == 2
    assert result.has_unexpected


def test_a_section_emptied_by_routing_is_not_reported_twice():
    """Routing already explains its own loss."""
    legacy = {"fundingOpportunities": [{"name": "A market"}]}
    result = compare(
        legacy, _payload(industry_development_market_strategy=[{"name": "A market"}])
    )
    kinds = {f.kind for f in result.findings}
    assert ROUTING_MOVED in kinds
    assert SECTION_EMPTIED not in kinds
    assert not result.has_unexpected


def test_a_section_emptied_by_a_withdrawn_figure_is_not_reported_twice():
    legacy = {"incentiveEstimates": [{"confirmedIncentive": "£5.74M"}]}
    result = compare(legacy, _payload())
    emptied = [
        f
        for f in result.findings
        if f.kind == SECTION_EMPTIED and f.where == "tax_incentive_analysis"
    ]
    assert emptied == []


# ── Money detection ──────────────────────────────────────────────────────────


def test_money_is_found_however_deeply_it_is_nested():
    legacy = {
        "financialAnalysis": {
            "scenarios": [{"rows": [{"note": "saves £1,200,000 net"}]}]
        }
    }
    result = compare(legacy, _payload())
    assert any(f.legacy_value == "£1,200,000" for f in result.findings)


def test_several_currencies_are_recognised():
    for amount in ("$8,000,000", "€400,000", "£12M"):
        result = compare(
            {"incentiveEstimates": [{"value": amount}]}, _payload()
        )
        assert any(
            f.kind == FIGURE_WITHDRAWN and f.legacy_value == amount
            for f in result.findings
        ), amount


def test_a_report_with_no_figures_produces_no_figure_findings():
    result = compare({"incentiveEstimates": []}, _payload())
    assert not [
        f for f in result.findings if f.kind in (FIGURE_WITHDRAWN, FIGURE_ADDED)
    ]


# ── The rendered output ──────────────────────────────────────────────────────


def test_the_output_says_it_does_not_decide_the_cutover():
    """An automated verdict would be a machine approving its own replacement."""
    text = render(compare({}, _payload()))
    assert "does not decide which version is right" in text
    assert "a reviewer signs the cutover off" in text


def test_expected_and_unexplained_findings_are_reported_separately():
    legacy = {
        "incentiveEstimates": [{"v": "£5.74M"}],
        "comparables": [{"title": "A Film"}],
    }
    text = render(compare(legacy, _payload()))
    assert "Expected — the rebuild working as designed:" in text
    assert "Needing review — not explained by any rule:" in text


def test_a_clean_comparison_says_every_difference_has_a_rule():
    text = render(compare({}, _payload()))
    assert "Nothing unexplained" in text


# ── Against the real sample ──────────────────────────────────────────────────


def test_the_worked_sample_produces_no_unexplained_differences():
    """Every difference the fixture creates is one the rebuild intends."""
    from app.modules.reports.sample_orchestration import (
        as_payload,
        build_sample_orchestration,
    )

    legacy = {
        "incentiveEstimates": [
            {"territory": "New York", "confirmedIncentive": "$7,650,000"},
            {"territory": "United Kingdom", "confirmedIncentive": "£5,740,000"},
        ],
        "fundingOpportunities": [
            {"name": "Film London Production Finance Market"},
            {"name": "A national production fund"},
        ],
        "executiveSummary": {"headlineNetBudget": "approximately $24,262,500"},
    }
    result = compare(legacy, as_payload(build_sample_orchestration()))

    assert not result.has_unexpected, [f.detail for f in result.needs_review]
    kinds = {f.kind for f in result.expected}
    assert FIGURE_WITHDRAWN in kinds
    assert ROUTING_MOVED in kinds
    assert COMMITTED_FINANCE_WITHDRAWN in kinds
