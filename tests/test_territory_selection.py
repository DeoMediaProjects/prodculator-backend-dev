"""Every territory a producer selects must reach the report, or say why it did not.

A producer chose three territories and the report analysed two, with nothing stating
which was missing or why. The cause: `_select_territories` expanded a country to its
sub-territories by reading `incentive_programs.parent_territory`, which is NULL on
every sub-territory row in the dataset. Selecting "United States" therefore found no
children and dropped the country outright, despite six US states carrying active
programmes.

The other half of the same complaint was `must_film_in`. It was read into the request
schema and used by nothing at all, so the one territory a production had committed to
never entered the analysis.
"""
from __future__ import annotations

import pytest

from app.modules.reports.builder import ReportBuilder
from app.modules.reports.helpers import index_incentives_by_territory


def row(territory, rate=25, **extra):
    return {
        "territory": territory,
        "program": f"{territory} programme",
        "rate_gross": rate,
        "status": "active",
        "is_supplementary": False,
        # NULL exactly as the real dataset has it. This is the condition that broke
        # the expansion, so the fixtures must not quietly supply what the DB lacks.
        "parent_territory": None,
        **extra,
    }


def builder(user_territories, incentive_rows, project_facts=None):
    b = ReportBuilder.__new__(ReportBuilder)
    b.datasets = {"_user_territories": user_territories}
    b._territory_incentives = index_incentives_by_territory(incentive_rows)
    b._territory_financials = {}
    b.is_preview = False
    b._unanalysed_territories = []
    b._project_facts = project_facts or {"budget_gbp": 5_000_000}
    return b


class TestParentExpansion:
    def test_a_country_whose_incentives_live_in_its_states_is_not_dropped(self):
        rows = [row("California", 35), row("Georgia (USA)", 30)]
        b = builder(["United States"], rows)
        assert ReportBuilder._select_territories(b) != []

    def test_the_reported_count_matches_what_was_selected(self):
        """The complaint in its plainest form: three chosen, three analysed."""
        rows = [row("Canada", 20), row("Mexico", 25), row("California", 35)]
        b = builder(["Canada", "Mexico", "United States"], rows)
        assert len(ReportBuilder._select_territories(b)) == 3

    def test_expansion_works_with_parent_territory_null(self):
        """The DB column is NULL on every sub-territory row, so the Territory enum
        has to be consulted. Reading the column alone is what caused the bug."""
        rows = [row("New Mexico", 25)]
        b = builder(["United States"], rows)
        assert ReportBuilder._select_territories(b) == ["New Mexico"]

    def test_the_db_column_is_still_honoured_when_present(self):
        rows = [row("Someplace", 25, parent_territory="United States")]
        b = builder(["United States"], rows)
        assert ReportBuilder._select_territories(b) == ["Someplace"]

    def test_a_directly_covered_country_is_not_expanded(self):
        rows = [row("France", 30), row("Île-de-France", 40)]
        b = builder(["France"], rows)
        assert ReportBuilder._select_territories(b) == ["France"]


class TestChildChoiceRespectsUsability:
    def test_the_child_the_production_can_use_wins_over_the_higher_rate(self):
        """Picking on rate alone resolved a country to a state whose minimum
        qualifying spend was many times the entire budget."""
        rows = [
            row("California", 35, qualifying_spend_min=1_000_000,
                qualifying_spend_currency="USD"),
            row("New York", 25),
        ]
        b = builder(["United States"], rows, {"budget_gbp": 45_840})
        assert ReportBuilder._select_territories(b) == ["New York"]

    def test_rate_decides_again_once_both_are_usable(self):
        rows = [
            row("California", 35, qualifying_spend_min=1_000_000,
                qualifying_spend_currency="USD"),
            row("New York", 25),
        ]
        b = builder(["United States"], rows, {"budget_gbp": 20_000_000})
        assert ReportBuilder._select_territories(b) == ["California"]


class TestNothingVanishesSilently:
    def test_a_territory_with_no_programme_is_reported_rather_than_dropped(self):
        b = builder(["Nigeria", "France"], [row("France", 30)])
        analysed = ReportBuilder._select_territories(b)

        assert analysed == ["France"]
        assert [d["territory"] for d in b._unanalysed_territories] == ["Nigeria"]
        assert "no active incentive programme" in b._unanalysed_territories[0]["reason"].lower()

    def test_a_supplementary_only_territory_says_so_specifically(self):
        """A stacking credit is a real fact about the territory, just not a
        standalone rebate. The reason has to distinguish the two."""
        rows = [row("Scotland", 30, is_supplementary=True), row("France", 30)]
        b = builder(["Scotland", "France"], rows)
        ReportBuilder._select_territories(b)

        reasons = {d["territory"]: d["reason"] for d in b._unanalysed_territories}
        assert "Scotland" in reasons
        assert "supplementary" in reasons["Scotland"].lower()

    def test_every_selection_is_either_analysed_or_explained(self):
        """The invariant behind the whole complaint. Nothing may fall between."""
        selected = ["Canada", "Mexico", "United States", "Nigeria", "Brazil"]
        rows = [row("Canada", 20), row("Mexico", 25), row("California", 35)]
        b = builder(selected, rows)
        analysed = ReportBuilder._select_territories(b)

        explained = {d["territory"] for d in b._unanalysed_territories}
        for choice in selected:
            accounted = (
                choice in analysed
                or choice in explained
                # A country is accounted for by the child it expanded to.
                or any(a not in selected for a in analysed)
            )
            assert accounted, f"{choice} vanished without explanation"
        assert len(analysed) + len(explained) == len(selected)

    def test_nothing_is_reported_when_everything_resolves(self):
        rows = [row("France", 30), row("Germany", 25)]
        b = builder(["France", "Germany"], rows)
        ReportBuilder._select_territories(b)
        assert b._unanalysed_territories == []


class TestMustFilmIn:
    """It was accepted by the schema and read by nothing."""

    def _resolve(self, metadata):
        from app.modules.reports.service import ReportService

        return ReportService._resolve_territories_hint(None, metadata)

    def test_it_reaches_the_dataset_hint(self):
        hint = self._resolve({
            "territories_considering": ["France"],
            "must_film_in": "Germany",
        })
        assert "Germany" in hint

    def test_it_is_canonicalised(self):
        hint = self._resolve({"territories_considering": [], "must_film_in": "UK"})
        assert "United Kingdom" in hint

    def test_it_is_not_duplicated_when_already_selected(self):
        hint = self._resolve({
            "territories_considering": ["France"],
            "must_film_in": "France",
        })
        assert hint.count("France") == 1

    @pytest.mark.parametrize("sentinel", ["", "Open", "open to all", "N/A", "none"])
    def test_a_non_answer_adds_nothing(self, sentinel):
        hint = self._resolve({
            "territories_considering": ["France"],
            "must_film_in": sentinel,
        })
        assert hint == ["France"]

    def test_a_domestic_strategy_is_unaffected(self):
        """Domestic restricts to the home country by design; a must-film-in cannot
        widen it back out."""
        hint = self._resolve({
            "location_strategy": "domestic",
            "country": "United Kingdom",
            "territories_considering": ["France"],
            "must_film_in": "Germany",
        })
        assert hint == ["United Kingdom"]
