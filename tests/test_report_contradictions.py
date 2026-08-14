"""Issues 1, 4, 5 and 7: sections that were each correct and together contradictory.

Every defect covered here passed the per-section assertions that existed. That is the
point of the module under test: a contradiction between a computed field and a sentence
can only be seen once both are final, so it is checked after both exist rather than
prevented by prompt wording.

Covered:

  Issue 1  "Special permits required — long lead times in every candidate territory"
           emitted from one script-level boolean, asserting a lead-time fact with no
           backing field and a territory-wide fact from a method that reads no
           territory data.
  Issue 4  New Mexico badged BANKABLE above a sentence saying the rebate "should not
           be treated as investor-bankable".
  Issue 5  UK AVEC (Enhanced/IFTC): eligibility unverified, confirmed incentive £0,
           and Incentive Value 88 carrying the territory to first place.
  Issue 7  The cross-section layer that catches all of the above.
"""
from __future__ import annotations

import json

import pytest

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.cross_section import PREFIX, validate_cross_section
from app.modules.scripts.schemas import (
    BudgetEstimate,
    Challenges,
    Equipment,
    Location,
    Metadata,
    ProductionScale,
    ScriptAnalysisResult,
)


# ── Issue 1: production challenges ───────────────────────────────────────────

def _script_analysis(**challenge_kwargs) -> ScriptAnalysisResult:
    defaults = dict(
        weatherDependent=False, historicalPeriod=False, specialPermits=False,
        stunts=False, animalWrangling=False, waterWork=False, nightShooting=False,
        notes=[],
    )
    defaults.update(challenge_kwargs)
    return ScriptAnalysisResult(
        locations=[
            Location(
                name="Hospital", country="South Africa",
                territory="South Africa", frequency=8, isMainLocation=True,
            )
        ],
        budgetEstimate=BudgetEstimate(
            range="micro", minUSD=50_000, maxUSD=250_000,
            confidence=0.7, indicators=["single location"],
        ),
        productionScale=ProductionScale(
            crewSize="small", principalCast="medium", supportingCast="small",
            backgroundExtras="minimal", estimatedShootingDays=6,
        ),
        equipment=Equipment(
            cameraEquipment="arri", specialEquipment=["wire rig"],
            vfxRequirements="moderate",
        ),
        metadata=Metadata(
            genres=["Horror", "Thriller"], format="short",
            tone="Dark", targetAudience="Adults",
        ),
        challenges=Challenges(**defaults),
    )


def _script_intelligence(**challenge_kwargs) -> dict:
    builder = ReportBuilder(
        {"incentives": [], "_territory_financials": {}, "_production_format": "Short"},
        {},
        script_analysis=_script_analysis(**challenge_kwargs),
    )
    return builder._build_script_intelligence()


class TestProductionChallengeGrounding:
    def test_the_leaked_lead_time_and_territory_claim_is_gone(self):
        """The exact string from the report, asserted absent.

        It claimed two things one boolean cannot support: "long lead times" has no
        backing field anywhere in the schema, and "in every candidate territory" is a
        claim about the ranked territories from a method that reads none — it would have
        printed identically for a one-territory report and a ten-territory one.
        """
        challenges = _script_intelligence(specialPermits=True)["productionChallenges"]
        joined = " ".join(challenges)
        assert "long lead times" not in joined.lower()
        assert "every candidate territory" not in joined.lower()
        assert "candidate territor" not in joined.lower()

    def test_a_permit_flag_still_produces_a_challenge_scoped_to_the_evidence(self):
        """Suppressing the claim entirely would lose a real signal. It is kept, scoped."""
        challenges = _script_intelligence(specialPermits=True)["productionChallenges"]
        assert any("permit" in c.lower() for c in challenges)
        # And it defers the territory question rather than answering it.
        permit_line = next(c for c in challenges if "permit" in c.lower())
        assert "film office" in permit_line.lower()

    def test_no_permit_flag_produces_no_permit_challenge(self):
        challenges = _script_intelligence(specialPermits=False)["productionChallenges"]
        assert not any("permit" in c.lower() for c in challenges)

    def test_every_challenge_traces_to_a_set_input(self):
        """With no flags and no counts set, the section must be empty rather than
        falling back to generic text."""
        assert _script_intelligence()["productionChallenges"] == []

    def test_counted_challenges_quote_their_count(self):
        challenges = _script_intelligence(
            night_scenes=9, stunt_sequences=2, crowd_scenes=1,
        )["productionChallenges"]
        joined = " ".join(challenges)
        assert "9 night scenes" in joined
        assert "2 stunt sequences" in joined
        assert "1 large-crowd scenes" in joined

    def test_multilingual_dialogue_surfaces_as_a_challenge(self):
        """The EJE report identifies Zulu-language casting as a real driver, and the
        language list is a structured input that supports saying so."""
        challenges = _script_intelligence(
            languages=["English", "Zulu"],
        )["productionChallenges"]
        joined = " ".join(challenges)
        assert "Zulu" in joined
        assert "2 languages" in joined

    def test_a_single_language_is_not_a_challenge(self):
        challenges = _script_intelligence(languages=["English"])["productionChallenges"]
        assert not any("language" in c.lower() for c in challenges)

    def test_vfx_scene_count_surfaces_as_a_challenge(self):
        challenges = _script_intelligence(
            vfxHeavySceneCount=4,
        )["productionChallenges"]
        assert any("4 VFX-heavy scenes" in c for c in challenges)


# ── Issue 4: bankability ─────────────────────────────────────────────────────

class TestBankabilityConsistency:
    def _findings(self, label: str, risks: list[str]) -> list[str]:
        report = {
            "locationRankings": [
                {"name": "New Mexico", "bankabilityLabel": label, "keyRisks": risks}
            ]
        }
        return validate_cross_section(report)

    def test_bankable_plus_not_investor_bankable_is_caught(self):
        """The New Mexico contradiction, exactly as it appeared."""
        findings = self._findings("BANKABLE", [
            "Payment timeline 4-9 months — this incentive should not be treated as "
            "investor-bankable. Budget cash flow independently.",
        ])
        assert any("BANKABLE but narrative says" in f for f in findings)

    def test_bankable_with_a_cash_flow_caveat_is_allowed(self):
        """The nuance is legitimate and must survive: formally bankable, still needs
        carrying. It just must not be phrased as a re-classification."""
        findings = self._findings("BANKABLE", [
            "Payment timeline 4-9 months — the programme is classified Bankable, but "
            "the production still has to carry the cost until the rebate lands. Plan "
            "interim cash flow for the full window.",
        ])
        assert findings == []

    def test_not_bankable_plus_a_bankable_claim_is_caught(self):
        findings = self._findings("NOT BANKABLE", [
            "The credit is bankable against a lender facility.",
        ])
        assert any("NOT BANKABLE but narrative" in f for f in findings)

    def test_verify_first_is_not_second_guessed(self):
        assert self._findings("VERIFY FIRST", ["Confirm payment record."]) == []


class TestBankabilityMessageFollowsTheLabel:
    """The builder's own long-payment message must respect the canonical label."""

    def test_a_bankable_territory_gets_the_cash_flow_wording(self):
        loc = {"bankabilityLabel": "BANKABLE", "keyRisks": []}
        ReportBuilder._inject_reliability_warnings(
            ReportBuilder.__new__(ReportBuilder), loc,
            [{"payment_timeline_days_max": 270, "payment_timeline_days_min": 120}],
        )
        joined = " ".join(loc["keyRisks"])
        assert "classified Bankable" in joined
        assert "should not be treated as investor-bankable" not in joined

    def test_a_non_bankable_territory_keeps_the_blunt_wording(self):
        loc = {"bankabilityLabel": "NOT BANKABLE", "keyRisks": []}
        ReportBuilder._inject_reliability_warnings(
            ReportBuilder.__new__(ReportBuilder), loc,
            [{"payment_timeline_days_max": 400, "payment_timeline_days_min": 200}],
        )
        assert any(
            "should not be treated as investor-bankable" in r
            for r in loc["keyRisks"]
        )


# ── Issue 5: unverified eligibility must not lift the ranking ────────────────

class TestUnverifiedEligibilityScoring:
    def test_an_unverified_programme_is_not_scored(self):
        """The rule, stated once: unresolved eligibility means the dimension is
        neutral, not rewarded."""
        from app.modules.reports.builder import SCORE_WEIGHTS

        weights = {"incentiveStrength": 1.0}
        # Not scored is neutral in the weighted total.
        assert ReportBuilder._weighted_score({"incentiveStrength": None}, weights, 0) == 50
        # A researched exclusion is zero, which is a different statement.
        assert ReportBuilder._weighted_score({"incentiveStrength": 0}, weights, 0) == 0
        # A confirmed programme keeps its computed strength.
        assert ReportBuilder._weighted_score({"incentiveStrength": 88}, weights, 0) == 88

    def test_an_unverified_territory_cannot_outrank_a_confirmed_one_on_rate(self):
        """The failure this rule prevents: a big unconfirmed number beating a smaller
        confirmed one."""
        weights = {"incentiveStrength": 1.0}
        unverified_high_rate = ReportBuilder._weighted_score(
            {"incentiveStrength": None}, weights, 0
        )
        confirmed_lower_rate = ReportBuilder._weighted_score(
            {"incentiveStrength": 60}, weights, 0
        )
        assert confirmed_lower_rate > unverified_high_rate


class TestEligibilityVsNarrative:
    def test_an_unverified_programme_described_as_eligible_is_caught(self):
        report = {
            "incentiveEstimates": [{
                "territory": "United Kingdom",
                "program": "AVEC (Enhanced/IFTC)",
                "formatEligibility": {"verdict": "unverified"},
                "eligibilityNote": "The production is eligible for this programme.",
            }]
        }
        findings = validate_cross_section(report)
        assert any("asserts eligibility" in f for f in findings)

    def test_an_unconfirmed_rebate_described_as_secured_is_caught(self):
        report = {
            "incentiveEstimates": [{
                "territory": "United Kingdom",
                "program": "AVEC (Enhanced/IFTC)",
                "formatEligibility": {"verdict": "unverified"},
                "rebateIsConfirmed": False,
                "eligibilityNote": "A rebate of £14,593 is secured against the budget.",
            }]
        }
        findings = validate_cross_section(report)
        assert any("describes it as secured" in f for f in findings)

    def test_format_ineligible_but_rebate_marked_confirmed_is_caught(self):
        report = {
            "incentiveEstimates": [{
                "territory": "California",
                "program": "California Film & Television Tax Credit (Program 4.0)",
                "formatEligibility": {"verdict": "ineligible"},
                "rebateIsConfirmed": True,
            }]
        }
        findings = validate_cross_section(report)
        assert any("marked confirmed" in f for f in findings)

    def test_a_properly_caveated_unverified_programme_passes(self):
        report = {
            "incentiveEstimates": [{
                "territory": "United Kingdom",
                "program": "AVEC (Enhanced/IFTC)",
                "formatEligibility": {"verdict": "unverified"},
                "rebateIsConfirmed": False,
                "eligibilityNote": (
                    "Whether this programme accepts short projects has not been "
                    "verified. The figure shown is illustrative only."
                ),
            }]
        }
        assert validate_cross_section(report) == []


# ── Issue 7: the cross-section layer itself ──────────────────────────────────

class TestShootWindowCrossSection:
    def test_an_august_shoot_placed_inside_a_march_to_june_window_is_caught(self):
        report = {
            "weatherLogistics": [{
                "territory": "United Kingdom",
                "bestMonths": ["March", "April", "May", "June"],
                "shootWindowVerdict": "outside_optimal_window",
                "seasonalConsiderations": (
                    "An August 2026 shoot falls within the UK's optimal production "
                    "window of March through June."
                ),
            }]
        }
        findings = validate_cross_section(report)
        assert any("United Kingdom" in f and "inside" in f for f in findings)

    def test_an_august_shoot_placed_inside_an_april_to_july_window_is_caught(self):
        report = {
            "weatherLogistics": [{
                "territory": "South Africa",
                "bestMonths": ["April", "May", "June", "July"],
                "shootWindowVerdict": "adjacent_to_optimal_window",
                "seasonalConsiderations": (
                    "An August 2026 shoot in South Africa falls within the optimal "
                    "April through July window."
                ),
            }]
        }
        findings = validate_cross_section(report)
        assert any("South Africa" in f for f in findings)

    def test_agreeing_weather_prose_passes(self):
        report = {
            "weatherLogistics": [{
                "territory": "United Kingdom",
                "shootWindowVerdict": "inside_optimal_window",
                "seasonalConsiderations": (
                    "An August shoot falls within the UK's optimal March to September "
                    "window."
                ),
            }]
        }
        assert validate_cross_section(report) == []

    def test_an_unknown_verdict_is_never_second_guessed(self):
        report = {
            "weatherLogistics": [{
                "territory": "California",
                "shootWindowVerdict": "unknown",
                "seasonalConsiderations": "Anything at all, inside or outside.",
            }]
        }
        assert validate_cross_section(report) == []


class TestStackingCrossSection:
    def test_one_pair_described_both_ways_is_caught(self):
        report = {
            "incentiveEstimates": [{
                "territory": "United Kingdom",
                "program": "UK VFX Expenditure Credit (Uplift)",
                "stackingRelationship": "stacks",
                "stacksWith": "AVEC (Enhanced/IFTC)",
                "stackingNote": (
                    "SUPPLEMENTARY: UK VFX Expenditure Credit (Uplift) stacks ON TOP "
                    "of AVEC (Enhanced/IFTC)."
                ),
                "requirements": [
                    "NARROW ELIGIBILITY -- Cannot be combined with the VFX uplift or "
                    "animation uplift.",
                ],
            }]
        }
        findings = validate_cross_section(report)
        assert any("stack" in f.lower() for f in findings)

    def test_a_consistent_exclusive_pair_passes(self):
        report = {
            "incentiveEstimates": [{
                "territory": "United Kingdom",
                "program": "UK VFX Expenditure Credit (Uplift)",
                "stackingRelationship": "mutually_exclusive",
                "stacksWith": "AVEC (Enhanced/IFTC)",
                "stackingNote": (
                    "MUTUAL EXCLUSIVITY: UK VFX Expenditure Credit (Uplift) CANNOT be "
                    "combined with AVEC (Enhanced/IFTC)."
                ),
                "requirements": [
                    "Cannot be combined with the VFX uplift or animation uplift.",
                ],
            }]
        }
        assert validate_cross_section(report) == []

    def test_two_different_pairs_in_one_territory_are_not_a_contradiction(self):
        """The VFX uplift stacks with standard AVEC and does not stack with the
        enhanced/IFTC rate. Both statements are true in one UK report, and a
        territory-wide check would have failed correct data."""
        report = {
            "incentiveEstimates": [
                {
                    "territory": "United Kingdom",
                    "program": "UK VFX Expenditure Credit (Uplift)",
                    "stackingRelationship": "stacks",
                    "stacksWith": "UK Audio-Visual Expenditure Credit (AVEC)",
                    "stackingNote": (
                        "SUPPLEMENTARY: UK VFX Expenditure Credit (Uplift) stacks ON "
                        "TOP of UK Audio-Visual Expenditure Credit (AVEC)."
                    ),
                },
                {
                    "territory": "United Kingdom",
                    "program": "AVEC (Enhanced/IFTC)",
                    "requirements": [
                        "Cannot be combined with the VFX uplift or animation uplift.",
                    ],
                },
            ]
        }
        assert validate_cross_section(report) == []


class TestConfirmedZeroCrossSection:
    def test_a_zero_confirmed_incentive_described_as_secured_is_caught(self):
        report = {
            "financialAnalysis": {
                "budgetScenarios": [{
                    "territory": "United Kingdom",
                    "netRebate": "£0",
                    "note": "The £14,593 rebate is secured and reduces the net cost.",
                }]
            }
        }
        findings = validate_cross_section(report)
        assert any("secured" in f for f in findings)

    def test_an_absent_figure_is_not_read_as_zero(self):
        """"N/A" carries no digits. Reading it as zero would report a contradiction
        about a number the report never claimed."""
        report = {
            "financialAnalysis": {
                "budgetScenarios": [{
                    "territory": "Canada",
                    "netRebate": "N/A",
                    "note": "Funding is secured through a co-production partner.",
                }]
            }
        }
        assert validate_cross_section(report) == []


class TestMustFilmInCrossSection:
    def test_an_ignored_must_film_in_territory_is_caught(self):
        report = {
            "executiveSummary": {"keyInsights": ["The United Kingdom ranks first."]},
            "alternativeStrategy": "Consider New Mexico as a fallback.",
        }
        findings = validate_cross_section(report, {"_must_film_in": "South Africa"})
        assert any("must-film-in" in f for f in findings)

    def test_an_acknowledged_must_film_in_territory_passes(self):
        report = {
            "executiveSummary": {
                "keyInsights": [
                    "You told us this production must film in South Africa.",
                ],
            },
            "alternativeStrategy": "Plan against South Africa.",
        }
        assert validate_cross_section(report, {"_must_film_in": "South Africa"}) == []

    def test_no_constraint_means_no_finding(self):
        assert validate_cross_section({}, {"_must_film_in": ""}) == []


class TestMetricAgreementCrossSection:
    def test_a_metric_with_two_values_across_sections_is_caught(self):
        report = {
            "incentiveEstimates": [{
                "territory": "New Mexico",
                "program": "New Mexico Film Tax Credit",
                "totalBudget": "$62,000",
            }],
            "financialAnalysis": {
                "budgetScenarios": [{
                    "territory": "New Mexico",
                    "totalBudget": "$58,280",
                }]
            },
        }
        findings = validate_cross_section(report)
        assert any("total budget differs" in f for f in findings)

    def test_agreeing_metrics_pass(self):
        report = {
            "incentiveEstimates": [{
                "territory": "New Mexico", "totalBudget": "$58,280",
            }],
            "financialAnalysis": {
                "budgetScenarios": [{
                    "territory": "New Mexico", "totalBudget": "$58,280",
                }]
            },
        }
        assert validate_cross_section(report) == []


class TestRobustness:
    def test_an_empty_report_produces_no_findings(self):
        assert validate_cross_section({}) == []

    def test_a_non_dict_report_does_not_raise(self):
        assert validate_cross_section(None) == []

    def test_malformed_sections_do_not_raise(self):
        report = {
            "locationRankings": ["not a dict", None, 42],
            "incentiveEstimates": "not a list",
            "weatherLogistics": [None],
        }
        assert isinstance(validate_cross_section(report), list)

    def test_findings_are_prefixed_for_greppability(self):
        report = {
            "locationRankings": [{
                "name": "New Mexico", "bankabilityLabel": "BANKABLE",
                "keyRisks": ["not investor-bankable"],
            }]
        }
        findings = validate_cross_section(report)
        assert findings
        assert all(f.startswith(PREFIX) for f in findings)
