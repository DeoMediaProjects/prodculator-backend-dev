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
# The table moved to app.core.formats so incentive-programme format eligibility
# reads the same vocabulary this writer stores. Re-exported under the original
# names, so every existing import keeps working and there is still one table.
from app.core.formats import (  # noqa: F401  (re-exported)
    CANONICAL_FORMATS,
    FORMAT_DISPLAY,
    FORMAT_MAP,
    canonical_format,
)

# Canonical genre list (lowercase). Anything outside maps through as-is lowercased,
# so a new genre never silently disappears — it just forms its own segment.
CANONICAL_GENRES = {
    "drama", "thriller", "sci-fi", "horror", "comedy", "romance", "action",
    "adventure", "fantasy", "mystery", "documentary", "biopic", "period",
    "western", "animation", "musical", "crime", "war", "sports", "family",
    # Kept in sync with the frontend genre picker (AnalysisWizard GENRE_OPTIONS).
    "history", "music", "superhero", "coming-of-age", "psychological",
    "disaster", "spy", "noir",
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
