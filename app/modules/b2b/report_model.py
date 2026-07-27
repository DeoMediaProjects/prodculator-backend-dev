"""Builds the view model for the Business Intelligence PDF.

The PDF follows the client-approved template in the handoff pack. That template
is dashboard-first and chart-led, and its page order is fixed:

    cover, contents and how-to-read, 01 executive dashboard,
    02 territory demand, 03 budget concentration, 04 genre and format,
    05 emerging signals, 06 market context and methodology.

This module turns the metrics dict produced by `B2BService` into exactly that
shape, choosing a chart per section from the section `key`. It renders no
numbers of its own: every figure here is a count that already passed the
privacy floors in `_facts_to_metrics`. Sections that were withheld arrive empty
and are reported honestly rather than padded.

Prose in these reports avoids the em-dash aside habit (developer notes): use
commas, colons or separate sentences.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.modules.b2b import charts

# Section keys the flagship layout knows how to draw.
TERRITORY_CONSIDERED_KEYS = {"territories_considered", "territory", "home_country"}
TERRITORY_RECOMMENDED_KEYS = {"territories_recommended"}
BUDGET_KEYS = {"budget_range"}
GENRE_KEYS = {"genres"}
FORMAT_KEYS = {"format"}
MONTH_KEYS = {"submission_month"}

_ASSETS = Path(__file__).resolve().parents[2] / "templates" / "pdf" / "assets"


@lru_cache(maxsize=1)
def logo_data_uri() -> str:
    """The black Prodculator mark, base64, for embedding on the cover."""
    path = _ASSETS / "prodculator_logo_b64.txt"
    try:
        return f"data:image/png;base64,{path.read_text().strip()}"
    except OSError:
        return ""


def _sections_by_key(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for section in metrics.get("sections") or []:
        key = section.get("key")
        if key:
            out[key] = section
    return out


def _rows_of(section: dict[str, Any] | None) -> list[dict[str, Any]]:
    return list((section or {}).get("rows") or [])


def _pct(part: int, whole: int) -> str:
    return f"{round(part / whole * 100)}%" if whole else "n/a"


def _direction(current: int | None, previous: int | None) -> dict[str, str]:
    """Direction-of-travel caption for a KPI tile."""
    if not previous or current is None:
        return {"text": "No prior period", "cls": "flat"}
    delta = current - previous
    if delta == 0:
        return {"text": "Level on prior period", "cls": "flat"}
    pct = round(abs(delta) / previous * 100)
    arrow = "▲" if delta > 0 else "▼"
    return {
        "text": f"{arrow} {'+' if delta > 0 else '-'}{abs(delta)} ({pct}%) vs prior",
        "cls": "up" if delta > 0 else "dn",
    }


def _period_label(metrics: dict[str, Any]) -> str:
    months = metrics.get("composed_from_months") or []
    start = metrics.get("period_start", "")
    end = metrics.get("period_end", "")
    if len(months) == 3:
        first = months[0]
        year, month = int(first[:4]), int(first[5:7])
        return f"Q{(month - 1) // 3 + 1} {year}"
    if len(months) == 1:
        return f"{start[:7]}"
    return f"{start} to {end}"


_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _month_name(value: str) -> str:
    """"2026-04" reads as "Apr". Falls back to the raw label if unparseable."""
    try:
        return _MONTH_NAMES[int(str(value)[5:7]) - 1]
    except (ValueError, IndexError):
        return str(value)


def _cadence(metrics: dict[str, Any]) -> str:
    months = metrics.get("composed_from_months") or []
    if len(months) == 1:
        return "Monthly"
    if len(months) == 3:
        return "Quarterly"
    if len(months) >= 12:
        return "Annual"
    return "Custom period"


def build_report_view(
    metrics: dict[str, Any],
    *,
    client_name: str | None = None,
    reference: str | None = None,
) -> dict[str, Any]:
    """Assemble everything the PDF template needs, charts included."""
    by_key = _sections_by_key(metrics)
    signal_count = int(metrics.get("source_signal_count") or 0)
    previous_count = metrics.get("previous_signal_count")

    considered = next((by_key[k] for k in TERRITORY_CONSIDERED_KEYS if k in by_key), None)
    recommended = next((by_key[k] for k in TERRITORY_RECOMMENDED_KEYS if k in by_key), None)
    budget = next((by_key[k] for k in BUDGET_KEYS if k in by_key), None)
    genre = next((by_key[k] for k in GENRE_KEYS if k in by_key), None)
    fmt = next((by_key[k] for k in FORMAT_KEYS if k in by_key), None)
    genre_format = by_key.get("genre_format")
    format_month = by_key.get("format_month")
    comparison = next(
        (s for s in metrics.get("sections") or [] if s.get("kind") == "comparison"), None
    )
    context_sections = [
        s for s in metrics.get("sections") or [] if s.get("kind") == "dataset"
    ]

    view: dict[str, Any] = {
        "logo": logo_data_uri(),
        "title_top": metrics.get("title_top") or "Strategic Production",
        "title_bottom": metrics.get("title_bottom") or "Trend Intelligence",
        "full_title": metrics.get("title") or "Business Intelligence",
        "cadence": _cadence(metrics),
        "period_label": _period_label(metrics),
        "period_start": metrics.get("period_start"),
        "period_end": metrics.get("period_end"),
        "client_name": client_name or metrics.get("client_name") or "Your organisation",
        "signal_count": signal_count,
        "reference": reference or "",
        "thresholds": metrics.get("thresholds") or {},
        "suppressed_count": len(metrics.get("suppressed_segments") or []),
        "generated_at": metrics.get("generated_at", "")[:10],
    }

    # ---- 01 executive dashboard -------------------------------------------
    considered_rows = _rows_of(considered)
    recommended_rows = _rows_of(recommended)
    budget_rows = _rows_of(budget)
    format_rows = _rows_of(fmt)

    top_recommended = recommended_rows[0]["label"] if recommended_rows else None
    top_considered = considered_rows[0]["label"] if considered_rows else None
    dominant_budget = budget_rows[0] if budget_rows else None
    lead_format = format_rows[0] if format_rows else None

    view["kpis"] = [
        {
            "label": "Consented signals",
            "value": str(signal_count),
            "small": False,
            "direction": _direction(signal_count, previous_count),
        },
        {
            "label": "Top recommended territory",
            "value": top_recommended or top_considered or "Withheld",
            "small": True,
            "direction": {
                "text": f"{recommended_rows[0]['count']} recommendations" if recommended_rows else "Below threshold",
                "cls": "flat",
            },
        },
        {
            "label": "Dominant budget band",
            "value": dominant_budget["label"] if dominant_budget else "Withheld",
            "small": True,
            "direction": {
                "text": f"{dominant_budget['percentage']}% of pipeline" if dominant_budget else "Below threshold",
                "cls": "flat",
            },
        },
        {
            "label": "Lead format share",
            "value": f"{lead_format['percentage']}%" if lead_format else "Withheld",
            "small": False,
            "direction": {
                "text": lead_format["label"] if lead_format else "Below threshold",
                "cls": "flat",
            },
        },
    ]

    volumes = metrics.get("period_volumes") or []
    view["trend_svg"] = charts.trendline([(v["label"], v["value"]) for v in volumes])
    view["has_trend"] = bool(view["trend_svg"])

    view["headline"] = _headline(considered_rows, recommended_rows, signal_count)
    view["dashboard_readout"] = _dashboard_readout(metrics, volumes, signal_count)

    # ---- 02 territory demand ----------------------------------------------
    view["territory"] = _territory_block(considered, recommended)

    # ---- 03 budget concentration ------------------------------------------
    view["budget"] = _budget_block(budget, signal_count)

    # ---- 04 genre and format ----------------------------------------------
    view["genre_format"] = _genre_format_block(genre_format, format_month, genre)

    # ---- 05 emerging signals ----------------------------------------------
    movers = list((comparison or {}).get("rows") or [])
    view["movers"] = {
        "rows": movers[:12],
        "title": (comparison or {}).get("title") or "Period on period movement",
        "summary": (comparison or {}).get("summary"),
        "available": bool(movers),
    }

    # ---- 06 market context and methodology --------------------------------
    view["context_sections"] = context_sections
    view["other_sections"] = [
        s
        for s in metrics.get("sections") or []
        if s.get("kind") not in {"dataset", "comparison", "crosstab"}
        and s.get("key") not in {
            *TERRITORY_CONSIDERED_KEYS,
            *TERRITORY_RECOMMENDED_KEYS,
            *BUDGET_KEYS,
            *GENRE_KEYS,
            *FORMAT_KEYS,
            *MONTH_KEYS,
        }
    ]
    return view


def _headline(
    considered_rows: list[dict[str, Any]],
    recommended_rows: list[dict[str, Any]],
    signal_count: int,
) -> str:
    """The single most useful sentence a planner can take from the quarter.

    Written from the counts, never invented. The interesting story in this data
    is the gap between where productions originate and where the engine says the
    economics point, so that is what it reports when both readings exist.
    """
    if considered_rows and recommended_rows:
        by_considered = {r["label"]: r["count"] for r in considered_rows}
        by_recommended = {r["label"]: r["count"] for r in recommended_rows}
        gaps = [
            (label, by_recommended.get(label, 0) - count)
            for label, count in by_considered.items()
        ]
        widest_out = min(gaps, key=lambda g: g[1], default=None)
        risers = [
            (label, count - by_considered.get(label, 0))
            for label, count in by_recommended.items()
        ]
        widest_in = max(risers, key=lambda r: r[1], default=None)
        if widest_out and widest_in and widest_out[1] < 0 < widest_in[1]:
            return (
                f"{widest_out[0]} leads as a production base "
                f"({by_considered.get(widest_out[0], 0)} of {signal_count} productions) but appears in only "
                f"{by_recommended.get(widest_out[0], 0)} recommended-territory lists, a gap of "
                f"{abs(widest_out[1])} productions. {widest_in[0]} shows the inverse, recommended "
                f"{by_recommended.get(widest_in[0], 0)} times against {by_considered.get(widest_in[0], 0)} "
                "considering it. For an incentive-led planner this migration signal is the period's "
                "clearest single insight: productions originate in one market while the economics point "
                "to another."
            )
    if recommended_rows:
        top = recommended_rows[0]
        return (
            f"{top['label']} is the most recommended territory this period, appearing in "
            f"{top['count']} of {signal_count} production plans ({top['percentage']}% of those "
            "clearing the display threshold)."
        )
    return (
        "Territory detail is withheld this period because no single territory reached the "
        "display threshold. Volume-level figures below remain reportable."
    )


def _dashboard_readout(
    metrics: dict[str, Any], volumes: list[dict[str, Any]], signal_count: int
) -> str:
    floor = (metrics.get("thresholds") or {}).get("minimum_overall_records", 10)
    if len(volumes) >= 2:
        rising = all(
            volumes[i]["value"] <= volumes[i + 1]["value"] for i in range(len(volumes) - 1)
        )
        shape = (
            f"{len(volumes)} consecutive periods of growth indicate a deepening pipeline."
            if rising
            else "Pipeline volume has moved unevenly across the periods shown."
        )
    else:
        shape = "This is the first stored period, so no trend is available yet."
    return (
        f"{shape} At {signal_count} signals the period clears the overall privacy floor of "
        f"{floor} comfortably, so the sections below render in full."
    )


def _territory_block(
    considered: dict[str, Any] | None, recommended: dict[str, Any] | None
) -> dict[str, Any]:
    considered_rows = _rows_of(considered)
    recommended_rows = _rows_of(recommended)
    if considered_rows and recommended_rows:
        by_considered = {r["label"]: r["count"] for r in considered_rows}
        by_recommended = {r["label"]: r["count"] for r in recommended_rows}
        labels = sorted(
            set(by_considered) | set(by_recommended),
            key=lambda label: by_considered.get(label, 0) + by_recommended.get(label, 0),
            reverse=True,
        )[:8]
        rows = [(label, by_considered.get(label, 0), by_recommended.get(label, 0)) for label in labels]
        return {
            "available": True,
            "diverging": True,
            "svg": charts.diverging(rows),
            "readout": _territory_readout(rows),
        }
    single = considered_rows or recommended_rows
    if single:
        rows = [(r["label"], r["count"], f"{r['count']} · {r['percentage']}%") for r in single[:8]]
        return {
            "available": True,
            "diverging": False,
            "svg": charts.hbars(rows, color=charts.INK),
            "readout": (
                f"{single[0]['label']} leads territory demand with {single[0]['count']} productions, "
                f"{single[0]['percentage']}% of those clearing the display threshold."
            ),
        }
    return {
        "available": False,
        "diverging": False,
        "svg": "",
        "readout": (
            "No territory reached the per-segment display threshold this period, so territory "
            "detail is withheld rather than shown at a level that could identify a production."
        ),
    }


def _territory_readout(rows: list[tuple[str, int, int]]) -> str:
    origin = max(rows, key=lambda r: r[1])
    destination = max(rows, key=lambda r: r[2] - r[1])
    if destination[2] <= destination[1]:
        return (
            f"{origin[0]} leads on both readings, considered by {origin[1]} productions and "
            f"recommended {origin[2]} times. Declared intent and modelled economics agree this period, "
            "so no material migration pressure is visible."
        )
    return (
        f"{origin[0]} is considered by {origin[1]} productions but recommended only {origin[2]} times. "
        f"{destination[0]} runs the other way, considered by {destination[1]} and recommended "
        f"{destination[2]}. The distance between the two bars is the migration pressure a planner "
        "should watch: it indicates where production capital and services demand are likely to land "
        "next period rather than where they sit today."
    )


def _budget_block(budget: dict[str, Any] | None, signal_count: int) -> dict[str, Any]:
    rows = _rows_of(budget)
    if not rows:
        return {
            "available": False,
            "bars_svg": "",
            "donut_svg": "",
            "legend": [],
            "readout": (
                "No budget band reached the display threshold this period, so the budget breakdown "
                "is withheld."
            ),
        }
    bar_rows = [(r["label"], r["count"], f"{r['count']} · {r['percentage']}%") for r in rows]
    palette = [charts.INK, charts.YEL, charts.GRAPH, charts.GRAPH_L, charts.YEL_D]
    segments = [(r["label"], r["count"], palette[i % len(palette)]) for i, r in enumerate(rows)]
    covered = sum(r["count"] for r in rows)
    lead = rows[0]
    return {
        "available": True,
        "bars_svg": charts.hbars(bar_rows, color=charts.INK),
        "donut_svg": charts.donut(segments),
        "legend": [
            {"label": r["label"], "pct": r["percentage"], "color": palette[i % len(palette)]}
            for i, r in enumerate(rows)
        ],
        "covered": covered,
        "readout": (
            f"{lead['label']} is the largest band at {lead['percentage']}% of reportable productions "
            f"({lead['count']} of {covered}). For a services or equipment planner this indicates where "
            "sustained demand sits; for a studio it flags where competition for crew and stages will "
            "concentrate."
        ),
    }


def _genre_format_block(
    genre_format: dict[str, Any] | None,
    format_month: dict[str, Any] | None,
    genre: dict[str, Any] | None,
) -> dict[str, Any]:
    matrix_svg = ""
    matrix_available = False
    if genre_format and genre_format.get("row_labels"):
        matrix_svg = charts.heatmatrix(
            genre_format["cols"], genre_format["row_labels"], genre_format["data"]
        )
        matrix_available = True

    grouped_svg = ""
    months: list[str] = []
    grouped_available = False
    if format_month and format_month.get("row_labels"):
        months = [_month_name(m) for m in format_month["cols"]]
        rows = [
            (label, format_month["data"][i]) for i, label in enumerate(format_month["row_labels"])
        ]
        grouped_svg = charts.grouped_quarter(rows[:5], months=months)
        grouped_available = True

    return {
        "available": matrix_available or grouped_available,
        "matrix_svg": matrix_svg,
        "matrix_available": matrix_available,
        "matrix_cols": (genre_format or {}).get("cols") or [],
        "grouped_svg": grouped_svg,
        "grouped_available": grouped_available,
        "months": months,
        "readout": _genre_readout(genre_format, format_month, genre),
    }


def _genre_readout(
    genre_format: dict[str, Any] | None,
    format_month: dict[str, Any] | None,
    genre: dict[str, Any] | None,
) -> str:
    parts: list[str] = []
    if format_month and format_month.get("row_labels"):
        lead = format_month["row_labels"][0]
        series = format_month["data"][0]
        if len(series) >= 2:
            direction = "climbed" if series[-1] > series[0] else "eased"
            parts.append(
                f"{lead} volume {direction} across the period, moving from {series[0]} to {series[-1]}."
            )
    if genre_format and genre_format.get("row_labels"):
        top_genre = genre_format["row_labels"][0]
        parts.append(
            f"{top_genre} is the deepest genre in the matrix, and the cells show which formats "
            "that demand actually lands in."
        )
    elif genre and _rows_of(genre):
        rows = _rows_of(genre)
        parts.append(f"{rows[0]['label']} leads genre mix at {rows[0]['percentage']}%.")
    if not parts:
        return (
            "Genre and format detail is withheld this period: no combination reached the "
            "per-cell display threshold."
        )
    parts.append(
        "A commissioning planner reads this as where the pipeline is deepest and where "
        "competition for talent and crew will be fiercest."
    )
    return " ".join(parts)
