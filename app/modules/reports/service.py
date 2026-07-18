import calendar
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import NoSuchTableError

from app.core.config import get_settings
from app.core.database_client import DatabaseClient
from app.core.territories import (
    resolve_territory,
    territory_to_iso as _build_territory_to_iso,
    iso_to_territory as _build_iso_to_territory,
)
from app.modules.b2b.signal_normalise import (
    canonical_format,
    canonical_genres,
    gbp_band,
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

_CREW_SCALE_TO_COUNT = {
    "small": 25,
    "medium": 60,
    "large": 120,
    "extra_large": 220,
}

_PRINCIPAL_SCALE_TO_COUNT = {
    "small": 3,
    "medium": 6,
    "large": 12,
    "extra_large": 20,
}

_SUPPORTING_SCALE_TO_COUNT = {
    "small": 8,
    "medium": 18,
    "large": 35,
    "extra_large": 60,
}

_EXTRAS_SCALE_TO_COUNT = {
    "small": 25,
    "medium": 80,
    "large": 200,
    "extra_large": 500,
}

# NOTE (R-1): budget banding for B2B signals is now FX-normalised to GBP via
# app.modules.b2b.signal_normalise.gbp_band. The old raw-amount USD buckets below
# are retained ONLY as a fallback for legacy report_data that already carries a
# pre-computed band string; they are never applied to a raw foreign-currency amount.
_BUDGET_BUCKETS_USD = (
    (500_000, "micro"),
    (5_000_000, "low"),
    (30_000_000, "medium"),
    (100_000_000, "high"),
    (float("inf"), "tentpole"),
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

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else None
        if isinstance(value, list):
            out = [str(item).strip() for item in value if str(item).strip()]
            return out or None
        return None

    @staticmethod
    def _derive_submission_date(value: Any) -> str:
        if value is None:
            return date.today().isoformat()
        raw = str(value).strip()
        if not raw:
            return date.today().isoformat()
        return raw[:10]

    @staticmethod
    def _scale_label_to_count(value: Any, mapping: dict[str, int]) -> int | None:
        if value is None:
            return None
        key = str(value).strip().lower()
        if not key:
            return None
        return mapping.get(key)

    @staticmethod
    def _budget_amount_to_range(value: Any) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None
        for upper_bound, label in _BUDGET_BUCKETS_USD:
            if amount < upper_bound:
                return label
        return None

    def _build_production_signal_payload(
        self,
        *,
        report_id: str,
        report_row: dict[str, Any],
        request_metadata: dict[str, Any],
        script_analysis: ScriptAnalysisResult | None,
    ) -> dict[str, Any]:
        report_data = report_row.get("report_data")
        production_details: dict[str, Any] = {}
        if isinstance(report_data, dict):
            maybe_production_details = report_data.get("productionDetails")
            if isinstance(maybe_production_details, dict):
                production_details = maybe_production_details

        analysis_camera: Any | None = None
        analysis_genres: Any | None = None
        analysis_format: str | None = None
        analysis_budget_range: str | None = None
        scale = None
        if script_analysis is not None:
            analysis_camera = script_analysis.equipment.cameraEquipment
            analysis_genres = script_analysis.metadata.genres
            analysis_format = script_analysis.metadata.format
            analysis_budget_range = script_analysis.budgetEstimate.range
            scale = script_analysis.productionScale

        crew_size = self._coerce_int(request_metadata.get("crew_size"))
        principal_cast = self._coerce_int(request_metadata.get("principal_cast"))
        supporting_cast = self._coerce_int(request_metadata.get("supporting_cast"))
        background_extras = self._coerce_int(request_metadata.get("background_extras"))

        if crew_size is None and scale is not None:
            crew_size = self._scale_label_to_count(getattr(scale, "crewSize", None), _CREW_SCALE_TO_COUNT)
        if principal_cast is None and scale is not None:
            principal_cast = self._scale_label_to_count(
                getattr(scale, "principalCast", None),
                _PRINCIPAL_SCALE_TO_COUNT,
            )
        if supporting_cast is None and scale is not None:
            supporting_cast = self._scale_label_to_count(
                getattr(scale, "supportingCast", None),
                _SUPPORTING_SCALE_TO_COUNT,
            )
        if background_extras is None and scale is not None:
            background_extras = self._scale_label_to_count(
                getattr(scale, "backgroundExtras", None),
                _EXTRAS_SCALE_TO_COUNT,
            )
        if crew_size is None:
            crew_size = self._scale_label_to_count(production_details.get("crewSize"), _CREW_SCALE_TO_COUNT)
        if principal_cast is None:
            principal_cast = self._scale_label_to_count(
                production_details.get("castSize"),
                _PRINCIPAL_SCALE_TO_COUNT,
            )

        camera_equipment = self._coerce_string_list(request_metadata.get("camera_equipment"))
        if camera_equipment is None:
            camera_equipment = self._coerce_string_list(analysis_camera)

        genres = self._coerce_string_list(request_metadata.get("genre"))
        if genres is None:
            genres = self._coerce_string_list(analysis_genres)
        if genres is None:
            genres = self._coerce_string_list(production_details.get("genres"))
        genres = canonical_genres(genres)

        # --- Budget: FX-normalise to GBP before banding (R-1) ---
        budget_range, budget_amount_gbp, budget_currency, fx_rate_date = self._normalise_budget(
            request_metadata=request_metadata,
            report_data=report_data if isinstance(report_data, dict) else {},
            analysis_budget_range=analysis_budget_range,
        )

        # --- Territory semantics: three distinct fields (R-2) ---
        home_country = request_metadata.get("production_country") or request_metadata.get("country")
        territories_considered = self._coerce_string_list(
            request_metadata.get("territories_considering")
            or request_metadata.get("territories_considered")
        )
        territories_recommended = self._extract_recommended_territories(report_data)

        # --- Format: canonical value only (R-10) ---
        fmt = canonical_format(
            request_metadata.get("format") or analysis_format or production_details.get("format")
        )

        # --- Audience (stored, never scored) ---
        target_audience = self._coerce_string_list(request_metadata.get("target_audience"))
        audience_segments = self._coerce_string_list(request_metadata.get("audience_segments"))
        primary_languages = self._coerce_string_list(
            request_metadata.get("primary_languages") or request_metadata.get("language")
        )
        # Intake sends yes/no/undecided; the signal stores a tri-state bool
        # (None = undecided/unanswered). bool("no") would be True — map strings.
        co_pro_raw = request_metadata.get("co_production_interest")
        if isinstance(co_pro_raw, str):
            co_pro = {"yes": True, "no": False}.get(co_pro_raw.strip().lower())
        elif co_pro_raw is not None:
            co_pro = bool(co_pro_raw)
        else:
            co_pro = None

        # --- Governance flags ---
        consent = bool(
            request_metadata.get("b2b_consent")
            or request_metadata.get("data_consent")
        )
        is_internal = bool(request_metadata.get("is_internal"))

        return {
            "script_id": report_row.get("id") or report_id,
            "home_country": home_country,
            "territory": home_country,  # legacy mirror during migration
            "territories_considered": territories_considered,
            "territories_recommended": territories_recommended,
            "state": request_metadata.get("state_province"),
            "submission_date": self._derive_submission_date(report_row.get("created_at")),
            "completion_window": self._month_key(request_metadata.get("completion_date")),
            "camera_equipment": camera_equipment,
            "crew_size": crew_size,
            "principal_cast": principal_cast,
            "supporting_cast": supporting_cast,
            "background_extras": background_extras,
            "budget_range": budget_range,
            "budget_amount_gbp": budget_amount_gbp,
            "budget_currency": budget_currency,
            "fx_rate_date": fx_rate_date,
            "format": fmt,
            "genres": genres,
            "target_audience": target_audience,
            "audience_segments": audience_segments,
            "audience_skew": request_metadata.get("audience_skew"),
            "primary_languages": primary_languages,
            "co_production_interest": co_pro,
            "b2b_consent": consent,
            "is_internal": is_internal,
            "schema_version": 2,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _normalise_budget(
        self,
        *,
        request_metadata: dict[str, Any],
        report_data: dict[str, Any],
        analysis_budget_range: str | None,
    ) -> tuple[str | None, float | None, str | None, str | None]:
        """Return (band, amount_gbp, currency, fx_rate_date_iso).

        Converts the declared budget to GBP via FXService before banding. Falls back to
        any pre-computed band only when no usable amount is present.
        """
        amount = request_metadata.get("budget_amount")
        currency = (request_metadata.get("budget_currency") or "GBP").upper()
        try:
            amount_f = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount_f = None

        if amount_f and amount_f > 0:
            if currency == "GBP":
                return gbp_band(amount_f), round(amount_f, 2), "GBP", None
            try:
                fx = FXService(get_settings()) if callable(get_settings) else None
            except Exception:  # pragma: no cover - settings unavailable
                fx = None
            if fx is not None:
                try:
                    conv = fx.convert_budget(amount_f, currency, "GBP")
                    gbp = conv["converted"]
                    return gbp_band(gbp), gbp, currency, conv.get("rate_date")
                except Exception:
                    logger.warning("FX conversion failed for B2B signal budget; storing currency only")
                    return None, None, currency, None
            return None, None, currency, None

        # No amount: use any pre-computed band string as a last resort.
        return analysis_budget_range, None, currency, None

    @staticmethod
    def _extract_recommended_territories(report_data: Any) -> list[str] | None:
        if not isinstance(report_data, dict):
            return None
        rankings = report_data.get("locationRankings") or report_data.get("location_rankings")
        if not isinstance(rankings, list):
            return None
        out: list[str] = []
        for item in rankings[:5]:
            if isinstance(item, dict):
                name = item.get("name") or item.get("territory")
                if name:
                    out.append(str(name).strip())
        return out or None

    @staticmethod
    def _month_key(value: Any) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        return raw[:7] if len(raw) >= 7 else None

    def upsert_production_signal(
        self,
        *,
        report_id: str,
        report_row: dict[str, Any],
        request_metadata: dict[str, Any],
        script_analysis: ScriptAnalysisResult | None = None,
    ) -> dict[str, Any] | None:
        payload = self._build_production_signal_payload(
            report_id=report_id,
            report_row=report_row,
            request_metadata=request_metadata,
            script_analysis=script_analysis,
        )

        # Consent gate (CRIT-2): never persist a signal the user did not consent to
        # aggregate. If a prior run for this script was consented and this run is not,
        # remove the earlier row so consent withdrawal is honoured.
        script_id = payload.get("script_id")
        if not payload.get("b2b_consent"):
            if script_id:
                try:
                    self.supabase.table("production_signals").delete().eq(
                        "script_id", script_id
                    ).execute()
                except NoSuchTableError:
                    pass
                except Exception:
                    logger.warning("Could not remove un-consented signal for script %s", script_id)
            return None

        try:
            # Dedupe on script_id (Decision 1): one signal per script, latest wins.
            existing = (
                self.supabase.table("production_signals")
                .select("id,report_runs")
                .eq("script_id", script_id)
                .execute()
            )
            prior = (existing.data or [None])[0] if getattr(existing, "data", None) else None
            if prior:
                payload["report_runs"] = int(prior.get("report_runs") or 1) + 1
                result = (
                    self.supabase.table("production_signals")
                    .update(payload)
                    .eq("script_id", script_id)
                    .select("*")
                    .single()
                    .execute()
                )
            else:
                payload["id"] = report_id
                result = (
                    self.supabase.table("production_signals")
                    .insert(payload)
                    .select("*")
                    .single()
                    .execute()
                )
            return result.data
        except NoSuchTableError:
            logger.warning("production_signals table is missing; skipping signal write")
            return None

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

    def create_share_link(self, report_id: str, user_id: str) -> str:
        """Generate (or return existing) a permanent share token for the report.

        Idempotent: if share_token is already set, returns the existing token
        without overwriting it. This prevents link rot if the studio user calls
        this endpoint more than once.
        """
        import secrets

        report = self.get_report(report_id)
        if not report:
            raise ValueError("report_not_found")
        if report.get("user_id") != user_id:
            raise PermissionError("access_denied")
        if report.get("share_token"):
            return report["share_token"]

        token = secrets.token_urlsafe(32)
        self.supabase.table("reports").update(
            {"share_token": token}
        ).eq("id", report_id).execute()
        return token

    def revoke_share_link(self, report_id: str, user_id: str) -> None:
        """Remove the share token, making the report private again."""
        report = self.get_report(report_id)
        if not report:
            raise ValueError("report_not_found")
        if report.get("user_id") != user_id:
            raise PermissionError("access_denied")
        self.supabase.table("reports").update(
            {"share_token": None}
        ).eq("id", report_id).execute()

    def delete_report(self, report_id: str, user_id: str) -> None:
        """Delete a report the user owns.

        Ownership-scoped: a user can only delete their own report. Any
        anonymised production signal derived from this report is deliberately
        left intact — it is consented, de-identified aggregate data and is not
        tied to the report's identifiable content, so a report deletion is not
        a consent withdrawal (see upsert_production_signal for that path).
        """
        report = self.get_report(report_id)
        if not report:
            raise ValueError("report_not_found")
        if report.get("user_id") != user_id:
            raise PermissionError("access_denied")
        self.supabase.table("reports").delete().eq("id", report_id).execute()

    def update_project_details(self, report_id: str, user_id: str, details: dict) -> dict:
        """Persist user-authored project details onto the report record.

        Merges incoming fields with any existing project_details so partial saves
        (e.g. saving only Finance fields) don't wipe previously saved Creative Team data.
        Explicit None values are filtered out; empty strings are preserved as cleared values.
        """
        report = self.get_report(report_id)
        if not report:
            raise ValueError("report_not_found")
        if report.get("user_id") != user_id:
            raise PermissionError("access_denied")

        existing: dict = report.get("project_details") or {}
        merged = {**existing, **{k: v for k, v in details.items() if v is not None}}

        self.supabase.table("reports").update(
            {"project_details": merged}
        ).eq("id", report_id).execute()

        return {**report, "project_details": merged}

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
        return self._generate_via_builder(
            script_service=script_service,
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
        report_data = self._generate_via_builder(
            script_service=script_service,
            script_analysis=script_analysis,
            request_metadata=request_metadata,
            datasets=datasets,
            is_preview=False,
        )

        if is_b2b:
            report_data["productionIntelligence"] = self._build_production_intelligence()

        return report_data

    def _generate_via_builder(
        self,
        *,
        script_service: ScriptAnalysisService,
        script_analysis: ScriptAnalysisResult | None,
        request_metadata: dict,
        datasets: dict,
        is_preview: bool,
    ) -> dict:
        """Builder path: deterministic skeleton + narrative-only AI call."""
        from app.modules.reports.builder import ReportBuilder
        from app.modules.reports.validator import ReportValidator

        builder = ReportBuilder(
            datasets=datasets,
            request_metadata=request_metadata,
            script_analysis=script_analysis,
            is_preview=is_preview,
        )
        skeleton = builder.build()

        report_data = script_service.generate_production_analysis_v2(
            skeleton=skeleton,
            script_analysis=script_analysis,
            request_metadata=request_metadata,
            datasets=datasets,
            is_preview=is_preview,
        )

        report_data, warnings = ReportValidator.assert_integrity(
            report_data, datasets
        )
        if warnings:
            logger.info(
                "Builder path: %d integrity warnings",
                len(warnings),
            )
        return report_data

    def _inject_derived_data(
        self,
        datasets: dict,
        *,
        script_analysis: ScriptAnalysisResult | None,
        request_metadata: dict,
    ) -> None:
        """Compute and inject derived signals into *datasets* in-place.

        These are used by the ReportBuilder, the AI narrative prompt, and
        the post-processing validator (ReportValidator.assert_integrity).
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

        # Authoritative shoot days — stored so the validator can override
        # any AI-hallucinated value in executiveSummary.shootDays.
        #
        # filming_duration (weeks, user-decided) is the primary source because
        # it reflects the producer's actual planned schedule.  Script analysis
        # estimatedShootingDays is only a fallback for when filming_duration
        # is not provided.
        filming_duration_weeks = request_metadata.get("filming_duration")
        if filming_duration_weeks and int(filming_duration_weeks) > 0:
            # filming_duration is the user-decided shoot schedule in weeks —
            # store it directly (no conversion)
            datasets["_shoot_weeks"] = int(filming_duration_weeks)
        else:
            # Fallback: derive weeks from script analysis estimated days
            _script_days = getattr(
                getattr(script_analysis, "productionScale", None),
                "estimatedShootingDays",
                None,
            ) if script_analysis is not None else None
            if _script_days and int(_script_days) > 0:
                datasets["_shoot_weeks"] = max(1, round(int(_script_days) / 5))

        # Producer eligibility inputs
        datasets["_producer_country"] = request_metadata.get("producer_country")
        datasets["_co_production_status"] = request_metadata.get("co_production_status")

        # Visa requirements — load DB-backed entries for the user's base country
        # so the validator can replace AI-generated travelVisa fields with
        # authoritative data instead of accepting hallucinated advice.
        base_country = request_metadata.get("country") or "United Kingdom"
        visa_rows = self._safe_query(
            "visa_requirements",
            lambda q: q.select("destination,visa_required,work_permit_required,notes")
                       .eq("base_country", base_country),
        )
        datasets["_visa_requirements"] = {
            (row.get("destination") or "").strip(): {
                "visa_required": row.get("visa_required"),
                "work_permit_required": row.get("work_permit_required"),
                "notes": row.get("notes") or "",
            }
            for row in visa_rows
            if row.get("destination")
        }

        # v3: Production priority (needed by validator for score recalculation)
        datasets["_production_priority"] = request_metadata.get("production_priority", "full")

        # User-submitted territories — canonical labels for builder territory selection.
        # Resolved via the same normalisation as _resolve_territories_hint.
        raw_considering = request_metadata.get("territories_considering") or []
        user_territories: list[str] = []
        seen_ut: set[str] = set()
        for raw in raw_considering:
            if raw.lower() in ("open to all", "open"):
                continue
            t = resolve_territory(raw)
            label = t.label if t else raw
            if label not in seen_ut:
                seen_ut.add(label)
                user_territories.append(label)
        datasets["_user_territories"] = user_territories

        # v3: Production format (needed by validator for format harmonisation)
        datasets["_production_format"] = request_metadata.get("format")

        # Episode metadata — used by validator for UK AVEC HETV threshold check
        datasets["_total_episodes"] = request_metadata.get("total_episodes")
        datasets["_episode_runtime_minutes"] = request_metadata.get("episode_runtime_minutes")

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

        # Load territory profiles for deterministic crew depth / infrastructure
        # scores plus bankability payment-timing intelligence
        territory_profile_rows = self._safe_query(
            'territory_profiles',
            lambda q: q.select(
                'territory,crew_depth_tier,crew_depth_score,crew_depth_notes,'
                'cost_efficiency_score,cost_efficiency_source,'
                'infrastructure_tier,infrastructure_score,infrastructure_notes,'
                'hemisphere,cert_weeks_min,cert_weeks_max,'
                'payment_weeks_min,payment_weeks_max,'
                'bankability_source_quality,bankability_suspended'
            )
        )
        datasets['_territory_profiles'] = {
            row['territory']: row
            for row in territory_profile_rows
            if row.get('territory')
        }

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

        territory_incentives = _index_incentives_by_territory(incentives)
        budget_symbol = _currency_symbol(budget_currency)

        production_format: str | None = datasets.get("_production_format")
        territory_financials: dict[str, dict] = {}

        for territory, rows in territory_incentives.items():
            if not territory or not rows:
                continue
            best = _best_incentive(rows, production_format)

            # Resolve FX rate for rebate cap enforcement (e.g. South Africa R25M).
            # When budget_currency is GBP, fx_rates_from_budget gives GBP→cap_currency.
            rebate_cap_currency = best.get("rebate_cap_currency")
            fx_rate_to_gbp: float | None = None
            if rebate_cap_currency and rebate_cap_currency != "GBP" and budget_currency == "GBP":
                fx_info = fx_rates_from_budget.get(rebate_cap_currency)
                if fx_info and fx_info.get("rate"):
                    fx_rate_to_gbp = fx_info["rate"]  # GBP → rebate_cap_currency

            corrected = ReportValidator._compute_corrected_rebate(
                best, budget_gbp, territory_incentives,
                production_format=production_format,
                fx_rate_to_gbp=fx_rate_to_gbp,
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
                "atl_deduction_note": corrected.get("atl_deduction_note"),
                "rebate_cap_note": corrected.get("rebate_cap_note"),
                "qualifying_spend_note": corrected.get("qualifying_spend_note"),
                "fx_note": fx_note,
                # Raw numerics (display currency) — used by the PDF waterfall
                # charts; the formatted strings above stay authoritative for text
                "total_budget_value": d_total,
                "qualifying_spend_value": d_qs,
                "gross_rebate_value": d_gross_rebate,
                "net_rebate_value": d_net_rebate,
                "net_budget_value": d_net_budget,
                "rate_gross_value": corrected["rate_gross"],
                "rate_net_value": corrected.get("rate_net"),
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
            # Also build ISO set for matching country-style dataset fields
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

        # (crew/cast day-rate loading removed 2026-07, owner-approved)

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
        # Distributors — confirmed-active only; verify_* statuses are never
        # presented as recommendations
        distributors = [
            d for d in self._safe_query("distributors", lambda q: q.select("*"))
            if (d.get("active_status") or "") == "confirmed_active"
        ]

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
            "Loaded analysis datasets counts: incentives=%s comparables=%s grants=%s festivals=%s weather=%s",
            len(incentives),
            len(comparables),
            len(grants),
            len(festivals),
            len(weather_data),
        )

        return {
            "incentives": incentives,
            "comparables": comparables,
            "grants": grants,
            "festivals": festivals,
            "distributors": distributors,
            "weather": weather_data,
            "stacking_map": stacking_map,
        }

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
            incentives = [i for i in all_incentives if i["territory"] == territory["name"]]
            ta = self._build_territory_analysis(territory, incentives, analysis)
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

    def _build_territory_analysis(self, territory, incentives, analysis):
        # Crew day-rate estimation removed 2026-07 (owner-approved); scoring is
        # incentive + location-match only on this deprecated path.
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
        inc_score = min((incentive_details[0]["potentialRebateUSD"] / 1_000_000 * 10) if incentive_details else 0, 40)
        loc_score = territory["matchScore"] / 100 * 20
        overall = min(round(inc_score + loc_score), 100)
        pros = []
        if incentive_details and incentive_details[0]["potentialRebateUSD"] > 500_000:
            pros.append(f"Strong incentive: {incentive_details[0]['rate']} rebate available")
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
