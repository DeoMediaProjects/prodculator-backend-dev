import logging
from datetime import datetime, timezone
from uuid import uuid4

from app.core.database_client import DatabaseClient
from app.modules.scripts.schemas import ScriptAnalysisResult
from app.modules.scripts.service import ScriptAnalysisService

logger = logging.getLogger(__name__)


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

    def fail_report(self, report_id: str, error_message: str) -> None:
        """Mark report as failed."""
        self.supabase.table("reports").update(
            {"status": "failed", "report_data": {"error": error_message}}
        ).eq("id", report_id).execute()

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

    def generate_preview_report(
        self,
        *,
        request_metadata: dict,
        script_service: ScriptAnalysisService,
    ) -> dict:
        """Generate a free preview report synchronously (no script, no DB row)."""
        datasets = self._load_analysis_datasets(
            territories_hint=request_metadata.get("territories_considering")
        )
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
            territories_hint=request_metadata.get("territories_considering")
        )
        report_data = script_service.generate_production_analysis(
            script_analysis=script_analysis,
            request_metadata=request_metadata,
            datasets=datasets,
            is_preview=False,
        )

        if is_b2b:
            report_data["productionIntelligence"] = self._build_production_intelligence()

        return report_data

    # --- Dataset loading ---

    def _load_analysis_datasets(self, territories_hint: list[str] | None = None) -> dict:
        """Load admin-managed datasets for AI prompt injection."""
        # Active incentive programs
        incentives_q = self.supabase.table("incentive_programs").select("*").eq("status", "active")
        all_incentives = incentives_q.execute().data or []
        if territories_hint:
            filtered = [i for i in all_incentives if i.get("territory") in territories_hint]
            # Fall back to all if filter yields nothing
            incentives = filtered if filtered else all_incentives
        else:
            incentives = all_incentives

        # Crew costs
        crew_q = self.supabase.table("crew_costs").select("*")
        all_crew = crew_q.execute().data or []
        if territories_hint:
            filtered = [c for c in all_crew if c.get("territory") in territories_hint]
            crew_costs = filtered if filtered else all_crew
        else:
            crew_costs = all_crew

        # Comparable productions (small dataset, load all)
        comparables = self.supabase.table("comparable_productions").select("*").execute().data or []

        # Open grants
        grants = (
            self.supabase.table("grant_opportunities")
            .select("*")
            .in_("status", ["open", "opening_soon", "closing_soon"])
            .execute()
            .data
            or []
        )

        # Upcoming festivals
        festivals = (
            self.supabase.table("film_festivals")
            .select("*")
            .in_("status", ["upcoming", "open"])
            .order("submission_deadline", desc=False)
            .execute()
            .data
            or []
        )

        return {
            "incentives": incentives,
            "crew_costs": crew_costs,
            "comparables": comparables,
            "grants": grants,
            "festivals": festivals,
        }

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
        daily = sum((c.get("day_rate_cents") or 0) for c in crew_costs) / 100
        weekly = sum((c.get("week_rate_cents") or 0) for c in crew_costs) / 100
        weeks = max(1, (shooting_days + 4) // 5)
        total = weekly * weeks
        breakdown = [
            {"role": c["role"], "dayRate": (c.get("day_rate_cents") or 0) / 100, "weekRate": (c.get("week_rate_cents") or 0) / 100}
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
