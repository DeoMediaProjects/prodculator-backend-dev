"""Section 12 comes from the frozen Sales/Distribution engine when it can.

The legacy matcher scores a hand-maintained `distributors` table. It cannot
say how a producer would reach anyone on it, and the freeze is explicit that
this section scores strategic fit and never probability of acquisition — the
regression report's language guardrail. The v2 engine carries a match state,
a canonical access route and the conditions still outstanding.

The fallback is deliberate and temporary: the commercial catalogue was empty
until its staging landed, and an empty section in a paid report is worse than
a labelled older one. These tests pin both halves, so removing the fallback
later is a visible change rather than a silent one.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.reports.builder import ReportBuilder, _sourced_scalar, _sourced_values
from app.modules.reports.helpers import package_display_limit


class _Fit:
    def __init__(self, score: int, known: int) -> None:
        self.score = score
        self.components_known = known


def _company(name: str, *, route: str = "public_contact", rules_complete: bool = True):
    return SimpleNamespace(
        name=name,
        access_route=route,
        rules_complete=rules_complete,
        role=None,
        rights_territories=None,
    )


def _match(name: str, *, status: str = "STRATEGIC_MATCH", score: int = 62):
    return SimpleNamespace(
        profile=_company(name),
        status=status,
        fit=_Fit(score, 6),
        score=score,
        reasons=("Genre fit.", "Feature scale."),
        conditions_to_confirm=("Confirm current acquisition remit.",),
        gate_results=(),
    )


def _builder(strategies) -> ReportBuilder:
    instance = ReportBuilder.__new__(ReportBuilder)
    instance.warnings = []
    instance.request_metadata = {"_package": "producer"}
    instance._commercial_cache = strategies
    return instance


class TestTheV2EngineWins:
    def test_section_12_uses_it_when_it_returns_companies(self):
        strategies = SimpleNamespace(
            sales=SimpleNamespace(recommendations=(_match("Fifth Season"),)),
            comparables=None,
            catalogue=None,
        )
        entries = _builder(strategies)._v2_distributor_recommendations()
        assert [e["name"] for e in entries] == ["Fifth Season"]
        assert entries[0]["commercialEngine"] == "v2"

    def test_it_states_an_access_route_and_what_is_unresolved(self):
        # The two things the legacy table could not say. A strategic fit with
        # no route is a name a producer cannot act on.
        strategies = SimpleNamespace(
            sales=SimpleNamespace(recommendations=(_match("Mister Smith"),)),
            comparables=None,
            catalogue=None,
        )
        entry = _builder(strategies)._v2_distributor_recommendations()[0]
        assert entry["accessRoute"] == "public_contact"
        assert entry["matchState"] == "STRATEGIC_MATCH"
        assert entry["conditionsToConfirm"] == ["Confirm current acquisition remit."]

    def test_it_reports_how_much_of_the_score_is_known(self):
        # A 62 built from six known components and a 62 built from two are
        # different claims, and a report showing only the number cannot tell
        # them apart.
        strategies = SimpleNamespace(
            sales=SimpleNamespace(recommendations=(_match("Focus Features"),)),
            comparables=None,
            catalogue=None,
        )
        entry = _builder(strategies)._v2_distributor_recommendations()[0]
        assert entry["strategicFitScore"] == 62
        assert entry["componentsKnown"] == 6

    def test_it_invents_no_submission_prose(self):
        # The engine states a canonical route. Turning that into a sentence
        # would put words in a company's mouth.
        strategies = SimpleNamespace(
            sales=SimpleNamespace(recommendations=(_match("Neon"),)),
            comparables=None,
            catalogue=None,
        )
        assert _builder(strategies)._v2_distributor_recommendations()[0][
            "submissionProcess"
        ] is None


class TestTheFallback:
    def test_no_companies_means_the_legacy_section_still_runs(self):
        strategies = SimpleNamespace(
            sales=SimpleNamespace(recommendations=()), comparables=None, catalogue=None
        )
        assert _builder(strategies)._v2_distributor_recommendations() == []

    def test_an_unavailable_catalogue_does_not_fail_the_report(self):
        # False is the cached failure. It must read as "nothing to render",
        # not raise into a report a producer paid for.
        assert _builder(False)._v2_distributor_recommendations() == []

    def test_no_projectfacts_snapshot_means_no_v2_section(self):
        # Locked decision I.1: every engine consumes the same snapshot. Without
        # one there is nothing legitimate to compute against.
        instance = ReportBuilder.__new__(ReportBuilder)
        instance.warnings = []
        instance.request_metadata = {}
        assert instance._commercial_strategies() is None


class TestSourcedValues:
    def test_a_stale_source_reads_as_absent(self):
        # SourcedValue.known owns the staleness rule. Rendering a value the
        # engine would have refused to score is how a report ends up more
        # confident than the engine behind it.
        stale = SimpleNamespace(known=lambda _today: False, value=["GB", "US"])
        assert _sourced_values(stale) == []
        assert _sourced_scalar(stale) is None

    def test_a_known_list_comes_back_sorted(self):
        fresh = SimpleNamespace(known=lambda _today: True, value=["US", "GB"])
        assert _sourced_values(fresh) == ["GB", "US"]

    def test_none_is_not_an_error(self):
        assert _sourced_values(None) == []
        assert _sourced_scalar(None) is None


class TestDisplayEntitlement:
    @pytest.mark.parametrize(
        "package,expected",
        [
            ("producer", 10), ("studio", 10), ("PRODUCER", 10),
            ("single", 5), ("professional", 5), ("free", 5),
            (None, 5), ("", 5), ("nonsense", 5),
        ],
    )
    def test_the_frozen_split(self, package, expected):
        # Grants lock 9, Festival lock 17, and the same split in the
        # Markets/Labs/WIP and Sales/Distribution freezes.
        assert package_display_limit(package) == expected

    def test_an_unknown_package_gets_the_shallower_slice(self):
        # A report that renders five is recoverable. One that renders ten to
        # someone who bought five has already given the product away.
        assert package_display_limit("something-new") == 5
