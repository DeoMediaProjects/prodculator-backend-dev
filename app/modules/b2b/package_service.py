"""B2B package assembly — the admin data-pull layer.

This is where an admin composes a report package from the platform's own data. Two
data families feed a package:

  1. PLATFORM SIGNALS  — aggregated production_signals v2 (consented, non-internal,
     FX-normalised, canonical vocab). Thresholded at 10 overall / 5 per segment.
  2. MARKET CONTEXT    — curated admin datasets (incentives, festivals, distributors,
     crew costs, comparables). Always renders; volume-independent (Decision 6, Part A).

A package is therefore two-part: Part A Market Context + Part B Platform Signals.

The section library below is the catalogue an admin picks from to build either a
standard product or a bespoke enterprise report. Every signal section carries its
source ("considered" vs "recommended" territory, GBP budget band, etc.) so the
provenance is explicit. Privacy suppression lives in B2BService section renderers and
is inherited here — no composition can switch it off.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

PRIVACY_MIN_OVERALL = 10
PRIVACY_MIN_SEGMENT = 5


@dataclass(frozen=True)
class SectionDef:
    key: str
    title: str
    part: str  # "signals" | "context"
    group: str  # library grouping for the admin UI
    signal_field: str | None = None  # production_signals column, if a signal section
    flatten: bool = False  # list-valued signal field
    dataset: str | None = None  # curated dataset name, if a context section
    kind: str = "distribution"  # distribution | numeric_band | month | dataset_table
    note: str = ""


# --- SECTION LIBRARY --------------------------------------------------------
# Everything an admin can add to a package. Grouped for the composition UI.
SECTION_LIBRARY: list[SectionDef] = [
    # Part B — Platform Demand Signals
    SectionDef("sig_territory_home", "Production Volume by Home Country", "signals",
               "Territory Signals", signal_field="home_country",
               note="Declared production-company base."),
    SectionDef("sig_territory_considered", "Territories Under Consideration", "signals",
               "Territory Signals", signal_field="territories_considered", flatten=True,
               note="Declared by producers at intake — forward-looking demand."),
    SectionDef("sig_territory_recommended", "Engine-Recommended Territories", "signals",
               "Territory Signals", signal_field="territories_recommended", flatten=True,
               note="Prodculator engine output — proprietary, unavailable elsewhere."),
    SectionDef("sig_format", "Production Type Distribution", "signals",
               "Production Signals", signal_field="format"),
    SectionDef("sig_genre", "Genre Mix", "signals",
               "Production Signals", signal_field="genres", flatten=True),
    SectionDef("sig_budget", "Budget Band Breakdown (GBP-normalised)", "signals",
               "Production Signals", signal_field="budget_range",
               note="FX-normalised to GBP before banding."),
    SectionDef("sig_camera", "Camera & Equipment Mix", "signals",
               "Equipment Signals", signal_field="camera_equipment", flatten=True),
    SectionDef("sig_crew", "Crew Size Distribution", "signals",
               "Crew & Cast Signals", signal_field="crew_size", kind="numeric_band"),
    SectionDef("sig_principal", "Principal Cast Demand", "signals",
               "Crew & Cast Signals", signal_field="principal_cast", kind="numeric_band"),
    SectionDef("sig_supporting", "Supporting Cast Demand", "signals",
               "Crew & Cast Signals", signal_field="supporting_cast", kind="numeric_band"),
    SectionDef("sig_extras", "Extras Demand", "signals",
               "Crew & Cast Signals", signal_field="background_extras", kind="numeric_band",
               note="Requires background_extras at intake (R-4 decision)."),
    SectionDef("sig_audience", "Target Audience Quadrants", "signals",
               "Audience Signals", signal_field="target_audience", flatten=True,
               note="Declared only, never inferred. Seed of Audience Intent product."),
    SectionDef("sig_audience_seg", "Audience Segments", "signals",
               "Audience Signals", signal_field="audience_segments", flatten=True),
    SectionDef("sig_language", "Primary Language Demand", "signals",
               "Audience Signals", signal_field="primary_languages", flatten=True),
    SectionDef("sig_month", "Monthly Submission Volume", "signals",
               "Timing Signals", signal_field="submission_date", kind="month"),
    SectionDef("sig_completion", "Completion Window Clusters", "signals",
               "Timing Signals", signal_field="completion_window",
               note="When productions expect to be market-ready."),
    # Part A — Market Context (curated datasets, always render)
    SectionDef("ctx_incentives", "Incentive Programme Landscape", "context",
               "Market Context", dataset="incentives", kind="dataset_table"),
    SectionDef("ctx_festivals", "Festival Calendar & Deadlines", "context",
               "Market Context", dataset="festivals", kind="dataset_table"),
    SectionDef("ctx_distributors", "Distributor Market Map", "context",
               "Market Context", dataset="distributors", kind="dataset_table"),
    SectionDef("ctx_crew_costs", "Crew Cost Benchmarks", "context",
               "Market Context", dataset="crew_costs", kind="dataset_table"),
    SectionDef("ctx_comparables", "Comparable Productions", "context",
               "Market Context", dataset="comparables", kind="dataset_table"),
]

SECTION_BY_KEY: dict[str, SectionDef] = {s.key: s for s in SECTION_LIBRARY}

# Standard product -> ordered section keys. Rebuilt on v2 signals.
PRODUCT_TEMPLATES: dict[str, list[str]] = {
    "camera_equipment": [
        "ctx_incentives", "sig_territory_considered", "sig_camera",
        "sig_format", "sig_genre", "sig_month",
    ],
    "production_services": [
        "ctx_crew_costs", "sig_crew", "sig_budget",
        "sig_territory_considered", "sig_format",
    ],
    "crew_casting": [
        "ctx_crew_costs", "sig_principal", "sig_supporting", "sig_extras",
        "sig_genre", "sig_completion",
    ],
    "strategic_trend": [
        "ctx_incentives", "ctx_festivals", "sig_territory_recommended",
        "sig_budget", "sig_genre", "sig_format", "sig_audience",
    ],
    "audience_intent": [  # new, specified not yet sold
        "sig_audience", "sig_audience_seg", "sig_language",
        "sig_genre", "sig_budget", "sig_territory_recommended",
    ],
    "territory_demand_index": [  # film-commission product
        "ctx_incentives", "sig_territory_considered",
        "sig_territory_recommended", "sig_completion", "sig_budget",
    ],
}


@dataclass
class CompositionResult:
    part_a: list[dict[str, Any]] = field(default_factory=list)
    part_b: list[dict[str, Any]] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)
    signal_count: int = 0
    insufficient_data: bool = False


class PackageService:
    """Assembles B2B packages from signals + curated datasets.

    Depends on an existing B2BService for signal loading + section rendering (so the
    privacy floors are the same code path as the standard products) and a dataset
    fetcher for Market Context.
    """

    def __init__(self, b2b_service: Any, dataset_fetcher: "DatasetFetcher | None" = None):
        self.b2b = b2b_service
        self.datasets = dataset_fetcher or DatasetFetcher(b2b_service.db)

    # --- library exposure for the admin UI ---
    @staticmethod
    def library() -> list[dict[str, Any]]:
        return [
            {
                "key": s.key, "title": s.title, "part": s.part, "group": s.group,
                "kind": s.kind, "note": s.note,
                "source": s.signal_field or s.dataset,
            }
            for s in SECTION_LIBRARY
        ]

    @staticmethod
    def product_template(product_type: str) -> list[str]:
        return PRODUCT_TEMPLATES.get(product_type, PRODUCT_TEMPLATES["strategic_trend"])

    # --- sufficiency preview: what WOULD render, before committing ---
    def preview(
        self, *, section_keys: list[str], period_start: date, period_end: date,
    ) -> dict[str, Any]:
        rows = self.b2b._load_signals(period_start, period_end)
        signal_count = len(rows)
        overall_ok = signal_count >= PRIVACY_MIN_OVERALL
        out_sections: list[dict[str, Any]] = []
        for key in section_keys:
            sec = SECTION_BY_KEY.get(key)
            if not sec:
                out_sections.append({"key": key, "status": "unknown", "renderable": False})
                continue
            if sec.part == "context":
                available = self.datasets.count(sec.dataset)
                out_sections.append({
                    "key": key, "title": sec.title, "part": "context",
                    "status": "ok" if available else "empty_dataset",
                    "renderable": bool(available),
                    "record_count": available,
                })
                continue
            # signal section: count distinct qualifying segments
            segs = self._segment_counts(rows, sec)
            qualifying = {k: v for k, v in segs.items() if v >= PRIVACY_MIN_SEGMENT}
            renderable = overall_ok and len(qualifying) > 0
            out_sections.append({
                "key": key, "title": sec.title, "part": "signals",
                "status": "ok" if renderable else ("below_threshold" if overall_ok else "insufficient_overall"),
                "renderable": renderable,
                "qualifying_segments": len(qualifying),
                "suppressed_segments": len(segs) - len(qualifying),
                "source": sec.signal_field,
            })
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "signal_count": signal_count,
            "overall_threshold_met": overall_ok,
            "thresholds": {
                "minimum_overall_records": PRIVACY_MIN_OVERALL,
                "minimum_segment_records": PRIVACY_MIN_SEGMENT,
            },
            "sections": out_sections,
            "renderable_sections": sum(1 for s in out_sections if s["renderable"]),
        }

    def _segment_counts(self, rows: list[dict[str, Any]], sec: SectionDef) -> dict[str, int]:
        from collections import Counter
        c: Counter[str] = Counter()
        for row in rows:
            val = row.get(sec.signal_field)
            if sec.kind == "month":
                val = str(val)[:7] if val else None
            values = val if (sec.flatten and isinstance(val, list)) else [val]
            for v in values:
                if v is None or v == "":
                    continue
                if sec.kind == "numeric_band":
                    c[self._band(v)] += 1
                else:
                    c[str(v)] += 1
        return dict(c)

    @staticmethod
    def _band(v: Any) -> str:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return "unknown"
        edges = [(10, "1–9"), (25, "10–24"), (50, "25–49"), (100, "50–99"), (10**9, "100+")]
        for hi, lbl in edges:
            if n < hi:
                return lbl
        return "100+"


class DatasetFetcher:
    """Reads curated Market Context datasets (Part A). Read-only."""

    _TABLES = {
        "incentives": "incentive_programs",
        "festivals": "festivals",
        "distributors": "distributors",
        "crew_costs": "crew_costs",
        "comparables": "comparable_productions",
    }

    def __init__(self, db: Any):
        self.db = db

    def count(self, dataset: str | None) -> int:
        table = self._TABLES.get(dataset or "")
        if not table:
            return 0
        try:
            res = self.db.table(table).select("id", count="exact", head=True).execute()
            return int(getattr(res, "count", 0) or 0)
        except Exception:
            return 0

    def fetch(self, dataset: str | None, limit: int = 100) -> list[dict[str, Any]]:
        table = self._TABLES.get(dataset or "")
        if not table:
            return []
        try:
            res = self.db.table(table).select("*").limit(limit).execute()
            return res.data or []
        except Exception:
            return []
