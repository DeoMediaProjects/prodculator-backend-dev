"""Turning verified research claims into the staged cycles the engines rank.

WHAT WAS MISSING
----------------
The verification ledger holds what a researcher read off an official page:
"Feature Competition | 2026-11-15", "format one_of feature". The engines read
``opportunity_cycles`` and ``opportunity_rules``. Nothing converted one into the
other, so a festival deadline could be researched, reviewed and verified by two
people and still leave Section 09 empty, because the cycle row it describes was
never created.

``stage_curated_cycles`` is not that path. It loads one hand-curated file of six
cycles and refuses anything whose SHA-256 differs, which is right for a file and
useless for a ledger that grows every time a claim is signed off.

WHAT THIS REFUSES TO PARSE
--------------------------
Everything it cannot type, and it says so by name rather than by absence.

The rule that shapes the rest: a rule the engine misreads is worse than a rule
it never sees. ``one_of`` iterates its expected value, so a bare string would be
compared character by character and FAIL silently — and a FAIL is
``INELIGIBLE_CONFIRMED``, which removes the opportunity from the producer's
report entirely. ``equals`` against a prose sentence fails the same way. So
``one_of`` always yields a list, the comparison operators require a number, and
anything that will not type is dropped and reported, never coerced.

A deadline line naming two dates is refused for the same reason. "Early bird
2026-09-01, final 2026-11-15" is two facts, and picking the first is a coin
flip on which one the producer plans around.

WHAT IT INFERS, AND THE ONE THING IT DOES NOT
---------------------------------------------
``evaluate_opportunity`` treats a cycle with neither ``cycle_open`` nor
``observed_open_on`` as not actionable, so a staged deadline alone produces
nothing. The research never asked when a call opened.

It did ask for "the current call's closing date… from the programme's own page",
and a researcher who answered that saw the official page present this as the
current call on the day they read it. That is what ``observed_open_on`` records
— observed open, exact opening date unknown — so it is set to the date the claim
was verified.

It is not set when the deadline had already passed on that date. A page showing
a closed call is evidence of a closed call, and recording it as observed-open
would be the inference this module exists to avoid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from app.modules.reports.project_dna import PROJECT_FACT_FIELDS
from app.modules.reports.verification_ledger import (
    GATE_FESTIVAL_SECTION,
    GATE_MARKET_CYCLE,
    GATE_MARKET_RULE,
    SourceClaim,
    readable_claims,
)

FESTIVAL = "FESTIVAL"
MARKET = "MARKET_LAB_WIP"

#: Operators the kernel evaluates. Anything else is a rule nobody implemented.
OPERATORS: frozenset[str] = frozenset(
    {
        "equals",
        "one_of",
        "at_least",
        "at_most",
        "greater_than",
        "less_than",
        "overlaps",
        "manual_confirmation",
    }
)
#: Operators whose expected value the kernel ITERATES. A string here is compared
#: character by character, which fails silently and confirms ineligibility.
_COLLECTION_OPERATORS = frozenset({"one_of", "overlaps"})
#: Operators the kernel floats. A non-numeric expected value yields UNKNOWN
#: forever, which looks like unfinished research rather than a bad rule.
_NUMERIC_OPERATORS = frozenset({"at_least", "at_most", "greater_than", "less_than"})

PREMIERE_FIELD = "premiere_requirement"
PREMIERE_VALUES: frozenset[str] = frozenset(
    {"WORLD", "INTERNATIONAL", "NATIONAL", "NONE"}
)

#: Values that say a deadline is not a date. Each is a real answer and none of
#: them is a cycle: the engine needs a boundary to rank against.
NOT_A_DATE: frozenset[str] = frozenset({"NOT_ANNOUNCED", "ROLLING", "UNKNOWN"})

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
#: Characters that mark prose rather than a value. A sentence compared with
#: ``equals`` never matches, and never matching is FAIL, not UNKNOWN.
_PROSE_MARKERS = ";.:"


@dataclass(frozen=True)
class ParsedRule:
    project_field: str
    operator: str
    expected: Any
    #: The researcher's own line, kept verbatim. It is what the report shows a
    #: producer as the condition to confirm, and a paraphrase here would be the
    #: engine explaining a rule in words nobody sourced.
    condition: str


@dataclass(frozen=True)
class ParsedCycle:
    kind: str
    record_id: str
    section_name: str
    cycle_deadline: date
    source_url: str
    verified_on: date
    observed_open_on: date | None
    premiere_requirement: str | None = None
    rules: tuple[ParsedRule, ...] = ()


@dataclass(frozen=True)
class ParseProblem:
    """One line that could not be typed, and why.

    Carried rather than logged. A dropped rule is a research finding — someone
    wrote something the engine cannot act on — and it needs to reach the person
    who can rewrite it.
    """

    gate: str
    subject_id: str
    detail: str
    reason: str


def _lines(value: Any) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _section_key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


def _expected_for(operator: str, tokens: Sequence[str]) -> tuple[Any, str | None]:
    """The typed expected value for one rule, or the reason it has none."""
    if operator == "manual_confirmation":
        if not tokens:
            return None, "manual_confirmation records nothing for a human to check"
        return None, None

    if not tokens:
        return None, f"{operator} names no expected value"

    if operator in _NUMERIC_OPERATORS:
        if len(tokens) != 1:
            return None, f"{operator} needs one number, not {' '.join(tokens)!r}"
        try:
            text = tokens[0]
            return (int(text) if re.fullmatch(r"-?\d+", text) else float(text)), None
        except ValueError:
            return None, f"{operator} needs a number, not {tokens[0]!r}"

    body = " ".join(tokens)
    if any(marker in body for marker in _PROSE_MARKERS):
        return None, f"{operator} was given prose; use manual_confirmation for it"

    if operator in _COLLECTION_OPERATORS:
        parts = [p.strip() for p in body.split(",")] if "," in body else list(tokens)
        values = [part for part in parts if part]
        if not values:
            return None, f"{operator} names no values"
        return values, None

    # equals. A long value is prose without the punctuation, and equals against
    # prose is a silent FAIL rather than an UNKNOWN.
    if len(body) > 40:
        return None, "equals was given a phrase too long to be a value"
    return body, None


def parse_rule_line(line: str) -> tuple[ParsedRule | None, str | None]:
    """One typed rule, or the reason the line is not one.

    Both token orders are accepted for ``manual_confirmation``. The research
    pack said "field operator expected", but manual_confirmation has no expected
    value, so "manual_confirmation attached_team" reads as naturally as the
    other way round — and rejecting one of them marks a researcher wrong for an
    ambiguity in the instruction they were given.
    """
    tokens = line.split()
    if len(tokens) < 2:
        return None, "names no field and operator"

    if tokens[0] in OPERATORS:
        operator, field_name, rest = tokens[0], tokens[1], tokens[2:]
    elif tokens[1] in OPERATORS:
        field_name, operator, rest = tokens[0], tokens[1], tokens[2:]
    else:
        return None, "names no known operator"

    if field_name not in PROJECT_FACT_FIELDS:
        return None, f"{field_name!r} is not a Project DNA field"

    expected, problem = _expected_for(operator, rest)
    if problem:
        return None, problem
    return ParsedRule(field_name, operator, expected, line), None


def _deadline_in(text: str) -> tuple[date | None, str | None]:
    found = _ISO.findall(text)
    if not found:
        return None, "carries no YYYY-MM-DD date"
    if len(set(found)) > 1:
        return None, f"names {len(set(found))} dates; which one closes is not decidable"
    try:
        return date.fromisoformat(found[0]), None
    except ValueError:
        return None, f"{found[0]!r} is not a real date"


def _observed_open(deadline: date, verified_on: date) -> date | None:
    """When the official page was seen presenting this as the current call.

    ``None`` when the deadline had already passed on the day it was read: the
    page was showing a closed call, and that is evidence of closure rather than
    of an open one.
    """
    return verified_on if deadline >= verified_on else None


def _festival_cycles(
    claims: Sequence[SourceClaim], problems: list[ParseProblem]
) -> list[ParsedCycle]:
    deadlines: dict[str, SourceClaim] = {}
    rules: dict[str, SourceClaim] = {}
    for claim in claims:
        if claim.field == "section_deadlines":
            deadlines[claim.subject_id] = claim
        elif claim.field == "section_rules":
            rules[claim.subject_id] = claim

    cycles: list[ParsedCycle] = []
    for subject_id, claim in sorted(deadlines.items()):
        value = str(claim.value or "").strip()
        if value.upper() in NOT_A_DATE:
            problems.append(
                ParseProblem(
                    GATE_FESTIVAL_SECTION, subject_id, value.upper(),
                    "no dated section, so there is no cycle to rank",
                )
            )
            continue

        by_section: dict[str, tuple[str, date]] = {}
        for line in _lines(value):
            if "|" not in line:
                problems.append(ParseProblem(
                    GATE_FESTIVAL_SECTION, subject_id, line,
                    "is not 'Section name | YYYY-MM-DD'",
                ))
                continue
            section, remainder = (part.strip() for part in line.split("|", 1))
            deadline, problem = _deadline_in(remainder)
            if not section:
                problem = "names no section"
            if problem or deadline is None:
                problems.append(ParseProblem(
                    GATE_FESTIVAL_SECTION, subject_id, line, problem or "has no deadline"
                ))
                continue
            key = _section_key(section)
            if key in by_section:
                problems.append(ParseProblem(
                    GATE_FESTIVAL_SECTION, subject_id, line,
                    f"repeats section {section!r}, which already has a deadline",
                ))
                continue
            by_section[key] = (section, deadline)

        premieres, section_rules = _festival_rules(
            rules.get(subject_id), set(by_section), problems
        )
        for key, (section, deadline) in by_section.items():
            cycles.append(
                ParsedCycle(
                    kind=FESTIVAL,
                    record_id=subject_id,
                    section_name=section,
                    cycle_deadline=deadline,
                    source_url=claim.source_url,
                    verified_on=claim.verified_on,
                    observed_open_on=_observed_open(deadline, claim.verified_on),
                    premiere_requirement=premieres.get(key),
                    rules=tuple(section_rules.get(key, ())),
                )
            )
    return cycles


def _festival_rules(
    claim: SourceClaim | None, known_sections: set[str], problems: list[ParseProblem]
) -> tuple[dict[str, str], dict[str, list[ParsedRule]]]:
    premieres: dict[str, str] = {}
    rules: dict[str, list[ParsedRule]] = {}
    if claim is None:
        return premieres, rules

    for line in _lines(claim.value):
        if "|" not in line:
            problems.append(ParseProblem(
                GATE_FESTIVAL_SECTION, claim.subject_id, line,
                "is not 'Section name | rule', so it cannot reach a section",
            ))
            continue
        section, body = (part.strip() for part in line.split("|", 1))
        key = _section_key(section)
        if key not in known_sections:
            # Rules for a section with no verified deadline. The cycle cannot be
            # staged without one, so the rule has nowhere to attach.
            #
            # The sections that DO have one are named in the reason, because the
            # common cause is not a missing deadline but two different names for
            # the same section written into two different gates — "Competition"
            # against "Feature Competition". Matching those by similarity is the
            # guess this module refuses to make; showing both lists is what lets
            # the person who wrote them settle it in a moment.
            available = ", ".join(sorted(known_sections)) or "none"
            problems.append(ParseProblem(
                GATE_FESTIVAL_SECTION, claim.subject_id, line,
                f"section {section!r} has no verified deadline to attach to; "
                f"dated sections are: {available}",
            ))
            continue

        tokens = body.split()
        if tokens and tokens[0] == PREMIERE_FIELD:
            requirement = tokens[1].upper() if len(tokens) > 1 else ""
            if requirement not in PREMIERE_VALUES:
                problems.append(ParseProblem(
                    GATE_FESTIVAL_SECTION, claim.subject_id, line,
                    "premiere must be WORLD, INTERNATIONAL, NATIONAL or NONE",
                ))
                continue
            premieres[key] = requirement
            continue

        rule, problem = parse_rule_line(body)
        if rule is None:
            problems.append(ParseProblem(
                GATE_FESTIVAL_SECTION, claim.subject_id, line, problem or "is not a rule"
            ))
            continue
        rules.setdefault(key, []).append(rule)
    return premieres, rules


def _market_cycles(
    claims: Sequence[SourceClaim], problems: list[ParseProblem]
) -> list[ParsedCycle]:
    deadlines = {c.subject_id: c for c in claims if c.gate == GATE_MARKET_CYCLE}
    gates = {c.subject_id: c for c in claims if c.gate == GATE_MARKET_RULE}

    cycles: list[ParsedCycle] = []
    for subject_id, claim in sorted(deadlines.items()):
        value = str(claim.value or "").strip()
        if value.upper() in NOT_A_DATE:
            problems.append(ParseProblem(
                GATE_MARKET_CYCLE, subject_id, value.upper(),
                "is a real answer and not a cycle the engine can rank",
            ))
            continue
        deadline, problem = _deadline_in(value)
        if deadline is None:
            problems.append(ParseProblem(
                GATE_MARKET_CYCLE, subject_id, value, problem or "has no deadline"
            ))
            continue

        rules: list[ParsedRule] = []
        gate_claim = gates.get(subject_id)
        if gate_claim is not None:
            for line in _lines(gate_claim.value):
                # A market track is one call, so a hard-gate line carries no
                # section prefix. A pipe is tolerated and its left side dropped.
                body = line.split("|", 1)[1].strip() if "|" in line else line
                rule, why = parse_rule_line(body)
                if rule is None:
                    problems.append(ParseProblem(
                        GATE_MARKET_RULE, subject_id, line, why or "is not a rule"
                    ))
                    continue
                rules.append(rule)

        cycles.append(
            ParsedCycle(
                kind=MARKET,
                record_id=subject_id,
                # A market track has one call rather than several sections, so
                # the cycle carries the track's own name and nothing more.
                section_name="",
                cycle_deadline=deadline,
                source_url=claim.source_url,
                verified_on=claim.verified_on,
                observed_open_on=_observed_open(deadline, claim.verified_on),
                rules=tuple(rules),
            )
        )
    return cycles


def build_cycles(
    claims: Iterable[SourceClaim], *, today: date
) -> tuple[list[ParsedCycle], list[ParseProblem]]:
    """Every cycle the verified claims support, and every line that made none.

    Only claims a named reviewer signed off reach this — and for market hard
    gates and festival section rules, only those a second reviewer independent
    of the author signed off. ``readable_claims`` enforces that by identity, so
    nothing here can lower the bar.
    """
    readable = readable_claims(claims, today=today)
    problems: list[ParseProblem] = []
    cycles = _festival_cycles(
        [c for c in readable if c.gate == GATE_FESTIVAL_SECTION], problems
    )
    cycles += _market_cycles(
        [c for c in readable if c.gate in {GATE_MARKET_CYCLE, GATE_MARKET_RULE}],
        problems,
    )
    return cycles, problems
