"""When the declared audience and the screenplay disagree about who this is for.

THE DEFECT THIS EXISTS FOR
--------------------------
The Devil Wears Prada regression found Chicago International Children's Film
Festival and New York International Children's Film Festival recommended for an
adult workplace comedy-drama. Nothing had gone wrong in the matcher: a
target-audience field said kids/family, the festivals accept kids/family, and
the two overlapped. One intake checkbox had quietly outvoted the screenplay.

The regression report states the rule::

    V2 must preserve any actual USER value if present, but it must not let that
    single field override contradictory script evidence without surfacing the
    conflict.

Both halves matter. Locked decision I.4 says objective user-declared facts are
not overwritten by screenplay inference, so the declared value is kept, stays in
ProjectFacts, and is still what the report says the producer told us. What it
stops doing is silently earning matches.

WHAT COUNTS AS A CONFLICT
-------------------------
Only a direct contradiction between two decisive answers: the audience the
producer declared, and ``targetAudience`` from the script analysis. Anything
undecided on either side is not a conflict, it is an absence.

``General audiences`` is specifically NOT treated as decisive. It is the script
service's own default — ``_choose_mode(audience_values, default="General
audiences")`` — so reading it as "this is a children's film" would raise a
conflict on every script the analyser was unsure about, and the fix would be
worse than the defect.

The vocabulary is deliberately small. A wider one buys a few more detections and
risks suppressing legitimate matches on a guess about what "urban" or "arthouse"
implies about age, which is the same class of mistake in the other direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Audiences that mean children are the intended viewers.
_CHILDREN: frozenset[str] = frozenset(
    {"children", "child", "kids", "kid", "family", "families", "all ages"}
)

#: Audiences that mean they are not.
_ADULT: frozenset[str] = frozenset(
    {"adult", "adults", "adults only", "mature", "mature audiences", "18+", "over 18"}
)

#: Phrases that contain a decisive word and are not decisive themselves.
#: "Young adult" overlaps family viewing and contradicts none of it, but it
#: tokenises to "adult" — so it has to be recognised whole, before the words
#: are looked at, or a YA film declared for families would raise a conflict
#: and lose its matches.
_NEUTRAL_PHRASES: frozenset[str] = frozenset(
    {"young adult", "young adults", "ya", "general audiences", "general"}
)

CHILDREN_FAMILY = "CHILDREN_FAMILY"
ADULT = "ADULT"
UNDECIDED = "UNDECIDED"

_WORD = re.compile(r"[a-z0-9+]+")


@dataclass(frozen=True)
class AudienceConflict:
    """A declared audience the screenplay contradicts."""

    declared: tuple[str, ...]
    declared_bucket: str
    script_audience: str
    script_bucket: str

    @property
    def note(self) -> str:
        """One sentence for the report's key flags.

        Names both sides and resolves neither. The producer knows which is
        right and the engine does not, so the report's job is to put the
        disagreement in front of them, not to pick.
        """
        declared = ", ".join(self.declared)
        return (
            f"You declared a target audience of {declared}, and the screenplay "
            f"reads as {self.script_audience.lower()}. Festival and distributor "
            f"matching does not use the declared audience while the two "
            f"disagree, because one intake field should not outvote the script. "
            f"Correct whichever is wrong and re-run to use it."
        )


def _bucket(values: list[str]) -> str:
    """Which side of the disagreement a set of audience words falls on.

    Undecided when it matches neither, and also when it matches both: a
    production declaring children and adults has told us something this rule
    cannot arbitrate, and guessing would be the behaviour it exists to stop.
    """
    words: set[str] = set()
    phrases: set[str] = set()
    for value in values:
        lowered = " ".join(str(value or "").strip().lower().split())
        if not lowered or lowered in _NEUTRAL_PHRASES:
            continue
        phrases.add(lowered)
        words.update(_WORD.findall(lowered))

    children = bool((words | phrases) & _CHILDREN)
    adult = bool((words | phrases) & _ADULT)
    if children and not adult:
        return CHILDREN_FAMILY
    if adult and not children:
        return ADULT
    return UNDECIDED


def detect_audience_conflict(
    declared_audience: list[str] | None,
    script_analysis: Any = None,
) -> AudienceConflict | None:
    """The conflict between declared and script-derived audience, if any.

    ``None`` whenever either side is undecided. A conflict suppresses a scoring
    signal, so a false one costs a producer real matches — it has to be a
    contradiction, not a silence.
    """
    declared = [str(a).strip() for a in (declared_audience or []) if str(a).strip()]
    if not declared:
        return None

    script_audience = str(getattr(script_analysis, "targetAudience", "") or "").strip()
    if not script_audience:
        return None

    declared_bucket = _bucket(declared)
    script_bucket = _bucket([script_audience])
    if UNDECIDED in (declared_bucket, script_bucket):
        return None
    if declared_bucket == script_bucket:
        return None

    return AudienceConflict(
        declared=tuple(declared),
        declared_bucket=declared_bucket,
        script_audience=script_audience,
        script_bucket=script_bucket,
    )
