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
    # Selection now asks whether anything is known about a territory beyond its
    # rebate, so these have to exist on the fixture as they do on a real builder.
    # Empty here: these cases are all about territories with active programmes.
    b._territory_inactive_incentives = {}
    b._territory_profiles = {}
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


class TestParentIsNotExpandedWhenItsRegionsWereChosen:
    """Picking states put a state nobody asked for into the report.

    The picker nests regions under their country and keeps the country selected
    while they are visible, so choosing California and New Mexico submits
    "United States" too. Expanding it to a best child then added a third state —
    New York at ordinary budgets — and presented it as a considered territory.
    The United States has no federal film incentive, so the country is a
    container for its states, not a choice of its own.
    """

    def test_no_unchosen_state_is_added(self):
        rows = [
            row("California", 35), row("New Mexico", 25),
            row("New York", 30), row("Georgia (USA)", 30),
        ]
        b = builder(["United States", "California", "New Mexico"], rows)
        assert ReportBuilder._select_territories(b) == ["California", "New Mexico"]

    def test_the_parent_still_expands_when_no_region_was_chosen(self):
        rows = [row("California", 35), row("New York", 25)]
        b = builder(["United States"], rows)
        assert ReportBuilder._select_territories(b) == ["California"]

    def test_a_parent_with_its_own_programme_is_still_analysed(self):
        """Canada has a federal credit, so choosing Canada and Ontario is two
        real territories — this rule only drops countries that model nothing."""
        rows = [row("Canada", 25), row("Ontario", 35)]
        b = builder(["Canada", "Ontario"], rows)
        assert ReportBuilder._select_territories(b) == ["Canada", "Ontario"]

    def test_the_dropped_parent_is_not_reported_as_unanalysed(self):
        """It is represented by the states chosen inside it, so there is nothing
        to explain — a warning here would read as a failure."""
        rows = [row("California", 35), row("New York", 25)]
        b = builder(["United States", "California"], rows)
        ReportBuilder._select_territories(b)
        assert b._unanalysed_territories == []


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
        # Nigeria has no programme at all, which its own record insists is a
        # different fact from a suspended one: "not a slow or developing incentive
        # timeline — there is no incentive program to time". The reason used to be
        # a single sentence covering both cases, and it was written for the wrong
        # one. See test_territory_without_incentive.py for the suspended case.
        reason = b._unanalysed_territories[0]["reason"].lower()
        assert "no incentive programme is on record" in reason
        assert "structural fact rather than a delay" in reason
        assert "suspended" not in reason

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

    @pytest.mark.parametrize(
        "sentinel",
        ["", "Open", "open to all", "N/A", "none",
         # The intake's "Not decided yet" submits this. It was treated as a place
         # name: added to the analysed list, found to carry no programme, and
         # reported back as a territory that could not be modelled.
         "Undecided", "undecided", "Not decided yet", "TBD"],
    )
    def test_a_non_answer_adds_nothing(self, sentinel):
        hint = self._resolve({
            "territories_considering": ["France"],
            "must_film_in": sentinel,
        })
        assert hint == ["France"]

    def test_the_production_country_can_be_the_commitment(self):
        """Shooting where the company is based is the most ordinary commitment a
        production makes, and naming it must put it into the analysis without it
        also having to be ticked as a territory under consideration."""
        hint = self._resolve({
            "territories_considering": ["France"],
            "country": "United Kingdom",
            "must_film_in": "United Kingdom",
        })
        assert hint == ["France", "United Kingdom"]

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
