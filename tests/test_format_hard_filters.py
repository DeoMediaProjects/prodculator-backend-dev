"""FIX-03 Stage 1, FIX-04, FIX-07: format as a hard filter, everywhere.

Festivals and grants gated on format from the start. Distributors and comparable
productions did not, and the EJE short report shows what that produced:

  distributors  A24, Neon, Dark Sky Films — real, all feature buyers, none of
                which takes a short, and no short-film outlet in the dataset to
                match instead
  comparables   eight titles, every one a feature, selected on territory and
                genre because nothing asked what the production was

Both now gate. Both treat "not recorded" as unknown rather than unsuitable, so
the existing records keep working — a null must never silently exclude.
"""
from __future__ import annotations

import pytest

from app.core.formats import canonical_format
from app.modules.reports.builder import ReportBuilder
from datetime import date

from app.modules.reports.matching import match_distributors, match_grants

TODAY = date(2026, 8, 13)


# ── FIX-04: distributors ─────────────────────────────────────────────────────

def dist(name, genres, formats):
    return {"name": name, "specialty_genres": genres, "format_focus": formats}


A24 = dist("A24", ["horror", "thriller"], ["feature"])
ALTER = dist("ALTER", ["horror", "thriller"], ["short"])
UNKNOWN = dist("Neon", ["horror"], None)
ALL_FORMATS = dist("Netflix", ["horror"], ["all"])


def matched(distributors, fmt):
    return [
        m.distributor["name"]
        for m in match_distributors(
            distributors, genres=["horror"], representation_gender=None,
            representation_minority=[], matched_festival_names=[],
            production_format=fmt,
        )
    ]


class TestDistributorFormatGate:
    def test_a_horror_short_reaches_the_short_film_outlet(self):
        """The EJE regression, stated directly."""
        assert "ALTER" in matched([A24, ALTER], "Short")

    def test_a_feature_buyer_is_excluded_from_a_short_report(self):
        assert "A24" not in matched([A24, ALTER], "Short")

    def test_a_short_outlet_is_excluded_from_a_feature_report(self):
        assert matched([A24, ALTER], "Feature Film") == ["A24"]

    def test_an_undeclared_format_focus_still_competes(self):
        """Null is unknown, not unsuitable. 57 existing records depend on this."""
        assert "Neon" in matched([UNKNOWN], "Short")
        assert "Neon" in matched([UNKNOWN], "Feature Film")

    def test_all_formats_always_competes(self):
        assert "Netflix" in matched([ALL_FORMATS], "Short")

    def test_no_declared_format_gates_nothing(self):
        assert set(matched([A24, ALTER, UNKNOWN], None)) == {"A24", "ALTER", "Neon"}

    def test_it_excludes_rather_than_deprioritises(self):
        """Consistent with the festival and grant gates: a buyer who does not
        handle this format is not a weaker option, it is not an option."""
        assert "A24" not in matched([A24], "Short")


# ── FIX-07: grants ───────────────────────────────────────────────────────────

class TestGrantFormatGate:
    def _run(self, eligible, fmt):
        # verified_at set because G3 excludes unverified funds outright; this
        # suite is about G1, and an unrelated gate silently emptying the result
        # would make every assertion here pass for the wrong reason.
        grants = [{
            "fund_name": "Test Fund", "territory": "United Kingdom",
            "eligible_formats": eligible, "continent": "Europe",
            "verified_at": TODAY.isoformat(),
            # A genre signal, so every fixture here scores above zero on
            # something other than format. Only score > 0 is returned, so without
            # it a fund that PASSED the format gate and simply scored nothing
            # would be indistinguishable from one the gate excluded — and the
            # "all"/empty cases below would pass for entirely the wrong reason.
            "genre_tags": ["horror"],
        }]
        matches, _ = match_grants(
            grants,
            {"format": fmt, "genres": ["horror"], "budget_usd": 50_000},
            today=TODAY,
        )
        return [m["grant"]["fund_name"] for m in matches]

    def test_a_matching_format_passes(self):
        assert self._run(["short"], "Short") == ["Test Fund"]

    def test_a_non_matching_format_is_excluded(self):
        assert self._run(["feature"], "Short") == []

    def test_a_display_label_in_the_data_still_matches(self):
        """"Short Film" in the fund record and "Short" from intake are the same
        format. A hard gate failing closed on a spelling difference loses a real
        match and looks exactly like having none."""
        assert self._run(["Short Film"], "Short") == ["Test Fund"]

    def test_all_passes_every_format(self):
        assert self._run(["all"], "Short") == ["Test Fund"]

    def test_an_empty_list_does_not_exclude(self):
        """A fund that states no formats has not stated a restriction. Reading
        silence as exclusion would drop every fund that never filled the field."""
        assert self._run([], "Short") == ["Test Fund"]

    def test_the_gate_excludes_on_format_and_nothing_else(self):
        """All four fixtures are identical but for eligible_formats, so only the
        format gate can explain the difference between them."""
        assert self._run(["short"], "Short") == ["Test Fund"]
        assert self._run(["all"], "Short") == ["Test Fund"]
        assert self._run([], "Short") == ["Test Fund"]
        assert self._run(["feature"], "Short") == []


# ── FIX-03 Stage 1: comparables ──────────────────────────────────────────────

def comp(title, fmt=None, territory="United Kingdom", genre="horror"):
    row = {"title": title, "primary_territory": territory, "genre": genre}
    if fmt is not None:
        row["format"] = fmt
    return row


def _builder(comparables, fmt="Short"):
    b = ReportBuilder.__new__(ReportBuilder)
    b.datasets = {"comparables": comparables}
    b.request_metadata = {"genre": ["horror"]}
    b._territory_names = ["United Kingdom"]
    b._production_format = fmt
    b._budget_gbp = 45_730
    return b


class TestComparableFormatGate:
    def test_an_out_of_format_comparable_is_discarded(self):
        """The EJE regression: eight features offered to a short."""
        titles = [c["title"] for c in _builder([comp("Aftersun", "feature")])._build_comparables()]
        assert titles == []

    def test_a_matching_comparable_is_kept(self):
        titles = [c["title"] for c in _builder([comp("A Short", "short")])._build_comparables()]
        assert titles == ["A Short"]

    def test_an_unrecorded_format_is_kept_and_marked(self):
        """Stage 1 is a stopgap. Discarding every null would empty the section on
        today's data, which tells the producer less than a labelled list does."""
        out = _builder([comp("Unknown Title")])._build_comparables()
        assert [c["title"] for c in out] == ["Unknown Title"]
        assert out[0]["formatVerified"] is False

    def test_a_confirmed_match_outranks_an_unrecorded_one(self):
        out = _builder([comp("Unknown Title"), comp("A Short", "short")])._build_comparables()
        assert [c["title"] for c in out] == ["A Short", "Unknown Title"]

    def test_format_outweighs_territory(self):
        """A feature in the same territory is a weaker comparable for a short than
        a short from elsewhere. Territory affinity must not resurrect it."""
        out = _builder([
            comp("UK Feature", "feature", territory="United Kingdom"),
            comp("Foreign Short", "short", territory="Japan"),
        ])._build_comparables()
        assert [c["title"] for c in out] == ["Foreign Short"]

    def test_a_display_label_in_the_data_is_canonicalised(self):
        titles = [c["title"] for c in _builder([comp("A Short", "Short Film")])._build_comparables()]
        assert titles == ["A Short"]

    def test_no_production_format_gates_nothing(self):
        out = _builder([comp("Aftersun", "feature")], fmt=None)._build_comparables()
        assert [c["title"] for c in out] == ["Aftersun"]

    @pytest.mark.parametrize("fmt", ["Short", "Feature Film", "Documentary", "TV Series"])
    def test_zero_out_of_format_comparables_survive(self, fmt):
        """The acceptance criterion, run across the matrix."""
        rows = [
            comp("F", "feature"), comp("S", "short"),
            comp("D", "documentary"), comp("T", "tv_series"),
        ]
        out = _builder(rows, fmt=fmt)._build_comparables()
        wanted = canonical_format(fmt)
        assert all(canonical_format(c["format"]) == wanted for c in out)
