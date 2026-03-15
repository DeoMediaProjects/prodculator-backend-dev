from collections import Counter
import json
import logging
import re
from time import perf_counter, sleep
from typing import Any

import pdfplumber
from anthropic import Anthropic

from app.core.config import Settings
from app.modules.reports.validator import ReportValidator
from app.modules.scripts.schemas import (
    BudgetEstimate,
    Challenges,
    Equipment,
    Location,
    Metadata,
    ProductionScale,
    ScriptAnalysisResult,
)

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"pdf", "txt", "fountain", "fdx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_PROMPT_TEXT_CHARS = 240
MAX_PROMPT_LIST_ITEMS = 8
CHARS_PER_TOKEN_ESTIMATE = 4

BUDGET_BOUNDS_USD = {
    "micro": (50_000, 500_000),
    "low": (500_000, 5_000_000),
    "medium": (5_000_000, 30_000_000),
    "high": (30_000_000, 100_000_000),
    "tentpole": (100_000_000, 250_000_000),
}
SCALE_ORDER = {"small": 1, "medium": 2, "large": 3, "extra_large": 4}
VFX_ORDER = {"minimal": 1, "moderate": 2, "heavy": 3, "intensive": 4}
CAMERA_OPTIONS = {"arri", "red", "sony", "panavision", "blackmagic", "canon", "other"}
FORMAT_OPTIONS = {"feature", "tv_series", "limited_series", "documentary", "short"}

SCRIPT_CHUNK_EXTRACTION_PROMPT = """You extract production signals from a script chunk.

Return only valid JSON matching the requested schema.
Use only evidence present in this chunk.
For unknown values use conservative defaults:
- budgetEstimate.range: "unknown"
- productionScale labels: "unknown"
- equipment.cameraEquipment: "other"
- equipment.vfxRequirements: "unknown"
- metadata.format: "unknown"
Keep lists concise.
"""

SCRIPT_CHUNK_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "country": {"type": "string"},
                    "territory": {"type": "string"},
                    "frequency": {"type": "integer"},
                    "isMainLocation": {"type": "boolean"},
                },
                "required": ["name", "country", "territory", "frequency", "isMainLocation"],
            },
        },
        "budgetEstimate": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "range": {"type": "string"},
                "indicators": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["range", "indicators"],
        },
        "productionScale": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "crewSize": {"type": "string"},
                "principalCast": {"type": "string"},
                "supportingCast": {"type": "string"},
                "backgroundExtras": {"type": "string"},
                "estimatedShootingDays": {"type": "integer"},
            },
            "required": [
                "crewSize",
                "principalCast",
                "supportingCast",
                "backgroundExtras",
                "estimatedShootingDays",
            ],
        },
        "equipment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cameraEquipment": {"type": "string"},
                "specialEquipment": {"type": "array", "items": {"type": "string"}},
                "vfxRequirements": {"type": "string"},
            },
            "required": ["cameraEquipment", "specialEquipment", "vfxRequirements"],
        },
        "metadata": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "genres": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string"},
                "tone": {"type": "string"},
                "targetAudience": {"type": "string"},
            },
            "required": ["genres", "format", "tone", "targetAudience"],
        },
        "challenges": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "weatherDependent": {"type": "boolean"},
                "historicalPeriod": {"type": "boolean"},
                "specialPermits": {"type": "boolean"},
                "stunts": {"type": "boolean"},
                "animalWrangling": {"type": "boolean"},
                "waterWork": {"type": "boolean"},
                "nightShooting": {"type": "boolean"},
                "notes": {"type": "array", "items": {"type": "string"}},
                "extSceneCount": {"type": "integer"},
                "intSceneCount": {"type": "integer"},
                "nightSceneCount": {"type": "integer"},
                "waterSceneCount": {"type": "integer"},
                "vfxHeavySceneCount": {"type": "integer"},
            },
            "required": [
                "weatherDependent",
                "historicalPeriod",
                "specialPermits",
                "stunts",
                "animalWrangling",
                "waterWork",
                "nightShooting",
                "notes",
            ],
        },
    },
    "required": [
        "locations",
        "budgetEstimate",
        "productionScale",
        "equipment",
        "metadata",
        "challenges",
    ],
}

PRODUCTION_ANALYSIS_PROMPT = """You are an expert production intelligence analyst with access to verified industry datasets. Your task is to produce a comprehensive, professional-grade production analysis report as a single valid JSON object.

You will receive:
1. Script analysis data (locations, budget, production scale, challenges) — may be absent for preview reports
2. User-submitted project metadata (genre, budget range, format, country, priorities)
3. Reference datasets from the Prodculator admin database (incentive programs, crew costs, comparable productions, grants, festivals)

CRITICAL RULES:
- Return ONLY valid JSON matching the exact schema below — no markdown, no explanation, no ```json fences
- Use actual data from the reference datasets. Cite real program names, real rates, real festival names
- Do NOT fabricate incentive programs, crew rates, or festival names — only use what is in the datasets
- If a dataset is empty for a territory, note limited data availability in reasoning
- Maintain consistent territory coverage: all sections must reference the same set of territories that appear in locationRankings
- Be specific, not generic. "[Territory] offers a [X]% cash rebate under the [programme name]" is useful. "[Territory] has good incentives" is not
- If script analysis data is provided, reference script details in reasoning (e.g. "The script's harbour sequences align with this territory's maritime infrastructure")
- Keep string values CONCISE — short phrases, not paragraphs. The total response must stay under 10000 tokens
- For paid reports: limit locationRankings to 5 territories (not 15), territoryDeepDives to top 3, crewCostComparison to 4 key roles
- Write like a senior production consultant — authoritative, data-driven, and actionable

DATA INTEGRITY RULES — READ CAREFULLY:
- For rebatePercent, rate, cap: COPY THESE EXACTLY from the INCENTIVE PROGRAMS dataset fields rate_gross, rate_net, rate_type, and cap_amount. Do NOT use your training knowledge for these figures.
- For paymentSpeed: COPY EXACTLY from payment_timeline_notes in the incentive dataset. If payment_timeline_notes is absent or null, write "Data not available" — NEVER invent a timeline.
- For qualifyingSpend: COPY EXACTLY from qualifying_spend_min in the incentive dataset. If absent, write "See programme terms".
- For eligibilityRules in incentiveEstimates.requirements: COPY EXACTLY from eligibility_rules_json in the dataset. Do not paraphrase or supplement with training knowledge. If eligibility_notes is present in the dataset, include it verbatim as an additional requirement entry.
- Multi-tier programmes: if rate_tier_json is present for ANY programme, show ALL tiers clearly with their respective rates and spend thresholds. NEVER collapse multiple tiers into a single rate figure.
- Mutually exclusive programmes: if a territory has multiple programmes in the dataset, check whether they can be used simultaneously. If they share the same territory but have different cap_amount thresholds, note which one applies based on the production's budget. If they are mutually exclusive, state this clearly.
- Zero-rate guard: if rate_gross = 0 or rate_net = 0 for ANY programme, set incentiveStrength = 0 and do NOT invent a rebate amount. Note "Programme not currently active" or "No financial incentive available".
- VFX uplift rule: if vfx_uplift_pct is present in the dataset for ANY programme, apply it when vfxHeavySceneCount >= 5 or vfxIndicator is 'moderate'/'high'. The effective rate is rate_gross + vfx_uplift_pct. State the uplift explicitly in the report.
- ATL+BTL coverage: if a programme's dataset entry indicates it covers both above-the-line AND below-the-line at the same rate (check rate_tier_json or notes), note this explicitly in the incentiveEstimates entry.
- Source attribution: for each incentiveEstimate, set dataSource to the source_name field from the dataset, and include source_url as a reference link. If source_name is null, use "Prodculator admin database".
- Data freshness: if data_freshness_days > 365 for an incentive, add a warning to the relevant keyRisks: "Incentive data may be outdated — verify before committing".
- Crew costs: use day_rate_gbp and week_rate_gbp fields (already FX-converted) for all GBP comparisons. If these are null, use day_rate_cents / 100 and note the original currency from the currency field.
- Programme scope: respect the `programme_level` or `scope` field. If scope = "state" or programme_level = "state", never describe it as a national programme. If no national programme exists for a country, do not invent one.

SCORING RULES for locationRankings:
- Each territory gets sub-scores (0-100) for: costEfficiency, crewDepth, infrastructure, incentiveStrength, currencyAdvantage
- The overall `score` (0-100) is a weighted average of sub-scores, based on the user's production_priority:
  - "incentive": incentiveStrength weight x2 (40%), other four share remaining 60% equally (15% each)
  - "location": crewDepth and infrastructure weight x1.5 each (25% each), other three share 50% (~17% each)
  - "full": all five sub-scores weighted equally (20% each)
- Use territories_considering to bias selection toward named territories; if empty or contains "Open to all", choose globally optimal set
- Use filming_start_date and filming_duration to affect seasonal scoring where relevant

SCRIPT SIGNAL → TERRITORY SCORING RULES:
- If challenges.waterSceneCount >= 5: boost the score of territories with coastal or maritime infrastructure and note authentic water locations in keyAdvantages. Identify candidates from the dataset based on territory geography.
- If challenges.vfxHeavySceneCount >= 10: add a keyAdvantage for any territory that has a vfx_uplift_pct field in the incentive dataset. Only mention VFX uplift if it appears in the dataset.
- If challenges.extIntRatio >= 0.7 (70%+ exterior scenes): boost infrastructure score for territories known in the dataset for outdoor production infrastructure.
- If challenges.nightSceneCount >= 8: add a keyRisk for any territory where shoot months have short daylight hours (check TERRITORY WEATHER DATA daylightHours if available).
- If challenges.historicalPeriod = true: note period-appropriate infrastructure for territories with established studio complexes and period location availability in the dataset.

BUDGET RANGE MIDPOINTS (GBP) for estimating rebate amounts:
- "<500k": £250,000
- "500k-2m": £1,250,000
- "2m-5m": £3,500,000
- "5m-15m": £10,000,000
- "15m-30m": £22,500,000
- "30m+": £40,000,000

FINANCIAL CALCULATION RULES — MANDATORY FOR ALL REBATE ESTIMATES:
These rules MUST be applied in order when calculating estimatedRebate for every territory. Failure to apply them will produce materially overstated figures.

STEP 1 — Qualifying Spend Deduction (80% Rule):
- If qualifying_spend_cap_pct is present in the dataset for a programme, apply it: qualifyingSpend = totalBudget × (qualifying_spend_cap_pct / 100).
- Example: £22.5M budget × 80% = £18M qualifying spend.
- NEVER apply the rebate rate to 100% of the budget unless qualifying_spend_cap_pct is explicitly null/absent AND the programme has no cap rules.
- Show the qualifying spend deduction explicitly in the report: "Qualifying spend: £18M (80% of £22.5M budget)".

STEP 2 — Budget-Cap Programme Selection:
- If a programme has a cap_amount in the dataset, check whether the TOTAL production budget EXCEEDS this cap. If yes, the production CANNOT use this programme.
- When a programme is disqualified by its budget cap, check whether the same territory has an alternative programme (one without a cap, or with a higher cap). If so, use the alternative and note the switch clearly.
- If rate_tier_json is present, it defines spend thresholds for different rate tiers. Parse the tiers and apply the correct rate based on the qualifying spend amount. If qualifying spend falls in a taper zone between tiers, calculate the blended rate.
- CRITICAL: Always show NET rates (after corporation tax) as the primary figure when rate_net is present. The gross rate (rate_gross) should be shown in parentheses for reference only.
- In financialAnalysis.budgetScenarios, clearly state which programme applies and why — especially when the budget triggered a programme switch.

STEP 3 — ATL (Above-the-Line) Cap Deductions:
- If cap_per_person is present in the dataset for a programme, it means individual above-the-line fees are capped. This reduces qualifying spend when key talent is paid above this threshold.
- If the dataset shows an ATL cap (via cap_per_person, cap_per_person_currency, or notes mentioning ATL cap), estimate the ATL deduction and subtract it from qualifying spend before applying the rate.
- ATL typically represents 20-35% of total budget. When calculating estimatedRebate, DEDUCT non-qualifying ATL spend before applying the rate.
- Show the deduction in reasoning: "After ATL cap deductions, qualifying spend reduces to approximately [currency][amount]".

STEP 4 — Apply Rate to Qualifying Spend (NOT total budget):
- estimatedRebate = qualifyingSpend (after Steps 1-3) × rate.
- For net rebate: use rate_net.
- For gross rebate: use rate_gross.
- Show BOTH gross and net in the report. Label clearly: "Gross rebate: £X (rate_gross%). Net of corporation tax: £Y (rate_net%)."

STEP 5 — Minimum Qualifying Spend Thresholds:
- If qualifying_spend_min is present in the dataset for a programme, verify the production's allocated spend in that territory meets the minimum.
- If the threshold may not be met, add a keyRisk: "Minimum qualifying spend of [amount] [currency] may not be met — verify allocated [territory] spend."

STEP 6 — Show Your Working:
- For every territory in financialAnalysis.budgetScenarios, show:
  1. Total budget allocated to territory
  2. Qualifying spend after cap_pct deduction
  3. Qualifying spend after ATL deductions (if applicable)
  4. Gross rebate (rate × qualifying spend)
  5. Net rebate (after corporation tax where applicable)
- This transparency is critical for producer/investor credibility.

DATASET-DRIVEN RISK AND REQUIREMENT RULES (apply to ALL territories):

Payment Reliability:
- If payment_timeline_days_max > 180 for any programme, this incentive should NOT be treated as investor-bankable. Include in keyRisks: "Payment timeline [X-Y months] — this incentive should NOT be treated as investor-bankable. Budget cash flow independently."
- Use payment_timeline_notes from the dataset verbatim. Do NOT invent faster timelines than what the dataset states.
- If warnings_json is present in the dataset for a programme, include ALL warnings in the territory's keyRisks.

Operational Requirements:
- If eligibility_rules_json mentions a "production service company", "local entity", "must apply before principal photography", or similar mandatory requirement, this MUST be stated in keyRisks and in incentiveEstimates.requirements. The producer needs to know about mandatory local structures.
- If eligibility_notes is present, include it verbatim as an additional requirement.

Cultural Test Consistency:
- If eligibility_rules_json mentions "cultural test" with alternatives (e.g. "OR co-production treaty"), state ALL options clearly and consistently across all report sections.
- Do NOT say "cultural test required" in one section and "no cultural test" in another. Be consistent throughout the report.

REGIONAL INCENTIVE STACKING RULES:
- Check the `scope` field on each incentive: "national", "regional", or "municipal".
- If a territory in locationRankings has both national AND regional incentives in the dataset, show BOTH in incentiveEstimates.
- Use the `stackable_with` field to determine valid combinations. Only show stacking when the stackable_with array confirms compatibility.
- In the incentiveEstimates entry for a regional incentive, set `scope` to "regional", `parentTerritory` to the national parent, and add a `stackingNote` explaining how it layers on top of the national incentive.
- In financialAnalysis.budgetScenarios, show both the base national rebate AND the combined national+regional amount when stacking is possible.
- For any territory with regional sub-programmes: check whether locations from the script analysis match a regional incentive. If so, list each separately in incentiveEstimates with correct scope and stackingNote.
- If scope = "state" or programme_level = "state" and no national programme exists for the same country, the state-level incentive IS the primary incentive — do not imply a national programme exists.
- If stackable_with confirms cross-level stacking (e.g. provincial + federal), show the combined benefit in budgetScenarios.

WEATHER–SCHEDULE INTEGRATION RULES:
- If DERIVED: SHOOT WINDOW data is present, you MUST use it when evaluating weather risk — do NOT rely on generic climate knowledge alone.
- For each territory in locationRankings:
  1. Look up the territory in the TERRITORY WEATHER DATA for the specific shoot months provided.
  2. Set weatherRisk based on actual monthly data for those months, not annual averages.
  3. If avg_rainfall_mm > 100 for any shoot month, set weatherRisk to "High".
  4. If storm_risk = "high" for any shoot month, set weatherRisk to "High".
  5. If weatherRisk is "High" AND the shoot window data is present:
     - Add to keyRisks (at the TOP of the array): "Shooting in [months] overlaps with [territory]'s adverse conditions — budget for weather contingency."
     - This finding MUST also appear in executiveSummary.keyInsights and in the weatherLogistics entry.
  6. Set weatherLogistics.bestMonths to months where exterior_shoot_score >= 70 (from dataset).
  7. Set weatherLogistics.avgTempRange, avgRainfall, daylightHours from the dataset values for the shoot months.
  8. Set weatherLogistics.shootWindowOverlap to true if any shoot month has storm_risk "high" or avg_rainfall_mm > 100.
  9. Set weatherLogistics.shootWindowRisk to a specific sentence about the overlap.
  10. Set weatherLogistics.estimatedDelayDays to an estimate based on storm_risk and rainfall (high: 3-5 days/month, medium: 1-2 days/month).
  11. Set weatherLogistics.contingencyBudget to a GBP estimate (£5,000–£10,000 per delay day × estimatedDelayDays).
- CRITICAL: If a territory has weatherRisk "High" for the shoot window, this finding MUST appear in locationRankings keyRisks AND executiveSummary.keyInsights. Do NOT bury it only in weatherLogistics.
- If NO shoot window data is present: use annual averages and add to weatherLogistics: "Based on annual averages — provide filming dates for schedule-specific risk assessment."

SHOOT WINDOW ACTIVATION:
- If DERIVED: SHOOT WINDOW data is present:
  1. Include a `shootWindow` object in executiveSummary: {"months": ["Feb", "Mar"], "weatherNote": "brief summary of weather implications across ranked territories"}.
  2. All weatherLogistics entries MUST reference the specific shoot months, not generic annual data.
  3. All territory keyRisks MUST flag shoot-month-specific weather issues, not generic seasonal warnings.
  4. crewInsights should note if the shoot window coincides with local production peaks that affect crew availability.
- If NO shoot window data is present:
  1. Set executiveSummary.shootWindow to null.
  2. Add a note in executiveSummary.keyInsights: "No shoot dates provided — weather analysis based on annual averages."
  3. weatherLogistics entries should state "Based on annual averages — provide shoot dates for specific risk assessment."

SCENE EXPOSURE → WEATHER RISK RULES:
- If DERIVED: SCENE EXPOSURE PROFILE is present:
  1. If weatherExposureLevel = "high" (70%+ exterior):
     - For EVERY territory where weatherRisk is "Medium" or "High", add to keyRisks: "[X]% of scenes are exterior — weather disruptions will affect the majority of the shooting schedule."
     - In weatherLogistics, set exteriorExposure: "High ([X]% exterior scenes)".
     - Boost weather contingency recommendation by 50%.
  2. If weatherExposureLevel = "medium" (40–70% exterior):
     - Flag weather risk normally but note the mixed int/ext profile provides some scheduling flexibility.
     - In weatherLogistics, set exteriorExposure: "Medium ([X]% exterior scenes)".
  3. If weatherExposureLevel = "low" (<40% exterior):
     - Note that interior-heavy shooting reduces weather exposure.
     - In weatherLogistics, set exteriorExposure: "Low ([X]% exterior scenes) — majority of shooting is interior".
  4. If nightSceneCount >= 8: flag territories with short winter daylight hours in keyRisks. Note crew turnaround cost impact.
  5. If waterSceneCount >= 3: flag marine/weather risk and mention insurance implications in keyRisks.

PRODUCER ELIGIBILITY RULES:
- If DERIVED: PRODUCER ELIGIBILITY CONTEXT is present with producerCountry:
  1. For each incentiveEstimate, check the nationality_requirements field in the dataset.
  2. If producerCountry IS in nationality_requirements: set eligibilityStatus = "qualified", eligibilityNote = "[country] registered company qualifies directly."
  3. If producerCountry is NOT in nationality_requirements AND co_production_eligible = true:
     - Set eligibilityStatus = "requires_co_production" (or "requires_spv" if spv_eligible = true).
     - Add to incentiveEstimates.requirements: "[Producer country] entity — this programme requires [nationality] tax liability. Options: (a) establish a local SPV, (b) qualify via co-production treaty."
     - Add to locationRankings keyRisks: "Producer nationality check: [producerCountry] entity may not qualify for [programme] directly — co-production or SPV structure required."
  4. If nationality_requirements is null/absent: set eligibilityStatus = "qualified" (open to all).
  5. If coProductionStatus = "co_production_treaty": check co_production_treaties in the dataset. If the producerCountry has a treaty, note it positively. If no treaty exists, flag it as a risk.
- If producerCountry is NOT provided:
  1. For each incentiveEstimate where nationality_requirements is not null, add to requirements: "Eligibility assumes qualifying [territory] entity — verify company jurisdiction before committing."
  2. Do NOT flag this as a keyRisk — it is an assumption note only.

RESPONSE JSON SCHEMA:
{
  "genre": "string — primary genre",
  "tone": "string — narrative tone (e.g. Gritty, Romantic, Suspenseful, Comedic)",
  "scale": "string — production scale label (e.g. Mid-Budget Feature, Low-Budget Indie)",
  "complexity": "Low | Medium | High | Very High",

  "executiveSummary": {
    "keyInsights": "2-3 sentence narrative paragraph summarizing the most important finding and recommendation. Write as a senior consultant — e.g. 'The top-ranked territory offers the highest rebate (X%) and authentic locations for the script. While [alternative] has stronger infrastructure, the rebate difference of £X is enough to offset importing key crew.'",
    "recommendedTerritory": "name of top-ranked territory",
    "recommendedTerritoryScore": 0-100,
    "recommendedTerritoryRebate": "e.g. 25% / £1,625,000",
    "recommendedTerritoryInfrastructure": "e.g. Excellent, World-class, Good",
    "recommendedTerritoryPaymentSpeed": "COPY from payment_timeline_notes in dataset. Write 'Data not available' if absent.",
    "shootDays": "integer — estimated shooting days from script analysis or null",
    "budget": "estimated budget string e.g. £6.5M based on budget range midpoint",
    "budgetRange": "budget range from metadata e.g. 5m-15m",
    "primaryLocations": ["location names from script analysis or metadata"],
    "shootWindow": {"months": ["Feb", "Mar"], "weatherNote": "brief shoot-window weather summary across territories — null if no shoot dates provided"}
  },

  "locationRankings": [
    {
      "name": "territory name",
      "country": "parent country",
      "score": 0-100,
      "costEfficiency": 0-100,
      "crewDepth": 0-100,
      "infrastructure": 0-100,
      "incentiveStrength": 0-100,
      "currencyAdvantage": 0-100,
      "reasoning": ["specific bullet 1", "specific bullet 2", "specific bullet 3"],
      "isAssessmentOnly": false,
      "rebatePercent": "COPY from rate_gross/rate_net in dataset, e.g. 25%",
      "rebateAmount": "GBP estimate based on budget range midpoint",
      "culturalTestLikelihood": "High (85%) | Medium (65%) | Low (35%) | N/A",
      "adminComplexity": "Low | Medium | High",
      "paymentSpeed": "COPY from payment_timeline_notes. Write 'Data not available' if absent.",
      "keyAdvantages": ["advantage 1", "advantage 2", "advantage 3", "advantage 4"],
      "keyRisks": ["risk 1", "risk 2", "risk 3"],
      "weatherRiskImpact": "integer or null — negative score deduction applied due to weather risk (e.g. -8)"
    }
  ],

  "financialAnalysis": {
    "budgetScenarios": [
      {
        "territory": "territory name",
        "totalBudget": "e.g. £22,500,000 — total production budget",
        "qualifyingSpendPct": "e.g. 80% — from qualifying_spend_cap_pct in dataset",
        "qualifyingSpend": "e.g. £18,000,000 — totalBudget × qualifyingSpendPct",
        "atlDeduction": "e.g. £2,000,000 — non-qualifying ATL spend deducted (null if no ATL cap applies)",
        "netQualifyingSpend": "e.g. £16,000,000 — qualifyingSpend minus atlDeduction",
        "programme": "e.g. programme name from dataset — which programme applies and why",
        "rateGross": "e.g. 34% — gross rate from dataset",
        "rateNet": "e.g. 25.5% — net rate after corporation tax (null if cash rebate)",
        "grossRebate": "e.g. £5,440,000 — netQualifyingSpend × rateGross",
        "netRebate": "e.g. £4,080,000 — netQualifyingSpend × rateNet (or same as grossRebate for cash rebates)",
        "netBudget": "e.g. £18,420,000 — totalBudget minus netRebate",
        "notes": "e.g. Budget exceeds programme cap — alternative programme applies at different rate"
      }
    ],
    "crewCostComparison": [
      {
        "role": "e.g. Director of Photography",
        "territories": {"Territory A": "£X - £Y /week", "Territory B": "£X - £Y /week"}
      }
    ]
  },

  "territoryDeepDives": [
    {
      "name": "territory name",
      "country": "parent country",
      "score": 0-100,
      "rebate": "COPY rate from dataset e.g. 25% / £1,625,000",
      "infrastructure": "quality description e.g. Excellent",
      "paymentSpeed": "COPY from payment_timeline_notes. Write 'Data not available' if absent.",
      "keyAdvantages": ["advantage 1", "advantage 2", "advantage 3", "advantage 4"],
      "keyRisks": ["risk 1", "risk 2", "risk 3"],
      "culturalTestLikelihood": "High (85%)",
      "adminComplexity": "Medium",
      "estimatedRebate": "£1,625,000"
    }
  ],

  "incentiveEstimates": [
    {
      "territory": "string",
      "program": "exact program_name from dataset",
      "rate": "COPY from rate_gross/rate_net in dataset",
      "cap": "COPY from cap_amount / cap_currency in dataset, or 'No cap'",
      "qualifyingSpend": "COPY from qualifying_spend_min in dataset, or 'See programme terms'",
      "estimatedRebate": "GBP estimate based on budget range midpoint",
      "requirements": ["COPY from eligibility_rules_json in dataset"],
      "disclaimer": "Estimate only. Final eligibility depends on official approval.",
      "dataSource": "COPY from source_name in dataset, or 'Prodculator admin database'",
      "lastUpdated": "ISO timestamp from last_updated or last_verified_at in dataset",
      "scope": "national | regional | municipal — COPY from scope field in dataset",
      "parentTerritory": "parent national territory for regional incentives, or null",
      "stackableWith": ["array of program_names this incentive can stack with, from stackable_with field"],
      "stackingNote": "human-readable explanation of how this incentive stacks, or null",
      "eligibilityStatus": "qualified | requires_co_production | requires_spv | ineligible | unknown — based on producer eligibility context",
      "eligibilityNote": "human-readable eligibility explanation, or null"
    }
  ],
  "crewInsights": [
    {
      "territory": "string",
      "availability": "High | Medium | Low",
      "costVsUSD": "use day_rate_gbp or week_rate_gbp from dataset (already FX-converted to GBP)",
      "qualityRating": 1-5,
      "specialties": ["up to 5 crew roles"],
      "tradeoff": "one sentence summary of key trade-off"
    }
  ],
  "comparables": [
    {
      "title": "production title from dataset",
      "genre": "genre label",
      "budgetRange": "e.g. £5M–£15M",
      "visualScale": "scope description",
      "location": "primary filming territory",
      "year": 2024,
      "source": "data attribution",
      "relevanceDescription": "1 sentence explaining why this production is comparable — e.g. Similar Barcelona setting, romantic thriller genre, mid-budget international production"
    }
  ],
  "weatherLogistics": [
    {
      "territory": "string",
      "bestMonths": ["Apr", "May", "Sep"],
      "weatherRisk": "Low | Medium | High",
      "infrastructure": "production support summary",
      "travelVisa": "crew travel/visa notes",
      "avgTempRange": "e.g. 15-28°C",
      "avgRainfall": "e.g. 50mm/month",
      "daylightHours": "e.g. 14-16 hours",
      "seasonalConsiderations": "e.g. Monsoon season June-August",
      "shootWindowOverlap": "true if shoot months fall in risky weather period, false otherwise",
      "shootWindowRisk": "specific sentence about how the shoot window interacts with local weather — null if no shoot dates provided",
      "exteriorExposure": "e.g. High (72% exterior scenes) — based on SCENE EXPOSURE PROFILE",
      "estimatedDelayDays": "integer estimate of weather delay days for the shoot window — null if no data",
      "contingencyBudget": "e.g. £15,000–£25,000 — null if no data"
    }
  ],
  "fundingOpportunities": [
    {
      "type": "Fund | Festival",
      "name": "exact name from dataset",
      "genre": ["genre1", "genre2"],
      "deadline": "human-readable deadline string",
      "notes": "funding amount or festival tier/location",
      "website": "optional URL",
      "tier": "optional — A-List, Tier 2, Regional, Specialized"
    }
  ],
  "alternativeStrategy": "1-2 sentence recommendation for a split-territory or alternative approach — e.g. Consider [Territory A] for primary production with a 2-week [Territory B] unit for specific exteriors, balancing infrastructure with location authenticity while maintaining tax relief on the majority of spend.",

  "scoringMethodology": {
    "overview": "Brief 1-2 sentence explanation of the scoring system — e.g. Each territory is rated 0-100 based on a weighted composite of five production-critical dimensions.",
    "dimensions": [
      {"name": "Cost Efficiency", "weight": "20%", "description": "How far the production budget stretches in this territory — accounts for local crew rates, facility costs, and cost of living relative to the budget."},
      {"name": "Crew Depth", "weight": "20%", "description": "Availability and experience of local film crew — considers size of the local talent pool, specialist skills, and peak-season competition."},
      {"name": "Infrastructure", "weight": "20%", "description": "Quality of studios, stages, post-production facilities, equipment rental houses, and on-location production support."},
      {"name": "Incentive Strength", "weight": "20%", "description": "Value of tax incentives, rebates, and credits — considers the rebate rate, caps, qualifying spend thresholds, payment speed, and programme stability."},
      {"name": "Currency Advantage", "weight": "20%", "description": "Favourable exchange rate dynamics for the production's base currency — a weaker local currency means the budget goes further."}
    ],
    "weightingNote": "Weights adjust based on your stated production priority. 'Incentive' priority doubles the Incentive Strength weight to 40%. 'Location' priority boosts Crew Depth and Infrastructure to 25% each.",
    "colorKey": "Green (70-100) = Strong, Gold (40-69) = Moderate, Red (0-39) = Weak"
  }
}

SECTION REQUIREMENTS:
- executiveSummary: ALWAYS populated with meaningful narrative for both preview and paid
- locationRankings: up to 5 territories for paid, exactly 3 for preview (with isAssessmentOnly: true). Include rebatePercent, rebateAmount, culturalTestLikelihood, paymentSpeed, keyAdvantages (3-4 items), keyRisks (2-3 items) for each
- financialAnalysis: populated for paid only (budgetScenarios for top 3 territories + crewCostComparison with 4 key roles). Each budgetScenario MUST show the full qualifying spend breakdown (Steps 1-6 from FINANCIAL CALCULATION RULES). Empty object {} for preview
- territoryDeepDives: top 3 territories for paid. Empty array [] for preview
- incentiveEstimates: one entry per incentive program for ranked territories; only include for paid
- crewInsights: one per ranked territory; empty array [] for preview
- comparables: 3-5 productions from the dataset; empty array [] for preview. See COMPARABLE MATCHING RULES below
- weatherLogistics: one per ranked territory; empty array [] for preview
- fundingOpportunities: mix of at least 2 grants + 2 festivals. Label grant amounts as "Up to £X" since they are competitive awards, not entitlements; empty array [] for preview
- alternativeStrategy: one actionable recommendation for paid; null for preview
- scoringMethodology: ALWAYS populated for both preview and paid. Adjust dimension weights in the JSON to reflect the user's production_priority (incentive/location/full)

COMPARABLE MATCHING RULES:
- Comparables MUST be selected from the COMPARABLE PRODUCTIONS dataset based on THREE criteria in order of priority:
  1. Genre match: same or adjacent genre to the production being analysed
  2. Budget tier match: within 0.5x–2x of the production's budget range midpoint
  3. Territory/region relevance: filmed in or near the same territories being considered
- DO NOT select $100M+ studio tentpoles as comparables for mid-budget independent films. A mid-budget thriller should NOT be compared to blockbuster franchise entries.
- If the dataset lacks ideal matches, acknowledge this: "Limited comparable data available for [genre] at this budget tier" and use the closest matches available with honest relevanceDescription explaining the gap.
- Each comparable's relevanceDescription must state the SPECIFIC reason for inclusion (e.g. "Similar genre, £18M budget, shot in relevant territories").

WRITING QUALITY:
- executiveSummary.keyInsights must read like a senior consultant's briefing — specific, data-backed, and actionable
- Each territory's reasoning bullets must reference concrete data (rates, amounts, programme names)
- keyAdvantages and keyRisks should be production-specific, not generic boilerplate
- comparables.relevanceDescription should explain WHY this production is comparable (similar setting, genre, budget tier, territory)
- alternativeStrategy should propose a specific multi-territory or alternative approach with clear rationale
"""


class ScriptAnalysisService:
    _STAGE_SCRIPT_CHUNK = "script_chunk"
    _STAGE_SCRIPT_AGGREGATE = "script_aggregate"
    _STAGE_SCRIPT_ANALYSIS = "script_analysis"
    _STAGE_PRODUCTION_ANALYSIS = "production_analysis"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = self._build_client(settings.ANTHROPIC_ANALYSIS_TIMEOUT)

    def _build_client(self, timeout_seconds: int) -> Anthropic:
        return Anthropic(
            api_key=self.settings.ANTHROPIC_API_KEY,
            timeout=float(timeout_seconds),
            max_retries=0,  # We handle retries ourselves in _call_anthropic_with_retry
        )

    def _stage_max_tokens(self, stage: str) -> int:
        legacy = self.settings.ANTHROPIC_MAX_TOKENS
        stage_specific: int | None
        if stage == self._STAGE_SCRIPT_CHUNK:
            stage_specific = getattr(self.settings, "ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK", None)
        elif stage in (self._STAGE_SCRIPT_AGGREGATE, self._STAGE_SCRIPT_ANALYSIS):
            stage_specific = getattr(self.settings, "ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE", None)
        elif stage == self._STAGE_PRODUCTION_ANALYSIS:
            stage_specific = getattr(self.settings, "ANTHROPIC_MAX_TOKENS_REPORT", None)
        else:
            stage_specific = None

        return stage_specific if isinstance(stage_specific, int) and stage_specific > 0 else legacy

    def _stage_timeout(self, stage: str) -> int:
        legacy = self.settings.ANTHROPIC_ANALYSIS_TIMEOUT
        stage_specific: int | None
        if stage == self._STAGE_SCRIPT_CHUNK:
            stage_specific = getattr(self.settings, "ANTHROPIC_TIMEOUT_SCRIPT_CHUNK", None)
        elif stage in (self._STAGE_SCRIPT_AGGREGATE, self._STAGE_SCRIPT_ANALYSIS):
            stage_specific = getattr(self.settings, "ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE", None)
        elif stage == self._STAGE_PRODUCTION_ANALYSIS:
            stage_specific = getattr(self.settings, "ANTHROPIC_TIMEOUT_REPORT", None)
        else:
            stage_specific = None

        return stage_specific if isinstance(stage_specific, int) and stage_specific > 0 else legacy

    def validate_file(self, filename: str, file_size: int) -> tuple[bool, str | None]:
        """Validate script file type and size."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            return False, f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        if file_size > MAX_FILE_SIZE:
            return False, "File too large. Maximum size: 50MB"
        return True, None

    def extract_text_from_pdf(self, file_bytes: bytes) -> str:
        """Extract text from PDF using pdfplumber."""
        import io

        started = perf_counter()
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        combined = "\n".join(text_parts)
        logger.debug(
            "PDF text extraction complete: pages=%s bytes=%s chars=%s elapsed_ms=%s",
            page_count,
            len(file_bytes),
            len(combined),
            int((perf_counter() - started) * 1000),
        )
        return combined

    def extract_text(self, filename: str, file_bytes: bytes) -> str:
        """Extract text from various script formats."""
        ext = filename.rsplit(".", 1)[-1].lower()
        logger.debug(
            "Extracting text from file: filename=%s ext=%s bytes=%s",
            filename,
            ext,
            len(file_bytes),
        )
        if ext == "pdf":
            return self.extract_text_from_pdf(file_bytes)
        return file_bytes.decode("utf-8")

    def analyze(self, script_content: str, script_title: str) -> ScriptAnalysisResult:
        """Analyze script using Anthropic Claude."""
        analysis, _meta = self.analyze_with_meta(script_content, script_title)
        return analysis

    def analyze_with_meta(
        self,
        script_content: str,
        script_title: str,
    ) -> tuple[ScriptAnalysisResult, dict[str, Any]]:
        """Analyze script and return result plus metadata about the analysis path."""
        if not self.settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured")

        chunked_enabled = bool(getattr(self.settings, "SCRIPT_ANALYSIS_CHUNKED_ENABLED", False))
        analysis_meta: dict[str, Any] = {
            "mode": "chunked",
            "chunkedEnabled": chunked_enabled,
            "fallbackUsed": False,
        }
        if not chunked_enabled:
            logger.warning(
                "SCRIPT_ANALYSIS_CHUNKED_ENABLED is false, but legacy hard-trim path is removed; running chunked analysis anyway"
            )

        try:
            result = self._analyze_chunked(script_content, script_title)
            analysis_meta.update(self.extract_analysis_metadata(result.rawResponse))
            analysis_meta.setdefault("mode", "chunked")
            analysis_meta.setdefault("fallbackUsed", False)
            self._emit_script_analysis_metrics(script_title, script_content, analysis_meta)
            return result, analysis_meta
        except Exception as exc:
            analysis_meta.update(
                {
                    "chunkedFailed": True,
                    "chunkedError": str(exc)[:220],
                    "fallbackUsed": True,
                    "reason": "chunked_analysis_failed",
                }
            )
            logger.exception(
                "Chunked script analysis failed, using default fallback: title=%s error=%s",
                script_title,
                exc,
            )
            fallback_result = self._fallback(script_title, reason="chunked_analysis_failed")
            analysis_meta.update(self.extract_analysis_metadata(fallback_result.rawResponse))
            self._emit_script_analysis_metrics(script_title, script_content, analysis_meta)
            return fallback_result, analysis_meta

    def _emit_script_analysis_metrics(
        self,
        script_title: str,
        script_content: str,
        analysis_meta: dict[str, Any],
    ) -> None:
        chunk_telemetry = analysis_meta.get("chunkTelemetry")
        chunk_telemetry = chunk_telemetry if isinstance(chunk_telemetry, dict) else {}
        mode = str(analysis_meta.get("mode", "single_pass"))
        fallback_used = bool(analysis_meta.get("fallbackUsed"))
        if mode.startswith("single"):
            chunk_count = 1
        else:
            chunk_count = chunk_telemetry.get("totalChunks")

        stop_reason = analysis_meta.get("reason")
        if not stop_reason:
            stop_reasons = chunk_telemetry.get("stopReasons")
            if isinstance(stop_reasons, dict) and stop_reasons:
                stop_reason = ",".join([f"{k}:{v}" for k, v in sorted(stop_reasons.items())])
        if not stop_reason:
            stop_reason = "none"

        metrics = {
            "script_chars": len(script_content),
            "estimated_input_tokens": self._estimate_tokens(script_content),
            "chunk_count": chunk_count,
            "dropped_chunks": chunk_telemetry.get("droppedChunks", 0),
            "stop_reason": stop_reason,
            "fallback_used": fallback_used,
            "mode": mode,
        }
        logger.info("Script analysis metrics: title=%s metrics=%s", script_title, metrics)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text.strip()) // CHARS_PER_TOKEN_ESTIMATE) if text.strip() else 0

    @staticmethod
    def extract_analysis_metadata(raw_response: str | None) -> dict[str, Any]:
        """Extract structured metadata from rawResponse when available."""
        if not raw_response:
            return {}

        payload = raw_response.strip()
        if not payload:
            return {}

        parsed: dict[str, Any] | None = None
        if payload.startswith("{") and payload.endswith("}"):
            try:
                loaded = json.loads(payload)
                if isinstance(loaded, dict):
                    parsed = loaded
            except (json.JSONDecodeError, ValueError):
                parsed = None

        if isinstance(parsed, dict):
            metadata_keys = {
                "mode",
                "fallbackUsed",
                "reason",
                "chunkTelemetry",
                "sectionConfidence",
                "overallConfidence",
                "aggregationEvidence",
            }
            if metadata_keys.intersection(parsed.keys()):
                metadata: dict[str, Any] = {}
                for key in [
                    "mode",
                    "fallbackUsed",
                    "reason",
                    "chunkTelemetry",
                    "sectionConfidence",
                    "overallConfidence",
                    "aggregationEvidence",
                ]:
                    if key in parsed:
                        metadata[key] = parsed[key]
                return metadata

        if "fallback analysis used" in payload.lower():
            return {"mode": "single_pass_fallback", "fallbackUsed": True, "reason": "fallback_marker"}

        return {}

    def _analyze_chunked(self, script_content: str, script_title: str) -> ScriptAnalysisResult:
        started = perf_counter()
        chunks, chunk_stats = self._build_script_chunks_with_stats(script_content)
        if not chunks:
            raise ValueError("Script file appears to be empty")

        logger.info(
            "Chunked script analysis started: title=%s input_chars=%s chunk_count=%s dropped_chunks=%s model=%s chunk_tokens=%s chunk_timeout_s=%s",
            script_title,
            len(script_content),
            len(chunks),
            chunk_stats.get("droppedChunks", 0),
            self.settings.ANTHROPIC_MODEL,
            self._stage_max_tokens(self._STAGE_SCRIPT_CHUNK),
            self._stage_timeout(self._STAGE_SCRIPT_CHUNK),
        )

        chunk_results: list[dict[str, Any]] = []
        failed_chunks = 0
        failed_chunk_details: list[dict[str, Any]] = []
        stop_reason_counts: Counter[str] = Counter()
        for idx, chunk_text in enumerate(chunks, start=1):
            try:
                chunk_payload = self._extract_chunk_analysis(chunk_text, idx, len(chunks))
                chunk_payload["_chunkIndex"] = idx
                chunk_results.append(chunk_payload)
            except Exception as exc:
                failed_chunks += 1
                stop_reason = self._infer_stop_reason(exc)
                if stop_reason:
                    stop_reason_counts[stop_reason] += 1
                failed_chunk_details.append(
                    {
                        "chunk": idx,
                        "error": str(exc)[:220],
                        "stopReason": stop_reason or "unknown",
                    }
                )
                logger.warning(
                    "Chunk extraction failed: title=%s chunk=%s/%s chars=%s stop_reason=%s error=%s",
                    script_title,
                    idx,
                    len(chunks),
                    len(chunk_text),
                    stop_reason or "unknown",
                    exc,
                )

        if not chunk_results:
            raise ValueError("Chunk extraction produced no usable results")

        aggregated = self._aggregate_chunk_results(
            chunk_results,
            script_title=script_title,
            total_chunks=len(chunks),
            failed_chunks=failed_chunks,
            failed_chunk_details=failed_chunk_details,
            dropped_chunks=chunk_stats.get("droppedChunks", 0),
            generated_chunks=chunk_stats.get("generatedChunks", len(chunks)),
            stop_reasons=dict(stop_reason_counts),
        )
        if failed_chunks:
            logger.warning(
                "Chunked script analysis had partial failures: title=%s failed_chunks=%s/%s failed_indices=%s",
                script_title,
                failed_chunks,
                len(chunks),
                [detail.get("chunk") for detail in failed_chunk_details][:20],
            )
        logger.info(
            "Chunked script analysis completed: title=%s succeeded_chunks=%s/%s failed_chunks=%s locations=%s budget_range=%s elapsed_ms=%s",
            script_title,
            len(chunk_results),
            len(chunks),
            failed_chunks,
            len(aggregated.locations),
            aggregated.budgetEstimate.range,
            int((perf_counter() - started) * 1000),
        )
        return aggregated

    def _build_script_chunks(self, script_content: str) -> list[str]:
        chunks, _stats = self._build_script_chunks_with_stats(script_content)
        return chunks

    def _build_script_chunks_with_stats(self, script_content: str) -> tuple[list[str], dict[str, int]]:
        clean = script_content.strip()
        if not clean:
            return [], {"generatedChunks": 0, "returnedChunks": 0, "droppedChunks": 0}

        target_tokens = max(int(getattr(self.settings, "SCRIPT_CHUNK_TARGET_TOKENS", 1800) or 1800), 200)
        overlap_tokens = max(int(getattr(self.settings, "SCRIPT_CHUNK_OVERLAP_TOKENS", 200) or 0), 0)
        max_chunks = max(int(getattr(self.settings, "SCRIPT_MAX_CHUNKS", 80) or 80), 1)
        overlap_tokens = min(overlap_tokens, target_tokens // 2)

        target_chars = target_tokens * CHARS_PER_TOKEN_ESTIMATE
        overlap_chars = overlap_tokens * CHARS_PER_TOKEN_ESTIMATE

        scenes = self._split_by_scene_headings(clean)
        packed: list[str] = []
        current = ""
        for scene in scenes:
            if len(scene) > target_chars:
                if current:
                    packed.append(current.strip())
                    current = ""
                packed.extend(self._split_large_block(scene, target_chars))
                continue

            candidate = scene if not current else f"{current}\n\n{scene}"
            if current and len(candidate) > target_chars:
                packed.append(current.strip())
                current = scene
            else:
                current = candidate

        if current:
            packed.append(current.strip())

        if not packed:
            packed = self._split_large_block(clean, target_chars)

        if overlap_chars > 0 and len(packed) > 1:
            overlapped = [packed[0]]
            for idx in range(1, len(packed)):
                tail = packed[idx - 1][-overlap_chars:]
                overlapped.append(f"{tail}\n\n{packed[idx]}".strip())
            packed = overlapped

        generated_chunks = len(packed)
        if len(packed) > max_chunks:
            logger.warning(
                "Chunk count exceeded limit; truncating: chunks=%s max_chunks=%s",
                len(packed),
                max_chunks,
            )
            packed = packed[:max_chunks]
            packed[-1] = packed[-1] + "\n\n[...ADDITIONAL SCRIPT CHUNKS OMITTED DUE TO LIMIT...]"

        final_chunks = [chunk for chunk in packed if chunk]
        dropped_chunks = max(0, generated_chunks - len(final_chunks))
        return final_chunks, {
            "generatedChunks": generated_chunks,
            "returnedChunks": len(final_chunks),
            "droppedChunks": dropped_chunks,
        }

    @staticmethod
    def _split_by_scene_headings(script_text: str) -> list[str]:
        heading_re = re.compile(r"(?m)^(?:\s{0,8})(?:INT\.?|EXT\.?|INT/EXT\.?|I/E\.?|EST\.?)\b")
        matches = list(heading_re.finditer(script_text))
        if not matches:
            return [script_text]

        boundaries = [m.start() for m in matches] + [len(script_text)]
        scenes: list[str] = []
        for idx in range(len(boundaries) - 1):
            chunk = script_text[boundaries[idx]:boundaries[idx + 1]].strip()
            if chunk:
                scenes.append(chunk)
        return scenes or [script_text]

    @staticmethod
    def _split_large_block(text: str, target_chars: int) -> list[str]:
        if len(text) <= target_chars:
            return [text.strip()]

        chunks = [text[idx:idx + target_chars].strip() for idx in range(0, len(text), target_chars)]
        if len(chunks) > 1 and len(chunks[-1]) < max(target_chars // 5, 200):
            chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}".strip()
            chunks.pop()
        return [c for c in chunks if c]

    def _extract_chunk_analysis(self, chunk_text: str, chunk_index: int, total_chunks: int) -> dict[str, Any]:
        prompt_attempts = [chunk_text]
        if len(chunk_text) > 4_000:
            prompt_attempts.append(chunk_text[: int(len(chunk_text) * 0.65)])

        last_error: Exception | None = None
        for prompt_text in prompt_attempts:
            response = self._call_anthropic_with_retry(
                system_prompt=SCRIPT_CHUNK_EXTRACTION_PROMPT,
                user_content=(
                    f"Chunk {chunk_index} of {total_chunks}.\n"
                    f"Estimate frequency relative to this chunk only.\n\n"
                    f"=== CHUNK TEXT START ===\n{prompt_text}\n=== CHUNK TEXT END ==="
                ),
                temperature=0.1,
                stage=self._STAGE_SCRIPT_CHUNK,
                output_config={"format": {"type": "json_schema", "schema": SCRIPT_CHUNK_OUTPUT_SCHEMA}},
            )
            raw = self._extract_text_response(response)
            try:
                return self._parse_json_payload(raw)
            except Exception:
                if getattr(response, "stop_reason", None) == "max_tokens":
                    last_error = ValueError("Chunk extraction output was truncated at max_tokens")
                    continue
                raise

        if last_error:
            raise last_error
        raise ValueError("Chunk extraction failed")

    @staticmethod
    def _infer_stop_reason(exc: Exception) -> str | None:
        message = str(exc).lower()
        if "max_tokens" in message or "truncated" in message:
            return "max_tokens"
        if "timeout" in message or "timed out" in message:
            return "timeout"
        if "rate limit" in message or "429" in message:
            return "rate_limit"
        if "parse" in message or "json" in message:
            return "parse_error"
        return None

    def _aggregate_chunk_results(
        self,
        chunk_results: list[dict[str, Any]],
        *,
        script_title: str,
        total_chunks: int,
        failed_chunks: int,
        failed_chunk_details: list[dict[str, Any]] | None = None,
        dropped_chunks: int = 0,
        generated_chunks: int | None = None,
        stop_reasons: dict[str, int] | None = None,
    ) -> ScriptAnalysisResult:
        location_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        budget_ranges: list[str] = []
        budget_indicators: list[str] = []
        crew_values: list[str] = []
        principal_values: list[str] = []
        supporting_values: list[str] = []
        extras_values: list[str] = []
        shoot_days_values: list[int] = []
        camera_values: list[str] = []
        special_equipment_values: list[str] = []
        vfx_values: list[str] = []
        genre_values: list[str] = []
        format_values: list[str] = []
        tone_values: list[str] = []
        audience_values: list[str] = []
        challenge_notes: list[str] = []
        challenge_flags = {
            "weatherDependent": False,
            "historicalPeriod": False,
            "specialPermits": False,
            "stunts": False,
            "animalWrangling": False,
            "waterWork": False,
            "nightShooting": False,
        }
        challenge_true_counts = {key: 0 for key in challenge_flags.keys()}
        # Signal count accumulators
        ext_scene_total = 0
        int_scene_total = 0
        night_scene_total = 0
        water_scene_total = 0
        vfx_heavy_scene_total = 0
        section_signal_counts = {
            "locations": 0,
            "budget": 0,
            "productionScale": 0,
            "equipment": 0,
            "metadata": 0,
            "challenges": 0,
        }
        failed_chunk_details = failed_chunk_details or []
        used_chunks = len(chunk_results)
        stop_reasons = stop_reasons or {}
        generated_chunks = generated_chunks if isinstance(generated_chunks, int) else total_chunks

        for chunk in chunk_results:
            locations = chunk.get("locations", []) if isinstance(chunk.get("locations"), list) else []
            if locations:
                section_signal_counts["locations"] += 1
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                territory = str(loc.get("territory", "")).strip() or "Unknown"
                name = str(loc.get("name", "")).strip() or territory
                country = str(loc.get("country", "")).strip() or "Unknown"
                frequency = loc.get("frequency", 1)
                if not isinstance(frequency, int):
                    try:
                        frequency = int(frequency)
                    except (TypeError, ValueError):
                        frequency = 1
                frequency = max(1, frequency)
                is_main = bool(loc.get("isMainLocation", False))
                key = (territory.lower(), name.lower(), country.lower())
                existing = location_map.get(key)
                if existing:
                    existing["frequency"] += frequency
                    existing["isMainLocation"] = existing["isMainLocation"] or is_main
                else:
                    location_map[key] = {
                        "name": name,
                        "country": country,
                        "territory": territory,
                        "frequency": frequency,
                        "isMainLocation": is_main,
                    }

            budget = chunk.get("budgetEstimate", {}) if isinstance(chunk.get("budgetEstimate"), dict) else {}
            budget_range = str(budget.get("range", "")).strip().lower()
            if budget_range in BUDGET_BOUNDS_USD:
                budget_ranges.append(budget_range)
                section_signal_counts["budget"] += 1
            indicators = budget.get("indicators", [])
            if isinstance(indicators, list):
                budget_indicators.extend([str(i).strip() for i in indicators if str(i).strip()])

            scale = chunk.get("productionScale", {}) if isinstance(chunk.get("productionScale"), dict) else {}
            crew_value = str(scale.get("crewSize", "")).strip().lower()
            principal_value = str(scale.get("principalCast", "")).strip().lower()
            supporting_value = str(scale.get("supportingCast", "")).strip().lower()
            extras_value = str(scale.get("backgroundExtras", "")).strip().lower()
            crew_values.append(crew_value)
            principal_values.append(principal_value)
            supporting_values.append(supporting_value)
            extras_values.append(extras_value)
            if any(value in SCALE_ORDER for value in (crew_value, principal_value, supporting_value, extras_value)):
                section_signal_counts["productionScale"] += 1
            shoot_days = scale.get("estimatedShootingDays")
            if isinstance(shoot_days, int) and shoot_days > 0:
                shoot_days_values.append(shoot_days)

            equipment = chunk.get("equipment", {}) if isinstance(chunk.get("equipment"), dict) else {}
            camera_value = str(equipment.get("cameraEquipment", "")).strip().lower()
            vfx_value = str(equipment.get("vfxRequirements", "")).strip().lower()
            camera_values.append(camera_value)
            vfx_values.append(vfx_value)
            specials = equipment.get("specialEquipment", [])
            if isinstance(specials, list):
                special_equipment_values.extend([str(item).strip() for item in specials if str(item).strip()])
            if camera_value in CAMERA_OPTIONS or vfx_value in VFX_ORDER or (
                isinstance(specials, list) and len(specials) > 0
            ):
                section_signal_counts["equipment"] += 1

            metadata = chunk.get("metadata", {}) if isinstance(chunk.get("metadata"), dict) else {}
            genres = metadata.get("genres", [])
            if isinstance(genres, list):
                genre_values.extend([str(g).strip() for g in genres if str(g).strip()])
            format_value = str(metadata.get("format", "")).strip().lower()
            format_values.append(format_value)
            tone = str(metadata.get("tone", "")).strip()
            if tone:
                tone_values.append(tone[:180])
            audience = str(metadata.get("targetAudience", "")).strip()
            if audience:
                audience_values.append(audience[:180])
            if (isinstance(genres, list) and len(genres) > 0) or format_value in FORMAT_OPTIONS or tone or audience:
                section_signal_counts["metadata"] += 1

            challenges = chunk.get("challenges", {}) if isinstance(chunk.get("challenges"), dict) else {}
            has_challenge_signal = False
            for key in challenge_flags.keys():
                if bool(challenges.get(key, False)):
                    challenge_true_counts[key] += 1
                    has_challenge_signal = True
            notes = challenges.get("notes", [])
            if isinstance(notes, list):
                challenge_notes.extend([str(n).strip() for n in notes if str(n).strip()])
                has_challenge_signal = has_challenge_signal or bool(notes)
            # Accumulate scene-signal counts
            for total_var, key in (
                (ext_scene_total, "extSceneCount"),
                (int_scene_total, "intSceneCount"),
                (night_scene_total, "nightSceneCount"),
                (water_scene_total, "waterSceneCount"),
                (vfx_heavy_scene_total, "vfxHeavySceneCount"),
            ):
                val = challenges.get(key)
                if isinstance(val, int) and val > 0:
                    if key == "extSceneCount":
                        ext_scene_total += val
                    elif key == "intSceneCount":
                        int_scene_total += val
                    elif key == "nightSceneCount":
                        night_scene_total += val
                    elif key == "waterSceneCount":
                        water_scene_total += val
                    elif key == "vfxHeavySceneCount":
                        vfx_heavy_scene_total += val
            if has_challenge_signal:
                section_signal_counts["challenges"] += 1

        location_rows = sorted(location_map.values(), key=lambda row: row["frequency"], reverse=True)
        if not location_rows:
            location_rows = [
                {
                    "name": "Los Angeles",
                    "country": "United States",
                    "territory": "California (USA)",
                    "frequency": 1,
                    "isMainLocation": True,
                }
            ]

        budget_range = self._choose_mode(budget_ranges, default="medium")
        budget_min, budget_max = BUDGET_BOUNDS_USD.get(budget_range, BUDGET_BOUNDS_USD["medium"])
        for key, true_count in challenge_true_counts.items():
            challenge_flags[key] = true_count >= max(1, int(round(used_chunks * 0.2)))

        section_confidence = {
            section: self._compute_section_confidence(
                signal_chunks=count,
                used_chunks=used_chunks,
                total_chunks=total_chunks,
            )
            for section, count in section_signal_counts.items()
        }
        overall_confidence = round(
            sum(section_confidence.values()) / max(len(section_confidence), 1),
            2,
        )

        production_scale = {
            "crewSize": self._choose_weighted_mode(crew_values, SCALE_ORDER, "medium"),
            "principalCast": self._choose_weighted_mode(principal_values, SCALE_ORDER, "medium"),
            "supportingCast": self._choose_weighted_mode(supporting_values, SCALE_ORDER, "medium"),
            "backgroundExtras": self._choose_weighted_mode(extras_values, SCALE_ORDER, "medium"),
            "estimatedShootingDays": self._trimmed_mean_int(shoot_days_values, default=30),
        }

        camera = self._choose_mode([c for c in camera_values if c in CAMERA_OPTIONS], default="arri")
        vfx = self._choose_weighted_mode(vfx_values, VFX_ORDER, "moderate")
        genres = self._unique_non_empty(genre_values)[:6] or ["Drama"]
        metadata_format = self._choose_mode([f for f in format_values if f in FORMAT_OPTIONS], default="feature")
        evidence_notes = self._build_aggregation_evidence(
            location_rows=location_rows,
            budget_range=budget_range,
            section_signal_counts=section_signal_counts,
            used_chunks=used_chunks,
            challenge_true_counts=challenge_true_counts,
        )

        payload = {
            "locations": location_rows[:20],
            "budgetEstimate": {
                "range": budget_range,
                "minUSD": budget_min,
                "maxUSD": budget_max,
                "confidence": section_confidence["budget"],
                "indicators": self._unique_non_empty(budget_indicators)[:8] or ["Chunked script signal synthesis"],
            },
            "productionScale": production_scale,
            "equipment": {
                "cameraEquipment": camera,
                "specialEquipment": self._unique_non_empty(special_equipment_values)[:12],
                "vfxRequirements": vfx,
            },
            "metadata": {
                "genres": genres,
                "format": metadata_format,
                "tone": self._choose_mode(tone_values, default="Unknown"),
                "targetAudience": self._choose_mode(audience_values, default="General audiences"),
            },
            "challenges": {
                **challenge_flags,
                "notes": self._unique_non_empty(challenge_notes)[:12],
                "extIntRatio": (
                    round(ext_scene_total / (ext_scene_total + int_scene_total), 3)
                    if (ext_scene_total + int_scene_total) > 0
                    else None
                ),
                "nightSceneCount": night_scene_total if night_scene_total > 0 else None,
                "waterSceneCount": water_scene_total if water_scene_total > 0 else None,
                "vfxHeavySceneCount": vfx_heavy_scene_total if vfx_heavy_scene_total > 0 else None,
            },
            "rawResponse": json.dumps(
                {
                    "mode": "chunked",
                    "scriptTitle": script_title,
                    "chunkTelemetry": {
                        "totalChunks": total_chunks,
                        "generatedChunks": generated_chunks,
                        "usedChunks": used_chunks,
                        "failedChunks": failed_chunks,
                        "droppedChunks": dropped_chunks,
                        "successRatio": round(used_chunks / max(total_chunks, 1), 3),
                        "stopReasons": stop_reasons,
                        "failedChunkDetails": failed_chunk_details[:20],
                    },
                    "sectionConfidence": section_confidence,
                    "overallConfidence": overall_confidence,
                    "aggregationEvidence": evidence_notes,
                },
                separators=(",", ":"),
            ),
        }
        return self._sanitize(payload)

    @staticmethod
    def _choose_mode(values: list[str], default: str) -> str:
        cleaned = [value for value in values if value]
        if not cleaned:
            return default
        return Counter(cleaned).most_common(1)[0][0]

    @staticmethod
    def _choose_max_scale(values: list[str], order: dict[str, int], default: str) -> str:
        filtered = [value for value in values if value in order]
        if not filtered:
            return default
        return max(filtered, key=lambda value: order[value])

    @staticmethod
    def _choose_weighted_mode(values: list[str], order: dict[str, int], default: str) -> str:
        filtered = [value for value in values if value in order]
        if not filtered:
            return default
        counts = Counter(filtered)
        return max(
            counts.keys(),
            key=lambda value: (counts[value], order.get(value, 0)),
        )

    @staticmethod
    def _trimmed_mean_int(values: list[int], default: int) -> int:
        if not values:
            return default
        ordered = sorted(v for v in values if isinstance(v, int) and v > 0)
        if not ordered:
            return default
        if len(ordered) > 4:
            ordered = ordered[1:-1]
        return max(1, int(round(sum(ordered) / len(ordered))))

    @staticmethod
    def _compute_section_confidence(*, signal_chunks: int, used_chunks: int, total_chunks: int) -> float:
        if used_chunks <= 0 or total_chunks <= 0:
            return 0.3
        coverage_ratio = signal_chunks / used_chunks
        success_ratio = used_chunks / total_chunks
        confidence = (0.15 + (0.55 * coverage_ratio) + (0.30 * success_ratio)) * (
            0.85 + (0.15 * success_ratio)
        )
        return round(max(0.25, min(0.98, confidence)), 2)

    @staticmethod
    def _build_aggregation_evidence(
        *,
        location_rows: list[dict[str, Any]],
        budget_range: str,
        section_signal_counts: dict[str, int],
        used_chunks: int,
        challenge_true_counts: dict[str, int],
    ) -> list[str]:
        notes: list[str] = []
        if location_rows:
            top = location_rows[:3]
            location_note = ", ".join([f"{loc['territory']} ({loc['frequency']})" for loc in top])
            notes.append(f"Top location frequency signals: {location_note}")
        notes.append(
            "Section coverage: "
            + ", ".join(
                [
                    f"{section} {count}/{max(used_chunks, 1)}"
                    for section, count in section_signal_counts.items()
                ]
            )
        )
        notes.append(f"Budget consensus resolved to '{budget_range}' from chunk-level signals")
        active_challenges = [name for name, count in challenge_true_counts.items() if count > 0]
        if active_challenges:
            notes.append(f"Challenge signals seen: {', '.join(active_challenges)}")
        return notes[:10]

    @staticmethod
    def _unique_non_empty(values: list[str]) -> list[str]:
        seen = set()
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(normalized)
        return result

    def generate_production_analysis(
        self,
        *,
        script_analysis: ScriptAnalysisResult | None,
        request_metadata: dict,
        datasets: dict,
        is_preview: bool,
    ) -> dict:
        """Generate the full ScriptAnalysis JSON from script parse + metadata + datasets."""
        if not self.settings.ANTHROPIC_API_KEY:
            raise ValueError("Anthropic API key is not configured")

        started = perf_counter()
        dataset_counts = {
            key: len(value) if isinstance(value, list) else 0 for key, value in datasets.items()
        }
        compacted_datasets = self._compact_datasets_for_prompt(datasets, is_preview=is_preview)
        compacted_counts = {
            key: len(value) if isinstance(value, list) else 0 for key, value in compacted_datasets.items()
        }
        logger.info(
            "Production analysis started: preview=%s has_script_analysis=%s metadata_keys=%s dataset_counts=%s compacted_counts=%s model=%s max_tokens=%s timeout_s=%s",
            is_preview,
            bool(script_analysis),
            sorted(request_metadata.keys()),
            dataset_counts,
            compacted_counts,
            self.settings.ANTHROPIC_MODEL,
            self._stage_max_tokens(self._STAGE_PRODUCTION_ANALYSIS),
            self._stage_timeout(self._STAGE_PRODUCTION_ANALYSIS),
        )

        # Build user message with all context
        parts = []

        # Project metadata
        parts.append("=== PROJECT METADATA ===")
        parts.append(json.dumps(self._trim_value_for_prompt(request_metadata), default=str, separators=(",", ":")))

        # Script analysis (if available — not for preview)
        if script_analysis:
            parts.append("\n=== SCRIPT ANALYSIS (from script parse) ===")
            script_payload = self._trim_value_for_prompt(script_analysis.model_dump(exclude={"rawResponse"}))
            parts.append(json.dumps(script_payload, default=str, separators=(",", ":")))
        else:
            parts.append("\n=== SCRIPT ANALYSIS ===")
            parts.append("No script provided. Generate analysis from project metadata only.")

        # Reference datasets
        parts.append("\n=== REFERENCE DATASETS ===")
        for key, label in [
            ("incentives", "INCENTIVE PROGRAMS"),
            ("crew_costs", "CREW COST BENCHMARKS"),
            ("cast_costs", "CAST COST BENCHMARKS"),
            ("comparables", "COMPARABLE PRODUCTIONS"),
            ("grants", "GRANT OPPORTUNITIES"),
            ("festivals", "FILM FESTIVALS"),
        ]:
            data = compacted_datasets.get(key, [])
            parts.append(f"\n{label} ({len(data)} records in prompt):")
            if data:
                parts.append(json.dumps(data, default=str, separators=(",", ":")))
            else:
                parts.append("No data available.")

        # Territory weather data (only include months relevant to the shoot window)
        shoot_months: list[int] | None = datasets.get("_shoot_months")
        weather_data = compacted_datasets.get("weather", [])
        if weather_data:
            if shoot_months:
                filtered_weather = [w for w in weather_data if w.get("month") in shoot_months]
            else:
                filtered_weather = weather_data
            parts.append(f"\nTERRITORY WEATHER DATA ({len(filtered_weather)} records):")
            if filtered_weather:
                parts.append(json.dumps(filtered_weather, default=str, separators=(",", ":")))
            else:
                parts.append("No weather data available for the specified shoot months.")

        # Derived: shoot window
        shoot_window = datasets.get("_shoot_window")
        if shoot_window:
            parts.append("\n=== DERIVED: SHOOT WINDOW ===")
            parts.append(json.dumps(shoot_window, default=str, separators=(",", ":")))
        else:
            parts.append("\n=== DERIVED: SHOOT WINDOW ===")
            parts.append('{"note":"No filming_start_date provided — use annual average weather data."}')

        # Derived: scene exposure profile
        ext_int_ratio: float | None = datasets.get("_ext_int_ratio")
        if ext_int_ratio is not None:
            weather_exposure = (
                "high" if ext_int_ratio >= 0.7 else
                "medium" if ext_int_ratio >= 0.4 else
                "low"
            )
            night_count = None
            water_count = None
            if script_analysis is not None:
                challenges = getattr(script_analysis, "challenges", None)
                if challenges is not None:
                    night_count = getattr(challenges, "nightSceneCount", None)
                    water_count = getattr(challenges, "waterSceneCount", None)
            parts.append("\n=== DERIVED: SCENE EXPOSURE PROFILE ===")
            parts.append(json.dumps({
                "extIntRatio": ext_int_ratio,
                "exteriorPercentage": f"{ext_int_ratio * 100:.0f}%",
                "weatherExposureLevel": weather_exposure,
                "nightSceneCount": night_count,
                "waterSceneCount": water_count,
            }, separators=(",", ":")))

        # Derived: producer eligibility
        producer_country = datasets.get("_producer_country")
        co_production_status = datasets.get("_co_production_status")
        if producer_country or co_production_status:
            parts.append("\n=== DERIVED: PRODUCER ELIGIBILITY CONTEXT ===")
            parts.append(json.dumps({
                "producerCountry": producer_country,
                "coProductionStatus": co_production_status,
            }, separators=(",", ":")))


        # Mode instruction
        if is_preview:
            parts.append("\n=== MODE: PREVIEW ===")
            parts.append(
                "This is a FREE PREVIEW report. Return exactly 3 locationRankings with "
                "isAssessmentOnly: true. Return incentiveEstimates for those 3 territories. "
                "Return empty arrays [] for crewInsights, comparables, weatherLogistics, "
                "and fundingOpportunities."
            )
        else:
            parts.append("\n=== MODE: FULL PAID REPORT ===")
            parts.append(
                "This is a FULL PAID report. Return up to 15 locationRankings with "
                "isAssessmentOnly: false. Populate ALL sections with complete data."
            )

        user_message = "\n".join(parts)
        stage_max_tokens = self._stage_max_tokens(self._STAGE_PRODUCTION_ANALYSIS)
        stage_timeout = self._stage_timeout(self._STAGE_PRODUCTION_ANALYSIS)
        logger.info(
            "Production analysis prompt prepared: preview=%s prompt_chars=%s "
            "system_chars=%s max_tokens=%s timeout_s=%s model=%s",
            is_preview,
            len(user_message),
            len(PRODUCTION_ANALYSIS_PROMPT),
            stage_max_tokens,
            stage_timeout,
            self.settings.ANTHROPIC_MODEL,
        )

        try:
            response = self._call_anthropic_with_retry(
                system_prompt=PRODUCTION_ANALYSIS_PROMPT,
                user_content=user_message,
                temperature=0.2,
                stage=self._STAGE_PRODUCTION_ANALYSIS,
            )
        except Exception as api_err:
            logger.exception(
                "Production analysis API call failed: preview=%s elapsed_ms=%s error=%s",
                is_preview,
                int((perf_counter() - started) * 1000),
                api_err,
            )
            logger.warning("Using fallback production analysis (API error): preview=%s", is_preview)
            return self._fallback_analysis(request_metadata, is_preview)

        # Inspect stop reason for truncation
        stop_reason = getattr(response, "stop_reason", None)
        usage = getattr(response, "usage", None)
        if stop_reason == "max_tokens":
            logger.warning(
                "Production analysis hit max_tokens (output truncated): preview=%s "
                "input_tokens=%s output_tokens=%s stop_reason=%s",
                is_preview,
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
                stop_reason,
            )
        else:
            logger.info(
                "Production analysis API returned: preview=%s stop_reason=%s "
                "input_tokens=%s output_tokens=%s",
                is_preview,
                stop_reason,
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
            )

        raw = self._extract_text_response(response)
        try:
            data = self._parse_json_payload(raw)
            sanitized = self._sanitize_analysis(data, is_preview)
            sanitized, _val_warnings = ReportValidator.validate(sanitized, datasets)
            logger.info(
                "Production analysis completed: preview=%s location_rankings=%s incentives=%s elapsed_ms=%s",
                is_preview,
                len(sanitized.get("locationRankings", [])),
                len(sanitized.get("incentiveEstimates", [])),
                int((perf_counter() - started) * 1000),
            )
            return sanitized
        except (json.JSONDecodeError, ValueError) as parse_err:
            # If truncated (max_tokens), try to recover partial JSON
            if stop_reason == "max_tokens":
                logger.warning(
                    "Attempting truncated JSON recovery: raw_chars=%s",
                    len(raw),
                )
                recovered = self._recover_truncated_json(raw)
                if recovered is not None:
                    sanitized = self._sanitize_analysis(recovered, is_preview)
                    sanitized, _val_warnings = ReportValidator.validate(sanitized, datasets)
                    logger.info(
                        "Truncated JSON recovery succeeded: preview=%s "
                        "location_rankings=%s elapsed_ms=%s",
                        is_preview,
                        len(sanitized.get("locationRankings", [])),
                        int((perf_counter() - started) * 1000),
                    )
                    return sanitized
                logger.warning("Truncated JSON recovery failed")

            raw_preview = raw[:500].replace("\n", " ") if raw else "(empty)"
            logger.error(
                "Failed to parse production analysis JSON: preview=%s raw_chars=%s "
                "stop_reason=%s elapsed_ms=%s error=%s raw_preview=%s",
                is_preview,
                len(raw),
                stop_reason,
                int((perf_counter() - started) * 1000),
                parse_err,
                raw_preview,
            )
            logger.warning("Using fallback production analysis (parse error): preview=%s", is_preview)
            return self._fallback_analysis(request_metadata, is_preview)
        except Exception as e:
            logger.exception(
                "Unexpected error in production analysis post-processing: preview=%s elapsed_ms=%s error=%s",
                is_preview,
                int((perf_counter() - started) * 1000),
                e,
            )
            logger.warning("Using fallback production analysis (unexpected error): preview=%s", is_preview)
            return self._fallback_analysis(request_metadata, is_preview)

    def _compact_datasets_for_prompt(self, datasets: dict, *, is_preview: bool) -> dict:
        preview_caps = {
            "incentives": 12,
            "crew_costs": 40,
            "comparables": 8,
            "grants": 10,
            "festivals": 10,
            "weather": 60,
        }
        paid_caps = {
            "incentives": 40,
            "crew_costs": 120,
            "comparables": 25,
            "grants": 25,
            "festivals": 25,
            "weather": 180,
        }
        field_whitelist = {
            "incentives": [
                "territory",
                "program",
                "program_name",
                "program_type",
                "rate",
                "rate_gross",
                "rate_net",
                "rate_type",
                "rate_tier_json",
                "cap",
                "cap_amount",
                "cap_currency",
                "qualifying_spend",
                "qualifying_spend_min",
                "qualifying_spend_cap_pct",
                "qualifying_spend_currency",
                "payment_timeline_days_min",
                "payment_timeline_days_max",
                "payment_timeline_notes",
                "eligibility_rules_json",
                "currency",
                "warnings_json",
                "expiry_date",
                "stackable",
                "source_name",
                "last_verified_at",
                "last_updated",
                "data_freshness_days",
                # Regional / stacking fields
                "scope",
                "parent_territory",
                "stacking_group",
                "stackable_with",
                # Nationality / eligibility fields
                "nationality_requirements",
                "co_production_eligible",
                "co_production_treaties",
                "spv_eligible",
                # Rate correction fields (guide v1.0)
                "vfx_uplift_pct",
                "programme_level",
                "eligibility_notes",
            ],
            "weather": [
                "territory",
                "month",
                "avg_temp_high_c",
                "avg_temp_low_c",
                "avg_rainfall_mm",
                "avg_daylight_hours",
                "storm_risk",
                "weather_notes",
                "exterior_shoot_score",
            ],
            "crew_costs": [
                "country",
                "region",
                "role",
                "role_category",
                "department",
                "union_rate_cents",
                "non_union_rate_cents",
                "union_rate_gbp",
                "non_union_rate_gbp",
                "rate_currency",
                "fx_rate",
                "fx_date",
                "fringe_rate_pct",
                "fringe_description",
                "source_name",
                "source_type",
                "confidence_score",
                "notes",
            ],
            "cast_costs": [
                "country",
                "region",
                "role",
                "role_category",
                "department",
                "union_rate_cents",
                "non_union_rate_cents",
                "union_rate_gbp",
                "non_union_rate_gbp",
                "rate_currency",
                "fx_rate",
                "fx_date",
                "fringe_rate_pct",
                "fringe_description",
                "source_name",
                "source_type",
                "confidence_score",
                "notes",
            ],
            "comparables": [
                "title",
                "year",
                "genre",
                "budget_usd",
                "primary_territory",
                "incentive_used",
                "production_company",
                "director",
                "source",
            ],
            "grants": [
                "title",
                "territory",
                "funding_body",
                "max_amount",
                "currency",
                "status",
                "application_deadline",
                "eligibility",
                "website_url",
                "data_source",
            ],
            "festivals": [
                "name",
                "location",
                "year",
                "genres",
                "budget_tiers",
                "festival_dates",
                "premiere_requirement",
                "tier",
                "acceptance_rate",
                "deadlines",
                "submission_deadline",
                "website_url",
                "filmfreeway_url",
                "notable_alumni",
                "average_budget_of_accepted_films",
                "notes",
                "current_status",
            ],
        }
        caps = preview_caps if is_preview else paid_caps
        compacted: dict[str, list] = {}
        for key, value in datasets.items():
            # Skip private derived-data keys (prefixed with _) and non-list values
            if key.startswith("_") or not isinstance(value, list):
                continue
            rows = value
            limited = rows[: caps.get(key, 10)]
            allowed_fields = field_whitelist.get(key)
            compacted_rows = []
            for row in limited:
                if not isinstance(row, dict):
                    compacted_rows.append(self._trim_value_for_prompt(row))
                    continue
                if allowed_fields:
                    slim = {field: row.get(field) for field in allowed_fields if field in row}
                else:
                    slim = row
                compacted_rows.append(self._trim_value_for_prompt(slim))
            compacted[key] = compacted_rows
        return compacted

    def _trim_value_for_prompt(self, value: Any) -> Any:
        if isinstance(value, str):
            if len(value) <= MAX_PROMPT_TEXT_CHARS:
                return value
            return value[:MAX_PROMPT_TEXT_CHARS] + "..."
        if isinstance(value, list):
            return [self._trim_value_for_prompt(item) for item in value[:MAX_PROMPT_LIST_ITEMS]]
        if isinstance(value, dict):
            result = {}
            for idx, (k, v) in enumerate(value.items()):
                if idx >= 30:
                    break
                result[k] = self._trim_value_for_prompt(v)
            return result
        return value

    def _call_anthropic_with_retry(
        self,
        *,
        system_prompt: str,
        user_content: str,
        temperature: float,
        stage: str,
        output_config: dict[str, Any] | None = None,
    ):
        retry_delays = [8, 20]
        max_attempts = len(retry_delays) + 1
        stage_max_tokens = self._stage_max_tokens(stage)
        stage_timeout = self._stage_timeout(stage)
        for attempt in range(1, max_attempts + 1):
            try:
                client = self._build_client(stage_timeout)
                request_payload: dict[str, Any] = {
                    "model": self.settings.ANTHROPIC_MODEL,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_content}],
                    "temperature": temperature,
                    "max_tokens": stage_max_tokens,
                }
                if output_config:
                    request_payload["output_config"] = output_config
                return client.messages.create(
                    **request_payload,
                )
            except Exception as exc:
                if not self._is_rate_limit_error(exc) or attempt >= max_attempts:
                    raise
                delay = retry_delays[attempt - 1]
                logger.warning(
                    "Anthropic rate limit at stage=%s attempt=%s/%s, retrying in %ss (max_tokens=%s timeout_s=%s)",
                    stage,
                    attempt,
                    max_attempts,
                    delay,
                    stage_max_tokens,
                    stage_timeout,
                )
                sleep(delay)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "rate_limit_error" in message
            or "rate limit" in message
            or "429" in message
            or "input tokens per minute" in message
        )

    @staticmethod
    def _recover_truncated_json(raw: str) -> dict[str, Any] | None:
        """Attempt to recover a valid JSON object from truncated LLM output.

        When stop_reason is max_tokens, the JSON is valid up to the truncation
        point. We close all open brackets/braces and try to parse.
        """
        text = raw.strip()
        # Strip markdown fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)

        # Find the start of the JSON object
        start = text.find("{")
        if start == -1:
            return None
        text = text[start:]

        # Remove any trailing incomplete string value (e.g. truncated mid-string)
        # Find the last complete line that ends with a JSON-valid token
        lines = text.rstrip().split("\n")
        while lines:
            last = lines[-1].rstrip()
            # If line ends with a valid JSON boundary, keep it
            if last and last[-1] in '",}]0123456789':
                break
            # If line ends with true/false/null
            if last.rstrip().endswith(("true", "false", "null")):
                break
            lines.pop()

        if not lines:
            return None

        text = "\n".join(lines)

        # Remove trailing comma
        text = text.rstrip().rstrip(",")

        # Count open brackets and close them
        open_braces = 0
        open_brackets = 0
        in_string = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                open_braces += 1
            elif ch == "}":
                open_braces -= 1
            elif ch == "[":
                open_brackets += 1
            elif ch == "]":
                open_brackets -= 1

        # If we're inside an unterminated string, close it
        if in_string:
            text += '"'

        # Close open brackets/braces
        text += "]" * max(0, open_brackets)
        text += "}" * max(0, open_braces)

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                logger.info("Truncated JSON recovery: parsed successfully with %d top-level keys", len(data))
                return data
        except (json.JSONDecodeError, ValueError):
            # Try with repair
            repaired = ScriptAnalysisService._repair_json(text)
            try:
                data = json.loads(repaired)
                if isinstance(data, dict):
                    logger.info("Truncated JSON recovery: parsed after repair with %d top-level keys", len(data))
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    @staticmethod
    def _repair_json(text: str) -> str:
        """Best-effort repair of common LLM JSON mistakes."""
        # Remove trailing commas before } or ]
        text = re.sub(r",\s*([}\]])", r"\1", text)
        # Insert missing commas between a value and the next key:
        #   "value"\n  "nextKey"  or  "value"  "nextKey"
        #   number/bool/null\n  "nextKey"
        text = re.sub(
            r'(?<=["\d\w])\s*\n(\s*")', r',\n\1', text
        )
        # Fix: true/false/null followed by "key" on next line (the lookbehind above
        # catches most, but be explicit for end-of-word boundaries)
        text = re.sub(
            r'(true|false|null)\s*\n(\s*")', r'\1,\n\2', text
        )
        # Fix missing comma after ] or } followed by "key"
        text = re.sub(r'([}\]])\s*\n(\s*")', r'\1,\n\2', text)
        return text

    @staticmethod
    def _parse_json_payload(raw: str) -> dict[str, Any]:
        payload = raw.strip()

        # Handle fenced JSON responses: ```json ... ```
        if payload.startswith("```"):
            payload = re.sub(r"^```(?:json)?\s*", "", payload, flags=re.IGNORECASE)
            payload = re.sub(r"\s*```$", "", payload)
            payload = payload.strip()

        def _try_load(text: str) -> dict[str, Any] | None:
            try:
                data = json.loads(text)
                return data if isinstance(data, dict) else None
            except (json.JSONDecodeError, ValueError):
                return None

        # 1. Try raw payload as-is
        result = _try_load(payload)
        if result is not None:
            return result

        # 2. Extract first JSON object block from mixed text
        match = re.search(r"\{[\s\S]*\}", payload)
        if match:
            result = _try_load(match.group(0))
            if result is not None:
                return result

            # 3. Try repairing extracted JSON
            repaired = ScriptAnalysisService._repair_json(match.group(0))
            result = _try_load(repaired)
            if result is not None:
                logger.warning("JSON required repair to parse successfully")
                return result

        preview = payload[:220].replace("\n", " ")
        raise ValueError(f"No valid JSON object found in model response: {preview}")

    def _extract_text_response(self, response) -> str:
        """Extract plain text from Anthropic messages API response blocks."""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        logger.error(
            "Anthropic response had no text block: block_types=%s",
            [getattr(block, "type", None) for block in getattr(response, "content", [])],
        )
        raise ValueError("Anthropic returned no text content")

    def _sanitize_analysis(self, data: dict, is_preview: bool) -> dict:
        """Validate and fill defaults for production analysis AI response."""
        result = {
            "genre": data.get("genre", "Drama"),
            "tone": data.get("tone", "Unknown"),
            "scale": data.get("scale", "Unknown"),
            "complexity": data.get("complexity", "Medium"),
            "locationRankings": [],
            "incentiveEstimates": [],
            "crewInsights": [] if is_preview else data.get("crewInsights", []),
            "comparables": [] if is_preview else data.get("comparables", []),
            "weatherLogistics": [] if is_preview else data.get("weatherLogistics", []),
            "fundingOpportunities": [] if is_preview else data.get("fundingOpportunities", []),
        }

        # Validate complexity enum
        if result["complexity"] not in ("Low", "Medium", "High", "Very High"):
            result["complexity"] = "Medium"

        # Sanitize location rankings
        for loc in data.get("locationRankings", []):
            sanitized_loc = {
                "name": loc.get("name", "Unknown"),
                "country": loc.get("country", "Unknown"),
                "score": max(0, min(100, loc.get("score", 50))),
                "costEfficiency": max(0, min(100, loc.get("costEfficiency", 50))),
                "crewDepth": max(0, min(100, loc.get("crewDepth", 50))),
                "infrastructure": max(0, min(100, loc.get("infrastructure", 50))),
                "incentiveStrength": max(0, min(100, loc.get("incentiveStrength", 50))),
                "currencyAdvantage": max(0, min(100, loc.get("currencyAdvantage", 50))),
                "reasoning": loc.get("reasoning", ["No detailed reasoning available"]),
                "isAssessmentOnly": True if is_preview else loc.get("isAssessmentOnly", False),
            }
            # New optional fields
            for field in (
                "rebatePercent", "rebateAmount", "culturalTestLikelihood",
                "adminComplexity", "paymentSpeed",
                "weatherRiskImpact",  # New: weather score penalty
            ):
                if loc.get(field) is not None:
                    sanitized_loc[field] = loc[field]
            for list_field in ("keyAdvantages", "keyRisks"):
                if loc.get(list_field) and isinstance(loc[list_field], list):
                    sanitized_loc[list_field] = loc[list_field]
            result["locationRankings"].append(sanitized_loc)

        # Sanitize incentive estimates
        for inc in data.get("incentiveEstimates", []):
            sanitized_inc = {
                "territory": inc.get("territory", "Unknown"),
                "program": inc.get("program", "Unknown Program"),
                "rate": inc.get("rate", "N/A"),
                "cap": inc.get("cap", "N/A"),
                "qualifyingSpend": inc.get("qualifyingSpend", "N/A"),
                "estimatedRebate": inc.get("estimatedRebate", "N/A"),
                "requirements": inc.get("requirements", []),
                "disclaimer": "Estimate only. Final eligibility depends on official approval.",
                "dataSource": "Prodculator backend datasets",
                "lastUpdated": inc.get("lastUpdated", ""),
            }
            # Preserve new regional/stacking/eligibility fields
            for field in (
                "scope", "parentTerritory", "stackableWith", "stackingNote",
                "eligibilityStatus", "eligibilityNote",
            ):
                if inc.get(field) is not None:
                    sanitized_inc[field] = inc[field]
            result["incentiveEstimates"].append(sanitized_inc)

        # Executive summary
        exec_summary = data.get("executiveSummary")
        if isinstance(exec_summary, dict) and exec_summary.get("keyInsights"):
            sanitized_exec: dict = {
                "keyInsights": exec_summary.get("keyInsights", ""),
                "recommendedTerritory": exec_summary.get("recommendedTerritory", ""),
                "recommendedTerritoryScore": max(0, min(100, exec_summary.get("recommendedTerritoryScore", 0))),
                "recommendedTerritoryRebate": exec_summary.get("recommendedTerritoryRebate"),
                "recommendedTerritoryInfrastructure": exec_summary.get("recommendedTerritoryInfrastructure"),
                "recommendedTerritoryPaymentSpeed": exec_summary.get("recommendedTerritoryPaymentSpeed"),
                "shootDays": exec_summary.get("shootDays"),
                "budget": exec_summary.get("budget"),
                "budgetRange": exec_summary.get("budgetRange"),
                "primaryLocations": exec_summary.get("primaryLocations", []),
            }
            # Preserve new shootWindow field
            shoot_window = exec_summary.get("shootWindow")
            if isinstance(shoot_window, dict) and shoot_window.get("months"):
                sanitized_exec["shootWindow"] = shoot_window
            result["executiveSummary"] = sanitized_exec

        # Financial analysis (paid only)
        if not is_preview:
            fin = data.get("financialAnalysis")
            if isinstance(fin, dict):
                result["financialAnalysis"] = {
                    "budgetScenarios": fin.get("budgetScenarios", []),
                    "crewCostComparison": fin.get("crewCostComparison", []),
                }

        # Territory deep dives (paid only)
        if not is_preview:
            deep_dives = data.get("territoryDeepDives", [])
            if isinstance(deep_dives, list):
                result["territoryDeepDives"] = deep_dives[:5]

        # Alternative strategy
        alt_strategy = data.get("alternativeStrategy")
        if isinstance(alt_strategy, str) and alt_strategy.strip():
            result["alternativeStrategy"] = alt_strategy

        # Scoring methodology — always include so the user understands the ratings
        scoring = data.get("scoringMethodology")
        if isinstance(scoring, dict) and scoring.get("dimensions"):
            result["scoringMethodology"] = scoring
        else:
            result["scoringMethodology"] = self._default_scoring_methodology()

        # Enforce preview limits
        if is_preview:
            result["locationRankings"] = result["locationRankings"][:3]

        return result

    @staticmethod
    def _default_scoring_methodology() -> dict:
        """Return a static scoring-methodology block that explains how scores work."""
        return {
            "overview": (
                "Each territory is scored out of 100 based on five weighted "
                "dimensions. The overall score is a weighted average that "
                "reflects the production's stated priorities."
            ),
            "dimensions": [
                {
                    "name": "Cost Efficiency",
                    "key": "costEfficiency",
                    "description": (
                        "Measures how far your budget stretches in this territory "
                        "— crew day-rates, stage hire, equipment rental, and "
                        "general cost of living relative to comparable markets."
                    ),
                },
                {
                    "name": "Crew Depth",
                    "key": "crewDepth",
                    "description": (
                        "Availability of experienced, English-speaking crew across "
                        "all key departments — camera, grip, electric, art, VFX, "
                        "and post-production."
                    ),
                },
                {
                    "name": "Infrastructure",
                    "key": "infrastructure",
                    "description": (
                        "Quality and capacity of studio stages, post-production "
                        "facilities, equipment houses, and supporting logistics "
                        "such as transport and accommodation."
                    ),
                },
                {
                    "name": "Incentive Strength",
                    "key": "incentiveStrength",
                    "description": (
                        "Value of available tax credits, rebates, and grants — "
                        "factoring in the rebate percentage, spend caps, "
                        "qualification complexity, and typical payment timelines."
                    ),
                },
                {
                    "name": "Currency Advantage",
                    "key": "currencyAdvantage",
                    "description": (
                        "Current and forecasted exchange-rate benefit when "
                        "spending in local currency versus your home currency, "
                        "including hedging considerations."
                    ),
                },
            ],
            "weightingNote": (
                "Dimension weights are adjusted to match the production priority "
                "you selected. 'Incentive-first' emphasises incentive strength "
                "(40 %), 'Location-first' emphasises crew depth and infrastructure "
                "(25 % each), and 'Full analysis' weights all five dimensions "
                "equally (20 % each)."
            ),
            "colorKey": {
                "green": "Score ≥ 70 — strong fit",
                "gold": "Score 40–69 — moderate fit, review trade-offs",
                "red": "Score ≤ 39 — potential challenges, proceed with caution",
            },
        }

    def _fallback_analysis(self, request_metadata: dict, is_preview: bool) -> dict:
        """Return fallback analysis when production analysis API call fails."""
        genre = (request_metadata.get("genre") or ["Drama"])[0]
        country = request_metadata.get("country", "UK")
        budget_range = request_metadata.get("budget_range", "Unknown")

        # Build location rankings from territories_considering if available
        territories = request_metadata.get("territories_considering") or []
        location_rankings = []
        if territories:
            for idx, territory in enumerate(territories[:5]):
                location_rankings.append({
                    "name": territory,
                    "country": territory,
                    "score": max(40, 70 - idx * 5),
                    "costEfficiency": 50,
                    "crewDepth": 50,
                    "infrastructure": 50,
                    "incentiveStrength": 50,
                    "currencyAdvantage": 50,
                    "reasoning": [
                        "Territory selected from user preferences",
                        "Full AI analysis was unavailable — scores are estimated defaults",
                    ],
                    "isAssessmentOnly": is_preview,
                })
        if not location_rankings:
            location_rankings.append({
                "name": country,
                "country": country,
                "score": 60,
                "costEfficiency": 50,
                "crewDepth": 60,
                "infrastructure": 60,
                "incentiveStrength": 50,
                "currencyAdvantage": 50,
                "reasoning": [
                    "Default territory based on project country",
                    "Full AI analysis was unavailable — scores are estimated defaults",
                ],
                "isAssessmentOnly": is_preview,
            })

        return {
            "_fallbackUsed": True,
            "genre": genre,
            "tone": "Pending analysis",
            "scale": f"{budget_range} production",
            "complexity": "Medium",
            "executiveSummary": {
                "keyInsights": (
                    f"AI analysis was temporarily unavailable for this {genre.lower()} "
                    f"{budget_range} production. The report below uses estimated defaults "
                    f"based on project metadata. We recommend regenerating this report for "
                    f"full territory analysis, financial breakdowns, and crew cost comparisons."
                ),
                "recommendedTerritory": location_rankings[0]["name"],
                "recommendedTerritoryScore": location_rankings[0]["score"],
                "recommendedTerritoryRebate": None,
                "recommendedTerritoryInfrastructure": None,
                "recommendedTerritoryPaymentSpeed": None,
                "shootDays": None,
                "budget": None,
                "budgetRange": budget_range,
                "primaryLocations": [],
            },
            "locationRankings": location_rankings,
            "incentiveEstimates": [],
            "crewInsights": [],
            "comparables": [],
            "weatherLogistics": [],
            "fundingOpportunities": [],
            "scoringMethodology": self._default_scoring_methodology(),
        }

    def _sanitize(self, data: dict[str, Any]) -> ScriptAnalysisResult:
        """Validate and fill defaults for AI response."""
        locations_raw = data.get("locations", [])
        budget_raw = data.get("budgetEstimate", {})
        scale_raw = data.get("productionScale", {})
        equipment_raw = data.get("equipment", {})
        metadata_raw = data.get("metadata", {})
        challenges_raw = data.get("challenges", {})

        if not isinstance(locations_raw, list):
            locations_raw = []
        if not isinstance(budget_raw, dict):
            budget_raw = {}
        if not isinstance(scale_raw, dict):
            scale_raw = {}
        if not isinstance(equipment_raw, dict):
            equipment_raw = {}
        if not isinstance(metadata_raw, dict):
            metadata_raw = {}
        if not isinstance(challenges_raw, dict):
            challenges_raw = {}

        locations = [
            Location.model_validate(loc)
            for loc in locations_raw
            if isinstance(loc, dict)
        ]

        return ScriptAnalysisResult(
            locations=locations,
            budgetEstimate=BudgetEstimate(
                range=budget_raw.get("range", "medium"),
                minUSD=budget_raw.get("minUSD", 5_000_000),
                maxUSD=budget_raw.get("maxUSD", 30_000_000),
                confidence=budget_raw.get("confidence", 0.7),
                indicators=budget_raw.get("indicators", ["General industry estimates"]),
            ),
            productionScale=ProductionScale(
                crewSize=scale_raw.get("crewSize", "medium"),
                principalCast=scale_raw.get("principalCast", "medium"),
                supportingCast=scale_raw.get("supportingCast", "medium"),
                backgroundExtras=scale_raw.get("backgroundExtras", "medium"),
                estimatedShootingDays=scale_raw.get("estimatedShootingDays", 30),
            ),
            equipment=Equipment(
                cameraEquipment=equipment_raw.get("cameraEquipment", "arri"),
                specialEquipment=equipment_raw.get("specialEquipment", []),
                vfxRequirements=equipment_raw.get("vfxRequirements", "moderate"),
            ),
            metadata=Metadata(
                genres=metadata_raw.get("genres", ["Drama"]),
                format=metadata_raw.get("format", "feature"),
                tone=metadata_raw.get("tone", "Unknown"),
                targetAudience=metadata_raw.get("targetAudience", "General audiences"),
            ),
            challenges=Challenges(
                weatherDependent=challenges_raw.get("weatherDependent", False),
                historicalPeriod=challenges_raw.get("historicalPeriod", False),
                specialPermits=challenges_raw.get("specialPermits", False),
                stunts=challenges_raw.get("stunts", False),
                animalWrangling=challenges_raw.get("animalWrangling", False),
                waterWork=challenges_raw.get("waterWork", False),
                nightShooting=challenges_raw.get("nightShooting", False),
                notes=challenges_raw.get("notes", []),
            ),
            rawResponse=data.get("rawResponse"),
        )

    def _fallback(self, script_title: str, reason: str = "script_analysis_error") -> ScriptAnalysisResult:
        """Return fallback analysis when API fails."""
        return ScriptAnalysisResult(
            locations=[
                Location(
                    name="Los Angeles",
                    country="United States",
                    territory="California (USA)",
                    frequency=10,
                    isMainLocation=True,
                )
            ],
            budgetEstimate=BudgetEstimate(
                range="medium",
                minUSD=5_000_000,
                maxUSD=30_000_000,
                confidence=0.5,
                indicators=["Estimated based on typical feature film budget"],
            ),
            productionScale=ProductionScale(
                crewSize="medium",
                principalCast="medium",
                supportingCast="medium",
                backgroundExtras="medium",
                estimatedShootingDays=30,
            ),
            equipment=Equipment(
                cameraEquipment="arri",
                specialEquipment=[],
                vfxRequirements="moderate",
            ),
            metadata=Metadata(
                genres=["Drama"],
                format="feature",
                tone="Unknown",
                targetAudience="General audiences",
            ),
            challenges=Challenges(
                weatherDependent=False,
                historicalPeriod=False,
                specialPermits=False,
                stunts=False,
                animalWrangling=False,
                waterWork=False,
                nightShooting=False,
                notes=["Analysis failed - using default estimates"],
            ),
            rawResponse=json.dumps(
                {
                    "mode": "single_pass_fallback",
                    "fallbackUsed": True,
                    "reason": reason,
                    "scriptTitle": script_title,
                },
                separators=(",", ":"),
            ),
        )
