"""Canonical vocabulary + budget normalisation for production_signals v2.

Single source of truth for how raw intake/analysis values are coerced before they
are written to production_signals. Keeping this here (not in the report path and not
in the metrics reader) guarantees writes and reads agree on vocabulary, which is what
stops segment fragmentation below the privacy threshold (R-10).

Rule: the writer stores canonical values ONLY. Display labels are mapped on read.
"""
from __future__ import annotations

from typing import Any

# --- Format canonicalisation (R-10) -----------------------------------------
# Every display/legacy label a script may arrive with -> one canonical value.
FORMAT_MAP: dict[str, str] = {
    "feature film": "feature",
    "feature": "feature",
    "short": "short",
    "short film": "short",
    "documentary": "documentary",
    "docuseries": "documentary",
    "tv series": "tv_series",
    "tv pilot": "tv_series",
    "tv": "tv_series",
    "limited series": "tv_series",
    "mini-series": "tv_series",
    "mini series": "tv_series",
    "series": "tv_series",
    "animation": "animation",
    "animated feature": "animation",
    "animation series": "animation",
}

FORMAT_DISPLAY: dict[str, str] = {
    "feature": "Feature Film",
    "short": "Short",
    "documentary": "Documentary",
    "tv_series": "TV / Series",
    "animation": "Animation",
}

# Canonical genre list (lowercase). Anything outside maps through as-is lowercased,
# so a new genre never silently disappears — it just forms its own segment.
CANONICAL_GENRES = {
    "drama", "thriller", "sci-fi", "horror", "comedy", "romance", "action",
    "adventure", "fantasy", "mystery", "documentary", "biopic", "period",
    "western", "animation", "musical", "crime", "war", "sports", "family",
}

# GBP-normalised budget bands (Decision R-1). Thresholds in GBP.
_BUDGET_BANDS_GBP: tuple[tuple[float, str], ...] = (
    (400_000, "micro"),
    (4_000_000, "low"),
    (24_000_000, "medium"),
    (80_000_000, "high"),
    (float("inf"), "tentpole"),
)

BUDGET_BAND_DISPLAY: dict[str, str] = {
    "micro": "Micro (< £400k)",
    "low": "Low (£400k–£4m)",
    "medium": "Mid (£4m–£24m)",
    "high": "High (£24m–£80m)",
    "tentpole": "Tentpole (£80m+)",
}


def canonical_format(value: Any) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return FORMAT_MAP.get(key, key.replace(" ", "_"))


def canonical_genres(values: Any) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return None
    out: list[str] = []
    for v in values:
        s = str(v).strip().lower()
        if s:
            out.append(s)
    return out or None


def gbp_band(amount_gbp: float | None) -> str | None:
    if amount_gbp is None or amount_gbp <= 0:
        return None
    for upper, label in _BUDGET_BANDS_GBP:
        if amount_gbp < upper:
            return label
    return None


def display_format(value: str | None) -> str:
    if not value:
        return "Unknown"
    return FORMAT_DISPLAY.get(value, value.replace("_", " ").title())


def display_budget_band(value: str | None) -> str:
    if not value:
        return "Unknown"
    return BUDGET_BAND_DISPLAY.get(value, value.title())
