import calendar
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import NoSuchTableError

from app.core.database_client import DatabaseClient
from app.core.territories import (
    Territory,
    resolve_territory,
    territory_to_iso as _build_territory_to_iso,
    iso_to_territory as _build_iso_to_territory,
)
from app.modules.fx.service import FXService
from app.modules.scripts.schemas import ScriptAnalysisResult
from app.modules.scripts.service import ScriptAnalysisService

logger = logging.getLogger(__name__)

# Derived from the canonical Territory enum — single source of truth.
_TERRITORY_TO_ISO: dict[str, str] = _build_territory_to_iso()
_ISO_TO_TERRITORY: dict[str, str] = _build_iso_to_territory()

MAX_ERROR_MESSAGE_CHARS = 500
MAX_STEP_CHARS = 80
MAX_META_TEXT_CHARS = 180
MAX_CHUNK_COUNT = 500
_ALLOWED_STEPS = {
    "init",
    "load_report_row",
    "email_processing_started",
    "download_script",
    "extract_script_text",
    "script_analysis",
    "production_analysis",
    "pdf_render",
    "pdf_generate",
    "pdf_upload",
    "persist_report",
    "email_report_ready",
}

_SECTION_CONFIDENCE_KEYS = (
    "locations",
    "budget",
    "productionScale",
    "equipment",
    "metadata",
    "challenges",
)


class ReportService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    # --- CRUD operations (unchanged) ---

    def create_report(
        self,
        user_id: str,
        script_title: str,
        report_type: str,
        script_file_path: str | None = None,
        request_metadata: dict | None = None,
    ) -> str:
        """Create a new report record, returns report ID."""
        payload = {
            "id": str(uuid4()),
            "user_id": user_id,
            "script_title": script_title,
            "script_file_path": script_file_path,
            "status": "processing",
            "report_type": report_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if request_metadata is not None:
            payload["request_metadata"] = request_metadata
        result = (
            self.supabase.table("reports")
            .insert(payload)
            .select("id")
            .single()
            .execute()
        )
        return result.data["id"]

    def complete_report(self, report_id: str, report_data: dict, pdf_url: str = "") -> None:
        """Mark report as completed with data."""
        self.supabase.table("reports").update(
            {
                "status": "completed",
                "report_data": report_data,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "pdf_url": pdf_url,
            }
        ).eq("id", report_id).execute()

    def update_pdf_url(self, report_id: str, pdf_url: str) -> None:
        """Update the PDF URL for a completed report."""
        self.supabase.table("reports").update({"pdf_url": pdf_url}).eq(
            "id", report_id
        ).execute()

    def fail_report(
        self,
        report_id: str,
        error_message: str,
        error_context: dict | None = None,
    ) -> None:
        """Mark report as failed."""
        report_data: dict = {"error": self._sanitize_error_message(error_message)}
        sanitized_context = self._sanitize_error_context(error_context)
        if sanitized_context:
            report_data["errorContext"] = sanitized_context
        self.supabase.table("reports").update(
            {"status": "failed", "report_data": report_data}
        ).eq("id", report_id).execute()

    @staticmethod
    def _sanitize_error_message(error_message: str) -> str:
        redacted = ReportService.redact_sensitive_text(str(error_message))
        return redacted[:MAX_ERROR_MESSAGE_CHARS]

    @staticmethod
    def _sanitize_error_context(error_context: dict | None) -> dict | None:
        if not isinstance(error_context, dict):
            return None

        out: dict[str, Any] = {}
        raw_step = str(error_context.get("step", "")).strip()
        if raw_step:
            step = ReportService.redact_sensitive_text(raw_step)[:MAX_STEP_CHARS]
            out["step"] = step if step in _ALLOWED_STEPS else "unknown"

        raw_meta = error_context.get("scriptAnalysisMeta")
        meta = ReportService._sanitize_script_analysis_meta(raw_meta)
        if meta:
            out["scriptAnalysisMeta"] = meta

        return out or None

    @staticmethod
    def _sanitize_script_analysis_meta(raw_meta: Any) -> dict | None:
        if not isinstance(raw_meta, dict):
            return None

        out: dict[str, Any] = {}
        mode = raw_meta.get("mode")
        if mode is not None:
            out["mode"] = ReportService.redact_sensitive_text(str(mode))[:MAX_META_TEXT_CHARS]

        for bool_key in ("fallbackUsed", "chunkedFailed", "fallbackToSinglePass"):
            if isinstance(raw_meta.get(bool_key), bool):
                out[bool_key] = raw_meta[bool_key]

        for text_key in ("reason", "chunkedError"):
            value = raw_meta.get(text_key)
            if value is not None:
                out[text_key] = ReportService.redact_sensitive_text(str(value))[:MAX_META_TEXT_CHARS]

        chunk_telemetry_raw = raw_meta.get("chunkTelemetry")
        if isinstance(chunk_telemetry_raw, dict):
            chunk_telemetry: dict[str, Any] = {}
            total = ReportService._coerce_int(chunk_telemetry_raw.get("totalChunks"))
            generated = ReportService._coerce_int(chunk_telemetry_raw.get("generatedChunks"))
            used = ReportService._coerce_int(chunk_telemetry_raw.get("usedChunks"))
            failed = ReportService._coerce_int(chunk_telemetry_raw.get("failedChunks"))
            dropped = ReportService._coerce_int(chunk_telemetry_raw.get("droppedChunks"))
            ratio = ReportService._coerce_float(chunk_telemetry_raw.get("successRatio"))

            if total is not None:
                chunk_telemetry["totalChunks"] = min(max(total, 0), MAX_CHUNK_COUNT)
            if generated is not None:
                chunk_telemetry["generatedChunks"] = min(max(generated, 0), MAX_CHUNK_COUNT)
            if used is not None:
                chunk_telemetry["usedChunks"] = min(max(used, 0), MAX_CHUNK_COUNT)
            if failed is not None:
                chunk_telemetry["failedChunks"] = min(max(failed, 0), MAX_CHUNK_COUNT)
            if dropped is not None:
                chunk_telemetry["droppedChunks"] = min(max(dropped, 0), MAX_CHUNK_COUNT)
            if ratio is not None:
                chunk_telemetry["successRatio"] = max(0.0, min(1.0, round(ratio, 4)))
            stop_reasons_raw = chunk_telemetry_raw.get("stopReasons")
            if isinstance(stop_reasons_raw, dict):
                stop_reasons: dict[str, int] = {}
                for key in ("max_tokens", "timeout", "rate_limit", "parse_error", "unknown"):
                    count = ReportService._coerce_int(stop_reasons_raw.get(key))
                    if count is not None and count > 0:
                        stop_reasons[key] = min(count, MAX_CHUNK_COUNT)
                if stop_reasons:
                    chunk_telemetry["stopReasons"] = stop_reasons

            if chunk_telemetry:
                out["chunkTelemetry"] = chunk_telemetry

        overall_confidence = ReportService._coerce_float(raw_meta.get("overallConfidence"))
        if overall_confidence is not None:
            out["overallConfidence"] = max(0.0, min(1.0, round(overall_confidence, 4)))

        section_conf_raw = raw_meta.get("sectionConfidence")
        if isinstance(section_conf_raw, dict):
            section_conf: dict[str, float] = {}
            for key in _SECTION_CONFIDENCE_KEYS:
                value = ReportService._coerce_float(section_conf_raw.get(key))
                if value is not None:
                    section_conf[key] = max(0.0, min(1.0, round(value, 4)))
            if section_conf:
                out["sectionConfidence"] = section_conf

        return out or None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _redact_sensitive_text(text: str) -> str:
        redacted = text
        patterns = [
            r"sk-[A-Za-z0-9\-_]{10,}",
            r"Bearer\s+[A-Za-z0-9\._\-]{8,}",
            r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{8,}",
            r"(?i)authorization\s*[:=]\s*['\"]?[A-Za-z0-9\._\-]{8,}",
            r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
        ]
        for pattern in patterns:
            redacted = re.sub(pattern, "[REDACTED]", redacted)
        return redacted

    @staticmethod
    def redact_sensitive_text(text: str) -> str:
        """Public helper for redacting sensitive-looking text in logs/payloads."""
        return ReportService._redact_sensitive_text(text)

    def get_report(self, report_id: str) -> dict | None:
        """Get a single report by ID."""
        result = (
            self.supabase.table("reports").select("*").eq("id", report_id).single().execute()
        )
        return result.data

    def get_report_by_share_token(self, share_token: str) -> dict | None:
        """Get report by public share token."""
        result = (
            self.supabase.table("reports")
            .select("*")
            .eq("share_token", share_token)
            .single()
            .execute()
        )
        return result.data

    def get_user_reports(self, user_id: str) -> list[dict]:
        """Get all reports for a user (excludes previews)."""
        result = (
            self.supabase.table("reports")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        reports = result.data or []
        return [r for r in reports if r.get("report_type") != "preview"]

    # --- New analysis report generation ---

    def _resolve_territories_hint(self, request_metadata: dict) -> list[str] | None:
        """Build the territory hint list for dataset loading from all relevant metadata fields.

        Priority order:
        1. If location_strategy == "domestic": restrict to home country + state_province only.
        2. If territories_considering is set: use those (+ state_province if provided).
        3. If only state_province is set: use country + state_province.
        4. Otherwise: None (load full dataset, no priority-boost).
        """
        location_strategy = (request_metadata.get("location_strategy") or "open").lower()
        raw_territories = request_metadata.get("territories_considering") or []
        country = request_metadata.get("country") or ""
        state_province = request_metadata.get("state_province") or ""

        # Strip "open to all" sentinel values
        strict_territories = [
            t for t in raw_territories if t.lower() not in ("open to all", "open")
        ]

        if location_strategy == "domestic":
            # Domestic: only the home territory (+ state for state-level incentives)
            hint: list[str] = []
            if country:
                hint.append(country)
            if state_province and state_province not in hint:
                hint.append(state_province)
            return hint or None

        # Open or international — start from user-selected territories
        hint = list(strict_territories)

        # Always add state_province to hint so state-level incentives get priority-boosted
        if state_province and state_province not in hint:
            hint.append(state_province)
            if country and country not in hint:
                hint.append(country)

        return hint or None

    def generate_preview_report(
        self,
        *,
        request_metadata: dict,
        script_service: ScriptAnalysisService,
    ) -> dict:
        """Generate a free preview report synchronously (no script, no DB row)."""
        datasets = self._load_analysis_datasets(
            territories_hint=self._resolve_territories_hint(request_metadata)
        )
        self._inject_derived_data(datasets, script_analysis=None, request_metadata=request_metadata)
        return script_service.generate_production_analysis(
            script_analysis=None,
            request_metadata=request_metadata,
            datasets=datasets,
            is_preview=True,
        )

    def generate_analysis_report(
        self,
        *,
        script_analysis: ScriptAnalysisResult,
        request_metadata: dict,
        report_id: str,
        script_service: ScriptAnalysisService,
        is_b2b: bool = False,
    ) -> dict:
        """Generate a full paid/b2b analysis report."""
        datasets = self._load_analysis_datasets(
            territories_hint=self._resolve_territories_hint(request_metadata)
        )
        self._inject_derived_data(datasets, script_analysis=script_analysis, request_metadata=request_metadata)
        report_data = script_service.generate_production_analysis(
            script_analysis=script_analysis,
            request_metadata=request_metadata,
            datasets=datasets,
            is_preview=False,
        )

        if is_b2b:
            report_data["productionIntelligence"] = self._build_production_intelligence()

        return report_data

    def _inject_derived_data(
        self,
        datasets: dict,
        *,
        script_analysis: ScriptAnalysisResult | None,
        request_metadata: dict,
    ) -> None:
        """Compute and inject derived signals into *datasets* in-place.

        These are used by both the AI prompt (via generate_production_analysis)
        and the post-processing validator (ReportValidator.validate).
        """
        # Shoot window
        shoot_months = self._compute_shoot_months(
            request_metadata.get("filming_start_date"),
            request_metadata.get("filming_duration"),
        )
        shoot_window = None
        if shoot_months:
            shoot_window = {
                "startDate": request_metadata.get("filming_start_date"),
                "durationWeeks": request_metadata.get("filming_duration"),
                "months": shoot_months,
                "monthNames": [calendar.month_abbr[m] for m in shoot_months],
                "season": self._classify_season(shoot_months),
            }
        datasets["_shoot_months"] = shoot_months
        datasets["_shoot_window"] = shoot_window

        # Scene exposure profile from script analysis
        ext_int_ratio: float | None = None
        if script_analysis is not None:
            challenges = getattr(script_analysis, "challenges", None)
            if challenges is not None:
                ext_int_ratio = getattr(challenges, "extIntRatio", None)
        datasets["_ext_int_ratio"] = ext_int_ratio

        # Producer eligibility inputs
        datasets["_producer_country"] = request_metadata.get("producer_country")
        datasets["_co_production_status"] = request_metadata.get("co_production_status")

        # v3: Production priority (needed by validator for score recalculation)
        datasets["_production_priority"] = request_metadata.get("production_priority", "full")

        # v3: Production format (needed by validator for format harmonisation)
        datasets["_production_format"] = request_metadata.get("format")

        # v3: Budget conversion to GBP
        budget_amount = request_metadata.get("budget_amount")
        budget_currency = request_metadata.get("budget_currency", "GBP")
        if budget_amount and budget_amount > 0:
            fx_service = FXService(self.supabase.settings)
            budget_data = fx_service.convert_budget(budget_amount, budget_currency, "GBP")
            datasets["_budget_gbp"] = budget_data
            datasets["_budget_amount"] = budget_amount
            datasets["_budget_currency"] = budget_currency

            # v3: Currency advantage scores for all territories
            territory_labels = set()
            for inc in datasets.get("incentives", []):
                t = inc.get("territory")
                if t:
                    territory_labels.add(t)
            # Also include territories from weather data
            for w in datasets.get("weather", []):
                t = w.get("territory")
                if t:
                    territory_labels.add(t)
            if territory_labels:
                datasets["_currency_advantage_scores"] = (
                    fx_service.compute_currency_advantage_batch(
                        budget_currency, list(territory_labels)
                    )
                )

            # v3: FX rates from budget currency → each territory's incentive
            # currency.  Used by the validator to convert budget amounts for
            # display in budget scenarios and territory deep-dives.
            from app.modules.fx.service import TERRITORY_CURRENCY
            target_currencies: set[str] = set()
            for t in territory_labels:
                tc = TERRITORY_CURRENCY.get(t)
                if tc and tc != budget_currency:
                    target_currencies.add(tc)
            fx_rates: dict[str, dict] = {}
            for tc in target_currencies:
                rate, rate_date = fx_service.get_rate(budget_currency, tc)
                fx_rates[tc] = {
                    "rate": rate,
                    "rate_date": rate_date.isoformat(),
                    "from_currency": budget_currency,
                }
            datasets["_fx_rates_from_budget"] = fx_rates

        # Pre-compute territory financials — authoritative monetary figures
        # that the AI should use verbatim instead of computing its own.
        self._pre_compute_territory_financials(datasets)

    def _pre_compute_territory_financials(self, datasets: dict) -> None:
        """Build datasets["_territory_financials"] with authoritative monetary figures.

        The AI copies these verbatim instead of doing its own rebate arithmetic.
        Uses the exact same calculation logic as ReportValidator._compute_corrected_rebate.
        """
        from app.modules.reports.validator import (
            ReportValidator,
            _index_incentives_by_territory,
            _best_incentive,
            _budget_to_display,
            _format_rate,
            _currency_symbol,
            _DEFAULT_ATL_PCT,
        )

        budget_gbp_data = datasets.get("_budget_gbp")
        if not isinstance(budget_gbp_data, dict):
            return
        budget_gbp = budget_gbp_data.get("converted")
        if not budget_gbp or budget_gbp <= 0:
            return

        budget_currency = datasets.get("_budget_currency", "GBP")
        budget_original_amount = datasets.get("_budget_amount")
        fx_rates_from_budget = datasets.get("_fx_rates_from_budget") or {}
        incentives = datasets.get("incentives", [])
        crew_costs = datasets.get("crew_costs", [])

        territory_incentives = _index_incentives_by_territory(incentives)
        budget_symbol = _currency_symbol(budget_currency)

        # Index crew costs by territory — full name, ISO code, and canonical label.
        # Crew rows may use ISO codes ("GB"), full names ("United Kingdom"), or
        # regional labels ("England").  Index all variants so lookups always hit.
        crew_by_territory: dict[str, list[dict]] = {}
        for row in crew_costs:
            for raw_key in (row.get("territory") or "", row.get("country") or ""):
                if not raw_key:
                    continue
                crew_by_territory.setdefault(raw_key, []).append(row)
                # Resolve to canonical label to handle ISO ↔ full-name mismatches
                t_obj = resolve_territory(raw_key)
                if t_obj:
                    canonical = t_obj.label
                    if canonical != raw_key:
                        crew_by_territory.setdefault(canonical, []).append(row)
                    if t_obj.parent and t_obj.parent.label != raw_key:
                        crew_by_territory.setdefault(t_obj.parent.label, []).append(row)

        territory_financials: dict[str, dict] = {}

        for territory, rows in territory_incentives.items():
            if not territory or not rows:
                continue
            best = _best_incentive(rows)
            corrected = ReportValidator._compute_corrected_rebate(
                best, budget_gbp, territory_incentives
            )
            if corrected is None:
                continue

            territory_currency = best.get("currency") or "GBP"

            def _disp(gbp_amount: float) -> tuple[float, str, str | None]:
                return _budget_to_display(
                    gbp_amount, territory_currency, budget_currency,
                    budget_original_amount, budget_gbp, fx_rates_from_budget,
                )

            d_total, sym, fx_note = _disp(budget_gbp)
            d_qs, _, _ = _disp(corrected["qualifying_spend_before_atl"])
            d_net_qs, _, _ = _disp(corrected["qualifying_spend"])
            d_gross_rebate, _, _ = _disp(corrected["gross_rebate"])
            d_net_rebate, _, _ = _disp(corrected["net_rebate"])
            d_net_budget = d_total - d_net_rebate

            atl_str = None
            atl_amount = corrected.get("atl_deduction_amount", 0)
            if atl_amount > 0:
                d_atl, _, _ = _disp(atl_amount)
                atl_str = f"{sym}{d_atl:,.0f}"

            rate_str = _format_rate(corrected["rate_gross"], corrected["rate_net"])
            qs_pct = corrected["qualifying_spend_pct"]

            programme_name = (
                corrected.get("switched_programme")
                or best.get("program_name")
                or best.get("program")
                or ""
            )

            # Build crew rate strings for this territory in budget currency
            t_crew_rows = crew_by_territory.get(territory, [])
            if not t_crew_rows:
                # Try ISO lookup
                from app.core.territories import territory_to_iso as _build_iso
                iso_map = _build_iso()
                iso = iso_map.get(territory, "")
                if iso:
                    t_crew_rows = crew_by_territory.get(iso, [])

            crew_rates: dict[str, str] = {}
            for crew_row in t_crew_rows:
                role = crew_row.get("role_category") or crew_row.get("role") or ""
                if not role:
                    continue
                union_gbp = crew_row.get("union_rate_gbp")
                non_union_gbp = crew_row.get("non_union_rate_gbp")
                if union_gbp is None and non_union_gbp is None:
                    continue

                def _crew_disp(gbp_val: float) -> str:
                    d, s, _ = _budget_to_display(
                        gbp_val, budget_currency, budget_currency,
                        budget_original_amount, budget_gbp, fx_rates_from_budget,
                    )
                    return f"{s}{d:,.0f}"

                if union_gbp and non_union_gbp:
                    lo, hi = sorted([union_gbp, non_union_gbp])
                    rate_text = f"{_crew_disp(lo)}–{_crew_disp(hi)}/day"
                elif union_gbp:
                    rate_text = f"{_crew_disp(union_gbp)}/day"
                else:
                    rate_text = f"{_crew_disp(non_union_gbp)}/day"  # type: ignore[arg-type]

                if role not in crew_rates:
                    crew_rates[role] = rate_text

            territory_financials[territory] = {
                "currency": territory_currency,
                "currency_symbol": sym,
                "total_budget": f"{sym}{d_total:,.0f}",
                "qualifying_spend_pct": f"{qs_pct:.0f}%",
                "qualifying_spend": f"{sym}{d_qs:,.0f}",
                "atl_deduction": atl_str,
                "atl_pct": f"{_DEFAULT_ATL_PCT:.0%}" if atl_amount > 0 else None,
                "net_qualifying_spend": f"{sym}{d_net_qs:,.0f}",
                "rate": rate_str or "N/A",
                "rate_gross": f"{corrected['rate_gross']:g}%",
                "rate_net": f"{corrected['rate_net']:g}%" if corrected.get("rate_net") else None,
                "gross_rebate": f"{sym}{d_gross_rebate:,.0f}",
                "net_rebate": f"{sym}{d_net_rebate:,.0f}",
                "net_budget": f"{sym}{d_net_budget:,.0f}",
                "headline_net_budget": f"approximately {sym}{d_net_budget:,.0f}",
                "programme": programme_name,
                "programme_note": corrected.get("programme_note"),
                "fx_note": fx_note,
                "crew_rates": crew_rates,
                # Budget-currency equivalents for context in prompt
                "budget_currency": budget_currency,
                "budget_symbol": budget_symbol,
                "budget_display": f"{budget_symbol}{budget_original_amount:,.0f}" if budget_original_amount else None,
            }

        datasets["_territory_financials"] = territory_financials
        logger.info(
            "Pre-computed territory financials: territories=%s",
            list(territory_financials.keys()),
        )

    # --- Dataset loading ---

    def _safe_query(self, table_name: str, builder) -> list[dict]:
        try:
            logger.debug("Loading analysis dataset table=%s", table_name)
            query = self.supabase.table(table_name)
            result = builder(query).execute()
            rows = result.data or []
            logger.debug("Loaded analysis dataset table=%s rows=%s", table_name, len(rows))
            return rows
        except NoSuchTableError:
            logger.warning("Optional dataset table missing: %s", table_name)
            return []

    def _compute_shoot_months(
        self,
        filming_start_date: str | None,
        filming_duration: int | None,
    ) -> list[int] | None:
        """Return sorted list of month numbers (1-12) the shoot spans.

        Returns None if *filming_start_date* is absent or unparseable.
        """
        if not filming_start_date:
            return None
        try:
            start = date.fromisoformat(str(filming_start_date).strip()[:10])
        except ValueError:
            return None

        duration_weeks = filming_duration if filming_duration and filming_duration > 0 else 4
        end = start + timedelta(weeks=duration_weeks)

        months: set[int] = set()
        current = start
        while current <= end:
            months.add(current.month)
            current += timedelta(days=15)
        months.add(end.month)
        return sorted(months)

    @staticmethod
    def _classify_season(months: list[int]) -> str:
        """Classify shoot window as 'summer', 'winter', or 'mixed' (Northern Hemisphere)."""
        summer = {5, 6, 7, 8, 9}
        winter = {11, 12, 1, 2, 3}
        month_set = set(months)
        if month_set and month_set.issubset(summer):
            return "summer"
        if month_set and month_set.issubset(winter):
            return "winter"
        return "mixed"


    def _load_analysis_datasets(self, territories_hint: list[str] | None = None) -> dict:
        """Load admin-managed datasets for AI prompt injection."""

        # ── Normalise territory hints via the canonical enum ────────────
        # The frontend may send short forms like "UK", "USA", "Canada" —
        # resolve them to canonical labels ("United Kingdom", etc.) so DB
        # matching works against the canonical territory strings we store.
        normalised_hint: list[str] | None = None
        if territories_hint:
            seen: set[str] = set()
            normalised_hint = []
            for raw in territories_hint:
                t = resolve_territory(raw)
                label = t.label if t else raw
                if label not in seen:
                    seen.add(label)
                    normalised_hint.append(label)
                    # Also include parent label (e.g. "Scotland" → "United Kingdom")
                    if t and t.parent and t.parent.label not in seen:
                        seen.add(t.parent.label)
                        normalised_hint.append(t.parent.label)

        logger.info(
            "Loading analysis datasets: raw_hint=%s normalised_hint=%s",
            territories_hint or [],
            normalised_hint or [],
        )

        # Active incentive programs — include NULL status (legacy rows) too
        all_incentives = self._safe_query(
            "incentive_programs",
            lambda q: q.select("*"),
        )
        # Filter to active + legacy (NULL status treated as active)
        all_incentives = [
            i for i in all_incentives
            if (i.get("status") or "").lower() in ("active", "") or i.get("status") is None
        ]
        if normalised_hint:
            hint_set = set(normalised_hint)
            # Also build ISO set for matching crew_costs.country-style fields
            hint_iso = {_TERRITORY_TO_ISO.get(t, t) for t in normalised_hint}
            # Priority-boost: hinted territories come first, remainder appended after.
            # Never hard-exclude — every active programme remains available so the
            # AI (and validator) always have the full dataset to draw from.
            hinted = [
                i for i in all_incentives
                if i.get("territory") in hint_set or i.get("territory") in hint_iso
            ]
            rest = [
                i for i in all_incentives
                if i.get("territory") not in hint_set and i.get("territory") not in hint_iso
            ]
            incentives = hinted + rest
        else:
            incentives = all_incentives

        # Annotate incentives with data freshness (days since last_verified_at)
        today = date.today()
        for inc in incentives:
            lv = inc.get("last_verified_at")
            if lv:
                try:
                    verified_date = date.fromisoformat(str(lv)[:10])
                    inc["data_freshness_days"] = (today - verified_date).days
                except ValueError:
                    inc["data_freshness_days"] = None
            else:
                inc["data_freshness_days"] = None

        # Crew & cast costs — load with FX enrichment
        all_rates = self._safe_query("crew_costs", lambda q: q.select("*"))
        if normalised_hint:
            # Convert full territory names to ISO codes for matching
            hint_iso = {_TERRITORY_TO_ISO.get(t, t) for t in normalised_hint}
            hint_full = set(normalised_hint)
            # Priority-boost: hinted territories first, rest appended.
            hinted = [
                c for c in all_rates
                if c.get("country") in hint_iso or c.get("territory") in hint_full
            ]
            rest = [
                c for c in all_rates
                if c.get("country") not in hint_iso and c.get("territory") not in hint_full
            ]
            all_rates = hinted + rest

        # Split into crew and cast by role_category prefix
        crew_costs = [
            c for c in all_rates
            if not (c.get("role_category") or "").startswith("CAST-")
        ]
        cast_costs = [
            c for c in all_rates
            if (c.get("role_category") or "").startswith("CAST-")
        ]

        # FX-enrich each row: add union_rate_gbp, non_union_rate_gbp, fx_rate, fx_date
        crew_costs = self._fx_enrich_crew_costs(crew_costs)
        cast_costs = self._fx_enrich_crew_costs(cast_costs)

        # Comparable productions (small dataset, load all)
        comparables = self._safe_query("comparable_productions", lambda q: q.select("*"))

        # Open grants
        grants = self._safe_query(
            "grant_opportunities",
            lambda q: q.select("*").in_("status", ["open", "opening_soon", "closing_soon"]),
        )

        # Upcoming festivals
        all_festivals = self._safe_query("film_festivals", lambda q: q.select("*"))
        festivals = [f for f in all_festivals if self._is_open_or_upcoming_festival(f)]
        festivals.sort(key=self._festival_sort_key)
        # Territory weather data (for shoot-date-aware risk scoring)
        weather_data = self._safe_query("territory_weather", lambda q: q.select("*"))

        # Build stacking map: group_id → [program_names]
        stacking_map: dict[str, list[str]] = {}
        for inc in incentives:
            group = inc.get("stacking_group")
            if group:
                stacking_map.setdefault(group, []).append(
                    inc.get("program_name") or inc.get("program") or ""
                )

        logger.info(
            "Loaded analysis datasets counts: incentives=%s crew_costs=%s cast_costs=%s comparables=%s grants=%s festivals=%s weather=%s",
            len(incentives),
            len(crew_costs),
            len(cast_costs),
            len(comparables),
            len(grants),
            len(festivals),
            len(weather_data),
        )

        return {
            "incentives": incentives,
            "crew_costs": crew_costs,
            "cast_costs": cast_costs,
            "comparables": comparables,
            "grants": grants,
            "festivals": festivals,
            "weather": weather_data,
            "stacking_map": stacking_map,
        }

    def _fx_enrich_crew_costs(self, crew_costs: list[dict]) -> list[dict]:
        """Add union_rate_gbp, non_union_rate_gbp, fx_rate, fx_date to each crew/cast cost row."""
        if not crew_costs:
            return crew_costs
        # Collect unique non-GBP currencies (check both new and legacy field names)
        currencies = set()
        for c in crew_costs:
            ccy = c.get("rate_currency") or c.get("currency") or ""
            if ccy and ccy.upper() != "GBP":
                currencies.add(ccy.upper())

        if not currencies:
            # All GBP — convert pence to pounds and annotate
            for c in crew_costs:
                union_cents = c.get("union_rate_cents")
                non_union_cents = c.get("non_union_rate_cents")
                c["union_rate_gbp"] = round(union_cents / 100) if union_cents else None
                c["non_union_rate_gbp"] = round(non_union_cents / 100) if non_union_cents else None
                c["fx_rate"] = 1.0
                c["fx_date"] = date.today().isoformat()
            return crew_costs

        try:
            fx = FXService(self.supabase.settings)
            rates = fx.get_rates_batch("GBP", list(currencies))
        except Exception:
            logger.warning("FX enrichment failed — crew costs will lack GBP conversions", exc_info=True)
            rates = {}

        for c in crew_costs:
            currency = (c.get("rate_currency") or c.get("currency") or "").upper()
            if currency == "GBP":
                union_cents = c.get("union_rate_cents")
                non_union_cents = c.get("non_union_rate_cents")
                c["union_rate_gbp"] = round(union_cents / 100) if union_cents else None
                c["non_union_rate_gbp"] = round(non_union_cents / 100) if non_union_cents else None
                c["fx_rate"] = 1.0
                c["fx_date"] = date.today().isoformat()
            elif currency in rates:
                rate, fx_date = rates[currency]
                union_cents = c.get("union_rate_cents")
                non_union_cents = c.get("non_union_rate_cents")
                # Convert from local currency cents to GBP pounds: ÷100 for cents→units, ÷rate for FX
                c["union_rate_gbp"] = round(union_cents / 100 / rate) if union_cents else None
                c["non_union_rate_gbp"] = round(non_union_cents / 100 / rate) if non_union_cents else None
                c["fx_rate"] = round(rate, 4)
                c["fx_date"] = fx_date.isoformat() if hasattr(fx_date, "isoformat") else str(fx_date)
            else:
                c["union_rate_gbp"] = None
                c["non_union_rate_gbp"] = None
                c["fx_rate"] = None
                c["fx_date"] = None
        return crew_costs

    @staticmethod
    def _parse_iso_date(value: object) -> date | None:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None

    def _next_known_festival_deadline(self, festival: dict) -> date | None:
        today = date.today()
        candidates: list[date] = []

        submission_deadline = self._parse_iso_date(festival.get("submission_deadline"))
        if submission_deadline:
            candidates.append(submission_deadline)

        deadlines = festival.get("deadlines")
        if isinstance(deadlines, list):
            for deadline in deadlines:
                if not isinstance(deadline, dict):
                    continue
                parsed = self._parse_iso_date(deadline.get("date"))
                if parsed:
                    candidates.append(parsed)

        if not candidates:
            return None

        future = [d for d in candidates if d >= today]
        if future:
            return min(future)
        return min(candidates)

    def _is_open_or_upcoming_festival(self, festival: dict) -> bool:
        status = str(festival.get("status") or festival.get("current_status") or "").strip().lower()
        if status:
            closed_statuses = {"closed", "completed", "archived"}
            if status in closed_statuses:
                return False

            open_like_statuses = {
                "open",
                "upcoming",
                "early-bird-open",
                "regular-open",
                "late-open",
                "opening_soon",
            }
            if status in open_like_statuses:
                return True

        next_deadline = self._next_known_festival_deadline(festival)
        if next_deadline:
            return next_deadline >= date.today()
        return True

    def _festival_sort_key(self, festival: dict) -> tuple[bool, date, str]:
        next_deadline = self._next_known_festival_deadline(festival)
        return (
            next_deadline is None,
            next_deadline or date.max,
            str(festival.get("name") or ""),
        )

    # --- B2B production intelligence (kept for backward compatibility) ---

    def _build_production_intelligence(self) -> dict:
        return {
            "marketTrends": {
                "cameraEquipmentDemand": [
                    {"equipment": "ARRI", "demand": "High", "trend": "+12% QoQ"},
                    {"equipment": "RED", "demand": "Medium", "trend": "-5% QoQ"},
                ],
                "crewAvailability": [
                    {"territory": "British Columbia", "availability": "Good", "rate": "Stable"},
                    {"territory": "Georgia (USA)", "availability": "Excellent", "rate": "Rising +8%"},
                ],
                "territoryDemand": [
                    {"territory": "Malta", "demandLevel": "High", "forecast": "Increasing"},
                    {"territory": "UK", "demandLevel": "Very High", "forecast": "Stable"},
                ],
            },
            "competitiveAnalysis": {
                "similarProjectsInProduction": 5,
                "territoryCompetition": "Moderate competition for crew in peak season",
                "recommendations": [
                    "Book key crew members early",
                    "Consider off-season filming for better rates",
                ],
            },
            "riskAssessment": {
                "incentiveStability": [
                    {"territory": "Georgia (USA)", "risk": "Low", "note": "Program well-established"},
                    {"territory": "South Africa", "risk": "Medium", "note": "Payment delays reported"},
                ],
                "crewCostVolatility": [
                    {"territory": "British Columbia", "volatility": "Low"},
                    {"territory": "California (USA)", "volatility": "Medium-High"},
                ],
                "overallRiskScore": 35,
            },
        }

    # --- Deprecated legacy methods (kept for backward compatibility) ---

    def generate_b2c_report(
        self, script_title: str, analysis: ScriptAnalysisResult, report_id: str
    ) -> dict:
        """DEPRECATED: Use generate_analysis_report() instead."""
        incentives_result = (
            self.supabase.table("incentive_programs")
            .select("*")
            .eq("status", "active")
            .execute()
        )
        all_incentives = incentives_result.data or []
        matched = self._match_territories(analysis, all_incentives)
        territory_analysis = []
        for territory in matched:
            crew_result = (
                self.supabase.table("crew_costs")
                .select("*")
                .eq("territory", territory["name"])
                .execute()
            )
            crew_costs = crew_result.data or []
            incentives = [i for i in all_incentives if i["territory"] == territory["name"]]
            ta = self._build_territory_analysis(territory, incentives, crew_costs, analysis)
            territory_analysis.append(ta)
        territory_analysis.sort(key=lambda t: t["overallScore"], reverse=True)
        comparables = self._find_comparables(analysis)
        grants = self._find_grants(analysis, territory_analysis)
        festivals = self._recommend_festivals(analysis)
        summary = self._build_summary(territory_analysis, analysis, comparables)
        return {
            "reportId": report_id,
            "scriptTitle": script_title,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "executiveSummary": summary,
            "territoryAnalysis": territory_analysis[:5],
            "comparableProductions": comparables,
            "grantOpportunities": grants,
            "festivalRecommendations": festivals,
            "productionDetails": {
                "format": analysis.metadata.format,
                "genres": analysis.metadata.genres,
                "estimatedShootingDays": analysis.productionScale.estimatedShootingDays,
                "crewSize": analysis.productionScale.crewSize,
                "castSize": analysis.productionScale.principalCast,
                "vfxRequirements": analysis.equipment.vfxRequirements,
                "specialRequirements": analysis.equipment.specialEquipment,
            },
        }

    def generate_b2b_report(
        self,
        script_title: str,
        analysis: ScriptAnalysisResult,
        report_id: str,
        client_id: str | None = None,
    ) -> dict:
        """DEPRECATED: Use generate_analysis_report(is_b2b=True) instead."""
        report = self.generate_b2c_report(script_title, analysis, report_id)
        report["productionIntelligence"] = self._build_production_intelligence()
        if client_id:
            client_result = (
                self.supabase.table("b2b_clients")
                .select("company_name, custom_branding")
                .eq("id", client_id)
                .single()
                .execute()
            )
            if client_result.data and client_result.data.get("custom_branding"):
                report["branding"] = {
                    "companyName": client_result.data["company_name"],
                    "customColors": True,
                }
        return report

    # --- Legacy private helpers (kept for deprecated methods) ---

    def _match_territories(self, analysis: ScriptAnalysisResult, incentives: list[dict]):
        territories: dict[str, dict] = {}
        for loc in analysis.locations:
            t = loc.territory
            if t not in territories:
                territories[t] = {"score": 0, "reasons": []}
            if loc.isMainLocation:
                territories[t]["score"] += 50
                territories[t]["reasons"].append(f"Main filming location: {loc.name}")
            else:
                territories[t]["score"] += loc.frequency * 5
                territories[t]["reasons"].append(f"{loc.frequency} scenes set in {loc.name}")
        for inc in incentives[:3]:
            if (inc.get("rate_min") or 0) >= 25 and inc["territory"] not in territories:
                territories[inc["territory"]] = {
                    "score": 20,
                    "reasons": ["High tax incentive available"],
                }
        return sorted(
            [{"name": k, "matchScore": v["score"], "reasons": v["reasons"]} for k, v in territories.items()],
            key=lambda x: x["matchScore"],
            reverse=True,
        )

    def _build_territory_analysis(self, territory, incentives, crew_costs, analysis):
        budget_min = analysis.budgetEstimate.minUSD
        incentive_details = []
        for inc in incentives:
            rate = inc.get("rate_max") or inc.get("rate_min") or 0
            rebate = int(budget_min * (rate / 100))
            rate_str = f"{inc.get('rate_min', rate)}"
            if inc.get("rate_max") and inc.get("rate_min") != inc.get("rate_max"):
                rate_str += f"-{inc['rate_max']}"
            rate_str += "%"
            cap = f"${inc['cap_amount'] // 100:,}" if inc.get("cap_amount") else "Uncapped"
            incentive_details.append(
                {"programName": inc["program_name"], "rate": rate_str, "cap": cap, "potentialRebateUSD": rebate}
            )
        shooting_days = analysis.productionScale.estimatedShootingDays
        daily = sum((c.get("union_rate_cents") or c.get("day_rate_cents") or 0) for c in crew_costs) / 100
        weekly = sum((c.get("non_union_rate_cents") or c.get("week_rate_cents") or 0) for c in crew_costs) / 100
        weeks = max(1, (shooting_days + 4) // 5)
        total = weekly * weeks
        breakdown = [
            {
                "role": c["role"],
                "dayRate": (c.get("union_rate_cents") or c.get("day_rate_cents") or 0) / 100,
                "weekRate": (c.get("non_union_rate_cents") or c.get("week_rate_cents") or 0) / 100,
            }
            for c in crew_costs
        ]
        crew_estimate = {
            "dailyTotal": daily,
            "weeklyTotal": weekly,
            "totalForProduction": total,
            "currency": crew_costs[0]["currency"] if crew_costs else "USD",
            "breakdown": breakdown,
        }
        inc_score = min((incentive_details[0]["potentialRebateUSD"] / 1_000_000 * 10) if incentive_details else 0, 40)
        crew_score = max(40 - (total / 1_000_000 * 5), 0)
        loc_score = territory["matchScore"] / 100 * 20
        overall = min(round(inc_score + crew_score + loc_score), 100)
        pros = []
        if incentive_details and incentive_details[0]["potentialRebateUSD"] > 500_000:
            pros.append(f"Strong incentive: {incentive_details[0]['rate']} rebate available")
        if total < 1_000_000:
            pros.append("Competitive crew costs")
        if territory["matchScore"] > 50:
            pros.append("Strong location match for script requirements")
        if not pros:
            pros = ["Viable filming location"]
        cons = []
        if not incentive_details:
            cons.append("No incentive programs available")
        if any(i["cap"] != "Uncapped" for i in incentive_details):
            cons.append("Incentive program has caps")
        return {
            "territory": territory["name"],
            "country": incentives[0]["country"] if incentives else "Unknown",
            "overallScore": overall,
            "incentives": incentive_details,
            "estimatedCrewCosts": crew_estimate,
            "locationMatch": {"score": territory["matchScore"], "reasons": territory["reasons"]},
            "pros": pros,
            "cons": cons,
        }

    def _find_comparables(self, analysis: ScriptAnalysisResult) -> list[dict]:
        result = self.supabase.table("comparable_productions").select("*").execute()
        comparables = result.data or []
        matched = []
        for comp in comparables:
            genre_match = any(g in (comp.get("genre") or []) for g in analysis.metadata.genres)
            budget_match = comp.get("budget_usd") and (
                comp["budget_usd"] >= analysis.budgetEstimate.minUSD * 0.5
                and comp["budget_usd"] <= analysis.budgetEstimate.maxUSD * 2
            )
            score = (50 if genre_match else 0) + (30 if budget_match else 0)
            if score > 0:
                matched.append({
                    "title": comp["title"],
                    "year": comp.get("year", 0),
                    "budget": f"${comp['budget_usd'] / 100 / 1_000_000:.1f}M" if comp.get("budget_usd") else "N/A",
                    "territory": comp.get("primary_territory", ""),
                    "incentiveUsed": comp.get("incentive_used", "Unknown"),
                    "genres": comp.get("genre", []),
                    "relevanceScore": score,
                })
        matched.sort(key=lambda x: x["relevanceScore"], reverse=True)
        return matched[:5]

    def _find_grants(self, analysis: ScriptAnalysisResult, territory_analysis: list[dict]) -> list[dict]:
        result = (
            self.supabase.table("grant_opportunities")
            .select("*")
            .in_("status", ["open", "opening_soon", "closing_soon"])
            .execute()
        )
        grants = result.data or []
        top_territories = [t["territory"] for t in territory_analysis[:3]]
        matched = [
            {
                "title": g["title"],
                "organization": g.get("organization", ""),
                "amount": f"Up to ${g['amount_max'] / 100 / 1_000_000:.1f}M" if g.get("amount_max") else "Varies",
                "deadline": g.get("deadline", "Rolling"),
                "territory": g.get("territory", ""),
                "matchScore": 70,
            }
            for g in grants
            if g.get("territory") in top_territories
        ]
        return matched[:5]

    def _recommend_festivals(self, analysis: ScriptAnalysisResult) -> list[dict]:
        result = (
            self.supabase.table("film_festivals")
            .select("*")
            .in_("status", ["upcoming", "open"])
            .order("submission_deadline", desc=False)
            .execute()
        )
        festivals = result.data or []
        return [
            {
                "name": f["name"],
                "location": f.get("location", ""),
                "deadline": f.get("submission_deadline", "TBA"),
                "tier": f.get("prestige_tier", "Unknown"),
                "submissionFees": f"${f['submission_fee_min'] / 100}+" if f.get("submission_fee_min") else "Varies",
                "matchScore": 70,
            }
            for f in festivals[:8]
        ]

    def _build_summary(self, territory_analysis, analysis, comparables):
        top = territory_analysis[0] if territory_analysis else None
        top_inc = top["incentives"][0] if top and top["incentives"] else None
        return {
            "recommendedTerritories": [t["territory"] for t in territory_analysis[:3]],
            "estimatedBudgetRange": f"${analysis.budgetEstimate.minUSD / 1_000_000:.1f}M - ${analysis.budgetEstimate.maxUSD / 1_000_000:.1f}M",
            "topIncentiveOpportunity": {
                "territory": top["territory"] if top else "N/A",
                "programName": top_inc["programName"] if top_inc else "N/A",
                "potentialRebate": top_inc["potentialRebateUSD"] if top_inc else 0,
                "rate": top_inc["rate"] if top_inc else "N/A",
            },
            "keyInsights": [
                f"{len(territory_analysis)} viable filming territories identified",
                f"{len(comparables)} comparable productions analyzed",
                f"Estimated {analysis.productionScale.estimatedShootingDays} shooting days required",
            ],
        }
