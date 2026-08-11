"""Draft a logline and synopsis from the analysis a report already holds.

Producers reach the investor summary with two blank prose fields and a finished
report sitting behind them that already describes the story. This drafts both from
that analysis so the blank page is not the starting point.

Written from the report, never from the script. The script text is never stored, so
by the time the investor summary is opened the analysis is the only record of the
story that exists. That constraint is also the honest one: this can restate what the
report already says and nothing else, so it cannot invent plot the analysis never saw.

Both fields come back as a draft for the producer to edit. Nothing here is written to
the report; saving stays an explicit action.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 6000

SYSTEM_PROMPT = "\n".join([
    "You write concise, factual copy for film financing documents.",
    "",
    "You are given an existing analysis of a screenplay. Write only from what that",
    "analysis states. Never invent plot points, character names, themes or outcomes",
    "it does not mention. If the analysis is too thin to support a field, return an",
    "empty string for that field rather than filling it with plausible-sounding",
    "material. An empty field a producer can fill in is worth more than a confident",
    "sentence they have to notice is wrong.",
    "",
    "Return strict JSON with exactly two keys and no other text:",
    '  "logline": one sentence, at most 40 words, present tense, naming the',
    "             protagonist and the central conflict.",
    '  "synopsis": two to three sentences, at most 90 words, covering setup,',
    "              conflict and stakes. No marketing language, no rhetorical",
    "              questions, no closing pitch.",
])

# Report fields that describe the story, in the order they are most useful. Only
# narrative text is used: figures, rankings and territory data say nothing about
# what the film is about and would only dilute the prompt.
_STORY_FIELDS = (
    ("scriptOverview", "Script overview"),
    ("storyReading", "Story reading"),
    ("genre", "Genre"),
    ("tone", "Tone"),
    ("scale", "Scale"),
    ("productionChallenges", "Production challenges"),
)


def build_story_context(report_data: dict[str, Any]) -> str:
    """Collect the narrative parts of a report into prompt context.

    Returns an empty string when the report carries no story narrative at all, which
    the caller must treat as "cannot draft" rather than drafting from nothing.
    """
    if not isinstance(report_data, dict):
        return ""

    parts: list[str] = []
    summary = report_data.get("executiveSummary")
    if isinstance(summary, dict):
        for key in ("scriptOverview", "storyOverview", "narrative"):
            value = summary.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(f"Script overview: {value.strip()}")
                break

    for key, label in _STORY_FIELDS:
        value = report_data.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{label}: {value.strip()}")
        elif isinstance(value, list) and value:
            joined = "; ".join(str(v).strip() for v in value if str(v).strip())
            if joined:
                parts.append(f"{label}: {joined}")

    return "\n\n".join(parts)[:MAX_CONTEXT_CHARS]


def parse_draft(text: str) -> dict[str, str]:
    """Pull the two fields out of a model response.

    Anything unparseable returns empty strings rather than raising: a producer who
    asked for a draft and got nothing can still type, whereas an exception loses the
    form they were filling in.
    """
    if not text:
        return {"logline": "", "synopsis": ""}

    # Strict JSON is asked for; a fenced code block is the usual deviation.
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {"logline": "", "synopsis": ""}
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        logger.warning("Investor copy draft was not valid JSON")
        return {"logline": "", "synopsis": ""}
    if not isinstance(parsed, dict):
        return {"logline": "", "synopsis": ""}

    return {
        "logline": str(parsed.get("logline") or "").strip(),
        "synopsis": str(parsed.get("synopsis") or "").strip(),
    }


def build_user_content(
    *,
    script_title: str,
    story_context: str,
    genres: list[str] | None,
    production_format: str | None,
) -> str:
    return "\n".join([
        f"Title: {script_title or 'Untitled'}",
        f"Format: {production_format or 'unspecified'}",
        f"Genres: {', '.join(genres) if genres else 'unspecified'}",
        "",
        "Existing analysis of the screenplay:",
        story_context,
    ])
