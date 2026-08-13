"""The commitment the producer declared, said back to them.

``must_film_in`` was read into the request schema, prepended to the analysed
territories so it led the ranking, stored on ``datasets["_must_film_in"]`` — and
then read by nothing. No section of the report mentioned it. A producer who told
us the one non-negotiable fact about their production got back a document that
discussed every territory as an open option, which reads as though the constraint
was ignored whether or not it was.

Three answers are possible and they are not interchangeable: the commitment leads
the analysis; it was analysed but something else scored higher; or it could not be
modelled at all. Each needs saying in its own words.
"""
from __future__ import annotations

from app.modules.reports.builder import ReportBuilder


def builder(must_film_in, territories, substitutions=None, unanalysed=None):
    b = ReportBuilder.__new__(ReportBuilder)
    b.datasets = {"_must_film_in": must_film_in}
    b._territory_substitutions = substitutions or {}
    b._unanalysed_territories = unanalysed or []
    return b


def note(b, territories) -> str:
    summary: dict = {}
    ReportBuilder._inject_must_film_in(b, summary, territories)
    return summary.get("mustFilmInNote") or ""


class TestItIsStatedAtAll:
    def test_nothing_is_added_when_none_was_declared(self):
        summary: dict = {}
        ReportBuilder._inject_must_film_in(builder(None, []), summary, ["France"])
        assert summary == {}

    def test_the_commitment_is_named(self):
        b = builder("France", ["France"])
        summary: dict = {}
        ReportBuilder._inject_must_film_in(b, summary, ["France", "Germany"])
        assert summary["mustFilmIn"] == "France"
        assert "must film in France" in summary["mustFilmInNote"]

    def test_it_leads_the_key_flags(self):
        """A producer reads the flags before the body. A commitment that appears
        only in a field nobody renders is the state this replaces."""
        b = builder("France", ["France"])
        summary: dict = {"keyFlags": ["Extended shoot timeline: 30 weeks."]}
        ReportBuilder._inject_must_film_in(b, summary, ["France"])
        assert "must film in France" in summary["keyFlags"][0]

    def test_it_is_not_repeated_on_a_second_pass(self):
        b = builder("France", ["France"])
        summary: dict = {}
        ReportBuilder._inject_must_film_in(b, summary, ["France"])
        ReportBuilder._inject_must_film_in(b, summary, ["France"])
        assert len(summary["keyFlags"]) == 1


class TestTheThreeAnswers:
    def test_when_the_commitment_leads_the_ranking(self):
        b = builder("France", ["France"])
        text = note(b, ["France", "Germany"])
        assert "figures are built around" in text
        assert "not as alternatives" in text

    def test_when_something_else_scores_higher(self):
        """The honest reading: the ranking is what the commitment costs, not a
        recommendation to break it."""
        b = builder("France", ["France"])
        text = note(b, ["Germany", "France"])
        assert "Germany scores higher" in text
        assert "Plan against France" in text
        assert "not a recommendation to break it" in text

    def test_when_it_could_not_be_modelled_the_reason_is_given(self):
        b = builder(
            "Nigeria", [],
            unanalysed=[{"territory": "Nigeria", "reason": "No active incentive programme is on record."}],
        )
        text = note(b, ["France"])
        assert "could not be modelled" in text
        assert "No active incentive programme is on record." in text

    def test_an_unmatched_commitment_does_not_pretend_the_others_are_options(self):
        b = builder("Atlantis", [])
        text = note(b, ["France"])
        assert "do not assume you are free to move" in text


class TestSubstitutionIsDisclosed:
    """A commitment to the United States is modelled as one of its states. The
    producer reads figures headed "New Mexico" under a commitment they made to
    the country, and has no way to tell whether that is the incentive's real
    level or a mistake."""

    def test_the_substitution_is_named_in_the_note(self):
        b = builder("United States", [], substitutions={"United States": "New Mexico"})
        text = note(b, ["New Mexico"])
        assert "modelled under New Mexico" in text
        assert "the level the incentive exists at" in text

    def test_the_substitution_is_exposed_as_a_field(self):
        b = builder("United States", [], substitutions={"United States": "New Mexico"})
        summary: dict = {}
        ReportBuilder._inject_must_film_in(b, summary, ["New Mexico"])
        assert summary["mustFilmInAnalysedAs"] == "New Mexico"

    def test_no_substitution_field_when_none_happened(self):
        b = builder("France", [])
        summary: dict = {}
        ReportBuilder._inject_must_film_in(b, summary, ["France"])
        assert "mustFilmInAnalysedAs" not in summary

    def test_a_substituted_commitment_that_lost_the_ranking_still_reads_correctly(self):
        b = builder("United States", [], substitutions={"United States": "New Mexico"})
        text = note(b, ["France", "New Mexico"])
        assert "France scores higher" in text
        assert "Plan against New Mexico" in text
