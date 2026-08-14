"""Countable screenplay facts, counted — not asked of a language model.

Two reports generated from the same screenplay disagreed:

    earlier   ~45 scenes   ~80% interior   5 shooting days   complexity Medium
    latest     29 scenes    72% interior   6 shooting days   complexity High

Neither was a parse. Every one of those numbers originated in an LLM JSON response
per chunk and was then summed, averaged or voted on in Python:

* scene counts came from ``extSceneCount`` / ``intSceneCount``, model-authored per
  chunk and added up;
* the interior percentage divided one model number by the sum of two, so a handful of
  ambiguous ``INT./EXT.`` slugs bucketed differently between runs moves it several
  points;
* shooting days were a trimmed mean of per-chunk guesses, floored at
  ``total_scenes // 10``;
* complexity was a free choice of one of four labels in a later, separate call, with
  no count fed into it and no rule behind it;
* each chunk was prefixed with an 800-character overlap tail of the previous chunk
  and nothing told the model to skip it, so headings in that window could be counted
  twice; and
* a single chunk timing out was swallowed silently, deleting its scenes from the
  total with no marker in the report.

The one regex in the pipeline — a slug-line matcher — was used only to choose chunk
boundaries. Its match count was never the scene count.

A scene heading is a lexical fact about a text file. So is whether it says INT. or
EXT., whether it says DAY or NIGHT, and whether it is a continuation of the heading
above it. This module reads those directly, and it is the authoritative source for
them: identical bytes in, identical numbers out, no network call, no temperature.

Deliberately NOT moved here: genre, tone, budget tier, and the qualitative challenge
flags. Those are readings of a script, not counts in it, and a parser has no business
asserting them. This module answers only what can be counted, and the LLM's values
survive as the fallback for any file this parser cannot read.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

#: Bumped when a change here would alter the numbers for an unchanged input. Recorded
#: on the output so a metric can be traced to the parser version that produced it, and
#: so "the same file gave different numbers" is answerable rather than speculative.
PARSER_VERSION = "1.0.0"

# ── Scene headings ───────────────────────────────────────────────────────────
# Anchored to the line start, allowing the leading indent screenplay PDFs carry, and
# an optional scene number prefix ("12  INT. KITCHEN - DAY", "A1 EXT. ...").

_SLUG_RE = re.compile(
    r"""^[ \t]{0,10}
        (?:(?:[0-9]{1,4}[A-Za-z]?|[A-Za-z][0-9]{1,3})[ \t.)\-]{1,6})?   # optional scene number
        (?P<prefix>
            INT\.?/EXT\.?|EXT\.?/INT\.?|INT\.?\ ?-\ ?EXT\.?|EXT\.?\ ?-\ ?INT\.?
          | I\.?/E\.?|E\.?/I\.?
          | INT\b\.?|EXT\b\.?|EST\b\.?
        )
        (?P<rest>[ \t].*)?$
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Mixed-interior/exterior prefixes. The old prompt named INT. and EXT. only and said
#: nothing about these, so a mixed slug could land in either bucket, in neither, or in
#: both — and because the interior percentage divides by (int + ext), a scene counted
#: in neither silently moved the percentage. They are counted as their own category
#: here and reported separately, so the split always sums to the scene total.
_MIXED_PREFIXES = ("int./ext", "int/ext", "ext./int", "ext/int", "int-ext",
                   "ext-int", "int. - ext", "ext. - int", "i/e", "e/i")

#: Continuation markers. A heading carrying one of these resumes the scene above it
#: rather than opening a new one, so counting it again inflates the total. There was
#: no dedup of any kind before this.
#:
#: The trailing class allows the punctuation these markers are actually written with.
#: Anchoring on ``\s*$`` alone missed "(CONTINUOUS)" and "(CONT'D)" — the standard
#: notation — because the closing bracket sits between the word and the line end.
#: A bare "LATER" is deliberately NOT a continuation: time has moved, so it is a new
#: scene even at the same location.
_CONTINUATION_RE = re.compile(
    r"\b(?:CONT(?:INUED|'D|D)?|CONTINUOUS|SAME(?:\s+(?:TIME|AS\s+BEFORE))?|"
    r"LATER\s+THAT\s+(?:DAY|NIGHT))\b[.)\]\s]*$",
    re.IGNORECASE,
)

_NIGHT_RE = re.compile(
    r"\b(?:NIGHT|DUSK|DAWN|EVENING|MIDNIGHT|LATE\s+NIGHT|PRE-?DAWN|TWILIGHT|NIGHTTIME)\b",
    re.IGNORECASE,
)
_DAY_RE = re.compile(
    r"\b(?:DAY|MORNING|AFTERNOON|MIDDAY|NOON|DAYTIME|SUNRISE|SUNSET|LUNCHTIME)\b",
    re.IGNORECASE,
)

#: Everything after the last " - " / " -- " on a slug is conventionally the time of
#: day. Falling back to scanning the whole slug would read a location called
#: "DAYCARE" as a day scene.
_TIME_SPLIT_RE = re.compile(r"\s+[-–—]{1,2}\s+")


class Scene:
    """One parsed scene heading."""

    __slots__ = ("index", "raw", "prefix", "location", "time_of_day", "int_ext",
                 "is_continuation", "line_no")

    def __init__(
        self,
        index: int,
        raw: str,
        prefix: str,
        location: str,
        time_of_day: str,
        int_ext: str,
        is_continuation: bool,
        line_no: int,
    ) -> None:
        self.index = index
        self.raw = raw
        self.prefix = prefix
        self.location = location
        self.time_of_day = time_of_day
        self.int_ext = int_ext          # "interior" | "exterior" | "mixed"
        self.is_continuation = is_continuation
        self.line_no = line_no

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "heading": self.raw,
            "location": self.location,
            "timeOfDay": self.time_of_day,
            "intExt": self.int_ext,
            "isContinuation": self.is_continuation,
            "line": self.line_no,
        }


def _classify_int_ext(prefix: str) -> str:
    normalised = prefix.lower().replace(" ", "").rstrip(".")
    for token in _MIXED_PREFIXES:
        if normalised.startswith(token.replace(" ", "").rstrip(".")):
            return "mixed"
    if normalised.startswith("int"):
        return "interior"
    if normalised.startswith("ext") or normalised.startswith("est"):
        # EST. (establishing) is an exterior convention.
        return "exterior"
    return "mixed"


def _split_location_time(rest: str) -> tuple[str, str]:
    """Location and time-of-day from the part of a slug after the prefix."""
    body = (rest or "").strip().lstrip("-–— ").strip()
    if not body:
        return "", ""
    parts = _TIME_SPLIT_RE.split(body)
    if len(parts) == 1:
        return parts[0].strip(" .-"), ""
    return " - ".join(p.strip() for p in parts[:-1]).strip(" .-"), parts[-1].strip(" .-")


def _classify_time_of_day(time_text: str, whole_slug: str) -> str:
    """"day" | "night" | "other". Reads the time segment, not the location name."""
    candidate = time_text or ""
    if not candidate:
        # No " - TIME" segment. Only then fall back to the whole slug, accepting the
        # small risk of a location name matching, because the alternative is losing
        # every scene written without the dash convention.
        candidate = whole_slug
    if _NIGHT_RE.search(candidate):
        return "night"
    if _DAY_RE.search(candidate):
        return "day"
    return "other"


def _normalise_location(location: str) -> str:
    """Location key for grouping. Upper-cased, trailing qualifiers stripped."""
    text = re.sub(r"\s+", " ", (location or "").strip()).upper()
    text = re.sub(r"\s*\((?:CONT(?:INUED|'D|D)?|CONTINUOUS|FLASHBACK|V\.?O\.?)\)\s*$",
                  "", text, flags=re.IGNORECASE)
    return text.strip(" .,-")


def parse_scenes(script_text: str) -> list[Scene]:
    """Every scene heading in *script_text*, in order.

    Continuations are parsed and marked rather than dropped, so a caller can see
    what was excluded from the count and why.
    """
    scenes: list[Scene] = []
    if not script_text:
        return scenes

    index = 0
    previous_location = ""
    for line_no, line in enumerate(script_text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _SLUG_RE.match(line)
        if not match:
            continue
        prefix = (match.group("prefix") or "").strip()
        rest = match.group("rest") or ""
        raw = line.strip()

        # A prefix with nothing after it is prose that happens to begin with the word
        # ("Interior designers arrived") rather than a heading.
        if not rest.strip():
            continue

        location, time_text = _split_location_time(rest)
        if not location:
            continue

        normalised = _normalise_location(location)
        is_continuation = bool(
            _CONTINUATION_RE.search(raw)
            and normalised
            and normalised == previous_location
        )

        index += 1
        scenes.append(
            Scene(
                index=index,
                raw=raw,
                prefix=prefix,
                location=normalised,
                time_of_day=_classify_time_of_day(time_text, raw),
                int_ext=_classify_int_ext(prefix),
                is_continuation=is_continuation,
                line_no=line_no,
            )
        )
        previous_location = normalised

    return scenes


# ── Shooting days ────────────────────────────────────────────────────────────
# Scenes per day, by complexity. A rule with stated numbers, so a producer can
# disagree with the assumption rather than with an opaque estimate. The previous
# figure was a trimmed mean of per-chunk model guesses floored at scenes/10, which
# is why 29 scenes produced 6 days while 45 scenes produced 5.

_SCENES_PER_DAY: dict[str, float] = {
    "Low": 9.0,
    "Medium": 7.0,
    "High": 5.0,
    "Very High": 3.5,
}


def estimate_shooting_days(countable_scenes: int, complexity: str) -> int:
    """Shooting days from scene count and complexity. Deterministic by construction."""
    if countable_scenes <= 0:
        return 0
    per_day = _SCENES_PER_DAY.get(complexity, _SCENES_PER_DAY["Medium"])
    import math
    return max(1, math.ceil(countable_scenes / per_day))


# ── Complexity ───────────────────────────────────────────────────────────────
# Rule-based, from counted inputs plus the qualitative flags the LLM is genuinely
# better placed to spot. Every contribution is recorded so the label can be
# explained rather than asserted — previously the label was a free model choice with
# no count fed into it at all, which is how the same script moved Medium → High.

_COMPLEXITY_BANDS = ((7, "Very High"), (4, "High"), (2, "Medium"))


def score_complexity(
    *,
    scene_count: int,
    exterior_pct: float | None,
    night_pct: float | None,
    distinct_locations: int,
    flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Complexity label plus the drivers behind it."""
    flags = flags or {}
    points = 0
    drivers: list[str] = []

    def add(condition: Any, weight: int, driver: str) -> None:
        nonlocal points
        if condition:
            points += weight
            drivers.append(driver)

    add(scene_count >= 80, 2, f"{scene_count} scenes")
    add(40 <= scene_count < 80, 1, f"{scene_count} scenes")
    add(distinct_locations >= 20, 2, f"{distinct_locations} distinct locations")
    add(8 <= distinct_locations < 20, 1, f"{distinct_locations} distinct locations")
    add(
        exterior_pct is not None and exterior_pct >= 50,
        1,
        f"{exterior_pct:.0f}% exterior scenes" if exterior_pct is not None else "",
    )
    add(
        night_pct is not None and night_pct >= 30,
        1,
        f"{night_pct:.0f}% night scenes" if night_pct is not None else "",
    )
    add(flags.get("stunts"), 1, "stunt sequences")
    add(flags.get("waterWork"), 1, "water work")
    add(flags.get("animalWrangling"), 1, "animal work")
    add(flags.get("weatherDependent"), 1, "weather-dependent sequences")
    add(flags.get("historicalPeriod"), 1, "period setting")
    vfx_scenes = flags.get("vfxHeavySceneCount") or 0
    add(vfx_scenes and vfx_scenes >= 5, 1, f"{vfx_scenes} VFX-heavy scenes")
    languages = flags.get("languages") or []
    add(
        isinstance(languages, list) and len(languages) > 1,
        1,
        f"dialogue in {len(languages)} languages" if isinstance(languages, list) else "",
    )

    label = "Low"
    for threshold, band in _COMPLEXITY_BANDS:
        if points >= threshold:
            label = band
            break

    return {
        "complexity": label,
        "points": points,
        "drivers": [d for d in drivers if d],
    }


# ── Top-level ────────────────────────────────────────────────────────────────

def compute_metrics(
    script_text: str,
    *,
    flags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every countable metric for *script_text*.

    Returns None-valued metrics rather than zeros when no scene heading was found:
    a screenplay this parser could not read must fall back to whatever the previous
    path produced, and zeros would silently win that comparison.
    """
    scenes = parse_scenes(script_text)
    countable = [s for s in scenes if not s.is_continuation]
    continuations = len(scenes) - len(countable)

    digest = hashlib.sha256((script_text or "").encode("utf-8", "replace")).hexdigest()

    if not countable:
        return {
            "parserVersion": PARSER_VERSION,
            "scriptSha256": digest,
            "totalScenes": None,
            "parsed": False,
        }

    interior = sum(1 for s in countable if s.int_ext == "interior")
    exterior = sum(1 for s in countable if s.int_ext == "exterior")
    mixed = sum(1 for s in countable if s.int_ext == "mixed")
    total = len(countable)

    night = sum(1 for s in countable if s.time_of_day == "night")
    day = sum(1 for s in countable if s.time_of_day == "day")
    other_time = total - night - day

    location_counts: dict[str, int] = {}
    for scene in countable:
        if scene.location:
            location_counts[scene.location] = location_counts.get(scene.location, 0) + 1

    # A mixed scene is genuinely both, so it is counted into each side for the
    # percentage while the raw counts stay separable. Without this the two
    # percentages did not sum to 100 and nothing said why.
    interior_pct = round((interior + mixed) / total * 100, 1)
    exterior_pct = round((exterior + mixed) / total * 100, 1)
    night_pct = round(night / total * 100, 1)

    complexity = score_complexity(
        scene_count=total,
        exterior_pct=exterior_pct,
        night_pct=night_pct,
        distinct_locations=len(location_counts),
        flags=flags,
    )

    return {
        "parserVersion": PARSER_VERSION,
        "scriptSha256": digest,
        "parsed": True,
        "totalScenes": total,
        "headingsFound": len(scenes),
        "continuationHeadings": continuations,
        "interiorScenes": interior,
        "exteriorScenes": exterior,
        "mixedScenes": mixed,
        "interiorPct": interior_pct,
        "exteriorPct": exterior_pct,
        "dayScenes": day,
        "nightScenes": night,
        "otherTimeScenes": other_time,
        "nightPct": night_pct,
        "distinctLocations": len(location_counts),
        "namedLocations": dict(
            sorted(location_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "primaryLocation": max(
            location_counts.items(), key=lambda kv: (kv[1], kv[0])
        )[0] if location_counts else None,
        "complexity": complexity["complexity"],
        "complexityPoints": complexity["points"],
        "complexityDrivers": complexity["drivers"],
        "estimatedShootingDays": estimate_shooting_days(total, complexity["complexity"]),
        "scenes": [s.as_dict() for s in scenes],
    }
