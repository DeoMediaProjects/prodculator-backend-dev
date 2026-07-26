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
from typing import Any

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
    # (ctx_crew_costs "Crew Cost Benchmarks" removed 2026-07 with the crew
    # day-rate dataset, owner-approved)
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
        "sig_crew", "sig_budget",
        "sig_territory_considered", "sig_format",
    ],
    "crew_casting": [
        "sig_principal", "sig_supporting", "sig_extras",
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

    # --- spec translation -------------------------------------------------
    @staticmethod
    def _spec_for(sec: SectionDef) -> dict[str, Any]:
        """SectionDef -> the spec dict B2BService._facts_from_specs consumes."""
        return {
            "kind": sec.kind,
            "key": sec.signal_field,
            "title": sec.title,
            "flatten": sec.flatten,
        }

    def _signal_counts(
        self, section_keys: list[str], rows: list[dict[str, Any]]
    ) -> dict[str, dict[str, int]]:
        """Raw per-section segment counts, computed by the SAME code that renders.

        Preview and generate share this so the two can never disagree about how
        a value is bucketed or whether a blank counts as a segment. Previously
        preview had its own counter with different numeric bands and it silently
        dropped empty values, so it could promise a section that then rendered
        differently (or vice versa).
        """
        defs = [
            SECTION_BY_KEY[key]
            for key in section_keys
            if key in SECTION_BY_KEY and SECTION_BY_KEY[key].part == "signals"
        ]
        if not defs:
            return {}
        facts = self.b2b._facts_from_specs([self._spec_for(sec) for sec in defs], rows)
        return {
            sec.key: (section.get("counts") or {})
            for sec, section in zip(defs, facts.get("sections") or [])
        }

    # --- sufficiency preview: what WOULD render, before committing ---
    def preview(
        self,
        *,
        section_keys: list[str],
        period_start: date,
        period_end: date,
        blocked_keys: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = self.b2b._load_signals(period_start, period_end)
        signal_count = len(rows)
        overall_ok = signal_count >= PRIVACY_MIN_OVERALL
        counts_by_key = self._signal_counts(section_keys, rows)
        blocked_keys = blocked_keys or {}
        out_sections: list[dict[str, Any]] = []
        for key in section_keys:
            sec = SECTION_BY_KEY.get(key)
            if not sec:
                out_sections.append({"key": key, "status": "unknown", "renderable": False})
                continue
            if key in blocked_keys:
                out_sections.append({
                    "key": key, "title": sec.title, "part": sec.part,
                    "status": "blocked_exclusive", "renderable": False,
                    "exclusivity": blocked_keys[key],
                })
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
            segs = counts_by_key.get(key, {})
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

    # --- generate: compose a deliverable package ---------------------------
    def compose(
        self,
        *,
        section_keys: list[str],
        period_start: date,
        period_end: date,
        title: str,
        client_name: str | None = None,
        dataset_limit: int = 25,
    ) -> dict[str, Any]:
        """Build deliverable metrics for an admin-composed package.

        Part B (signals) is routed through B2BService._facts_from_specs and
        _facts_to_metrics, so a bespoke report inherits the identical privacy
        floors as a standard product -- there is no bespoke suppression path to
        get wrong. Part A (curated market context) is volume-independent and is
        prepended.
        """
        known = [key for key in section_keys if key in SECTION_BY_KEY]
        signal_defs = [SECTION_BY_KEY[k] for k in known if SECTION_BY_KEY[k].part == "signals"]
        context_defs = [SECTION_BY_KEY[k] for k in known if SECTION_BY_KEY[k].part == "context"]

        rows = self.b2b._load_signals(period_start, period_end) if signal_defs else []
        facts = self.b2b._facts_from_specs([self._spec_for(s) for s in signal_defs], rows)
        metrics = self.b2b._facts_to_metrics(
            "bespoke",
            facts,
            period_start=period_start,
            period_end=period_end,
            title=title,
            extra={
                "client_name": client_name,
                "composed_section_keys": known,
                "unknown_section_keys": [k for k in section_keys if k not in SECTION_BY_KEY],
            },
        )

        # Privacy floors govern signal-derived output only. A context-only
        # package has no signals to protect, so it must not be treated as
        # insufficient just because the period happens to be quiet.
        if not signal_defs:
            metrics["insufficient_data"] = False
            metrics["sections"] = []

        if not metrics["insufficient_data"]:
            context_sections = [self._context_section(sec, dataset_limit) for sec in context_defs]
            metrics["sections"] = [s for s in context_sections if s] + metrics["sections"]

        return metrics

    def _context_section(self, sec: SectionDef, limit: int) -> dict[str, Any] | None:
        records = self.datasets.fetch(sec.dataset, limit=limit)
        if not records:
            return {
                "title": sec.title,
                "summary": f"No {sec.dataset} records are currently available.",
                "kind": "dataset",
                "columns": [],
                "records": [],
                "rows": [],
            }
        columns = self.datasets.display_columns(sec.dataset, records)
        return {
            "title": sec.title,
            "summary": (
                f"{len(records)} curated {sec.dataset} record(s). "
                "Market context is editorially maintained and volume-independent."
            ),
            "kind": "dataset",
            "columns": columns,
            "records": [
                {col: self._cell(record.get(col)) for col in columns} for record in records
            ],
            "rows": [],
        }

    @staticmethod
    def _cell(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, list):
            return ", ".join(str(v) for v in value if v is not None) or "—"
        if isinstance(value, dict):
            return ", ".join(f"{k}: {v}" for k, v in value.items()) or "—"
        return str(value)


class DatasetFetcher:
    """Reads curated Market Context datasets (Part A). Read-only."""

    _TABLES = {
        "incentives": "incentive_programs",
        # The festival table is `film_festivals`. This previously pointed at a
        # non-existent "festivals" table, and because count()/fetch() swallow
        # exceptions the Festival Calendar section silently reported
        # empty_dataset forever instead of failing loudly.
        "festivals": "film_festivals",
        "distributors": "distributors",
        "comparables": "comparable_productions",
    }

    # Preferred display columns per dataset, in report order. Intersected with
    # the columns actually present, so a schema change degrades to fewer columns
    # rather than raising or rendering blanks.
    _DISPLAY_COLUMNS = {
        "incentives": ["territory", "program", "rate", "cap", "status"],
        "festivals": ["name", "location", "submission_deadline", "tier", "year"],
        "distributors": ["name", "primary_market", "rights_type", "budget_tier_fit"],
        "comparables": ["title", "year", "primary_territory", "budget_usd", "incentive_used"],
    }

    _HIDDEN_COLUMNS = {"id", "created_at", "updated_at", "source_url", "filmfreeway_url"}

    def __init__(self, db: Any):
        self.db = db

    def display_columns(self, dataset: str | None, records: list[dict[str, Any]]) -> list[str]:
        if not records:
            return []
        present = set(records[0].keys())
        preferred = [c for c in self._DISPLAY_COLUMNS.get(dataset or "", []) if c in present]
        if preferred:
            return preferred
        # Unknown dataset or fully renamed schema: fall back to the first few
        # meaningful columns so the section still carries information.
        return [c for c in records[0] if c not in self._HIDDEN_COLUMNS][:5]

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
