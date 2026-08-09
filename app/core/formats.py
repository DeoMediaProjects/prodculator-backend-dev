"""Canonical production-format vocabulary.

One place decides what a format IS. The mapping already existed inside
``app.modules.b2b.signal_normalise`` for the production-signal writer; it now lives
here because format eligibility on incentive programmes needs the same vocabulary,
and two copies of a canonicalisation table is how "Short", "short film" and
"short_film" end up being three different formats.

Rule, unchanged from the signal writer: canonical values are stored, display labels
are mapped on read.
"""
from __future__ import annotations

from typing import Any

#: Every display or legacy label a project may arrive with, mapped to one canonical
#: value. Keys are lowercased on lookup, so only lowercase keys belong here.
FORMAT_MAP: dict[str, str] = {
    "feature film": "feature",
    "feature": "feature",
    "short": "short",
    "short film": "short",
    # Historical spellings that would otherwise fragment into their own formats.
    "short-film": "short",
    "short_film": "short",
    "shorts": "short",
    "documentary": "documentary",
    "docuseries": "documentary",
    "tv series": "tv_series",
    "tv pilot": "tv_series",
    "tv": "tv_series",
    "tv movie": "tv_series",
    "tv_movie": "tv_series",
    "limited series": "tv_series",
    "mini-series": "tv_series",
    "mini series": "tv_series",
    "series": "tv_series",
    "animation": "animation",
    "animated feature": "animation",
    "animation series": "animation",
}

#: Canonical value -> label shown to a reader.
FORMAT_DISPLAY: dict[str, str] = {
    "feature": "Feature Film",
    "short": "Short",
    "documentary": "Documentary",
    "tv_series": "TV / Series",
    "animation": "Animation",
}

#: The canonical set. A value outside it still passes through ``canonical_format``
#: rather than being dropped, so a new format never silently disappears.
CANONICAL_FORMATS = frozenset(FORMAT_DISPLAY)


def canonical_format(value: Any) -> str | None:
    """The canonical token for *value*, or None when it says nothing.

    An unrecognised label is normalised rather than discarded: losing it would make
    an unknown format look like a missing one, and those are different problems.
    """
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return FORMAT_MAP.get(key, key.replace(" ", "_").replace("-", "_"))


def format_display(value: Any) -> str | None:
    """Reader-facing label for *value*, canonicalising first."""
    token = canonical_format(value)
    if not token:
        return None
    return FORMAT_DISPLAY.get(token, token.replace("_", " ").title())
