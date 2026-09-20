"""Whether a researched value is in the shape an engine can read.

Separate from the ledger's own validation, which checks provenance: that a
source exists, is official, and is not our own data. A claim can pass all of
that and still carry a date no parser will read — and the engine discovers that
months later as an absence rather than an error.
"""
from __future__ import annotations

from dataclasses import dataclass

from scripts.ingest_research_workbook import value_problems


@dataclass(frozen=True)
class _Claim:
    gate: str
    field: str
    value: str


def _ok(gate, field, value):
    return value_problems(_Claim(gate, field, value)) == []


# ── Incentive engines ────────────────────────────────────────────────────────


def test_a_frozen_engine_name_is_accepted():
    assert _ok("INCENTIVE_ENGINE_CLASSIFICATION", "qs_engine_type", "QAPE")


def test_an_invented_engine_name_is_refused():
    assert not _ok("INCENTIVE_ENGINE_CLASSIFICATION", "qs_engine_type", "TOTAL_SPEND")


def test_the_legacy_vocabulary_is_not_a_valid_answer():
    """'local_spend' is the old column's word, not a statutory engine."""
    assert not _ok("INCENTIVE_ENGINE_CLASSIFICATION", "qs_engine_type", "local_spend")


# ── Dates ────────────────────────────────────────────────────────────────────


def test_an_iso_deadline_is_accepted():
    assert _ok("MARKET_CYCLE", "deadline", "2027-03-15")


def test_a_prose_date_is_refused_however_correct_it_is():
    """'15 October 2026' is a real deadline and not one any parser here reads."""
    assert not _ok("MARKET_CYCLE", "deadline", "15 October 2026")


def test_not_announced_is_accepted():
    assert _ok("MARKET_CYCLE", "deadline", "NOT_ANNOUNCED")


def test_a_festival_deadline_needs_its_section():
    """A festival-level date cannot say when THIS production must submit."""
    assert not _ok("FESTIVAL_SECTION", "section_deadlines", "2027-01-15")
    assert _ok("FESTIVAL_SECTION", "section_deadlines", "Main Competition | 2027-01-15")


def test_several_sections_each_need_a_date():
    value = "Features | 2027-01-15\nShorts | 2027-02-01"
    assert _ok("FESTIVAL_SECTION", "section_deadlines", value)
    assert not _ok(
        "FESTIVAL_SECTION", "section_deadlines", "Features | 2027-01-15\nShorts | soon"
    )


# ── Typed rules ──────────────────────────────────────────────────────────────


def test_a_rule_reads_field_operator_expected():
    assert _ok("MARKET_HARD_GATE", "hard_gates", "format one_of feature")


def test_manual_confirmation_is_accepted_in_either_order():
    """The pack's instruction was ambiguous, so both readings are correct.

    manual_confirmation has no expected value to compare against — only
    something for a human to check — so naming the operator first is as natural
    as naming it second.
    """
    assert _ok("MARKET_HARD_GATE", "hard_gates", "attached_director manual_confirmation")
    assert _ok("MARKET_HARD_GATE", "hard_gates", "manual_confirmation attached_director")


def test_a_line_naming_no_known_operator_is_refused():
    assert not _ok("MARKET_HARD_GATE", "hard_gates", "if_non_european must be true")


def test_a_premiere_requirement_uses_the_frozen_vocabulary():
    assert _ok("FESTIVAL_SECTION", "section_rules", "Main | premiere_requirement WORLD")
    assert not _ok(
        "FESTIVAL_SECTION", "section_rules", "Main | premiere_requirement one_of WORLD"
    )


# ── Split parents ────────────────────────────────────────────────────────────


def test_none_is_a_valid_split_parent_answer():
    """And the important one: a programme producers can no longer reach."""
    assert _ok("GRANTS_SPLIT_PARENT", "successor_titles", "NONE")


def test_semicolon_separated_successors_are_accepted():
    assert _ok("GRANTS_SPLIT_PARENT", "successor_titles", "Fund A; Fund B")


# ── Blank ────────────────────────────────────────────────────────────────────


def test_a_blank_value_is_never_usable():
    for gate in ("MARKET_CYCLE", "INCENTIVE_ENGINE_CLASSIFICATION", "FESTIVAL_SECTION"):
        assert not _ok(gate, "any", "   ")
