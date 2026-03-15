# Scriptelligence Report Gaps — Implementation Guide

**Date:** 2026-03-13  
**Branch:** `feat/s3-storage`  
**Status:** Pre-implementation audit & plan  
**Author:** Engineering

---

## Table of Contents

1. [Current State Audit](#1-current-state-audit)
2. [Gap Analysis (5 Gaps)](#2-gap-analysis)
3. [Gap 1: Regional Incentive Stacking](#3-gap-1-regional-incentive-stacking)
4. [Gap 2: Weather–Schedule Integration](#4-gap-2-weatherschedule-integration)
5. [Gap 3: Shoot Date Input Activation](#5-gap-3-shoot-date-input-activation)
6. [Gap 4: Ext/Int Ratio → Weather Risk Wiring](#6-gap-4-extint-ratio--weather-risk-wiring)
7. [Gap 5: Producer Nationality / Eligibility Logic](#7-gap-5-producer-nationality--eligibility-logic)
8. [Implementation Order & Dependencies](#8-implementation-order--dependencies)
9. [Database Migrations Required](#9-database-migrations-required)
10. [Prompt Engineering Changes](#10-prompt-engineering-changes)
11. [Validator Changes](#11-validator-changes)
12. [Schema Changes](#12-schema-changes)
13. [Testing Strategy](#13-testing-strategy)

---

## 1. Current State Audit

### What the report does today

| Component                | Status    | File(s)                                                                                         | Notes                                                                                                                                                                                               |
| ------------------------ | --------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Territory scoring matrix | ✅ Works  | `scripts/service.py` `PRODUCTION_ANALYSIS_PROMPT`                                               | 5 sub-scores (costEfficiency, crewDepth, infrastructure, incentiveStrength, currencyAdvantage) weighted by `production_priority`.                                                                   |
| Tax incentive figures    | ✅ Works  | `reports/validator.py`, seed migration `i3j4k5l6m7n8`                                           | AVEC (34%), IFTC (53%/34%), VFX Uplift (39%), South Africa (25%), Hungary (30%), Malta (40%), Nigeria (0%). Validator patches AI hallucinations against DB ground truth.                            |
| Crew cost comparison     | ✅ Works  | `reports/service.py` `_fx_enrich_crew_costs()`, `reports/validator.py` `_patch_crew_insights()` | FX-enriched GBP conversions via `FXService`.                                                                                                                                                        |
| Weather table            | ⚠️ Static | `scripts/service.py` prompt → `weatherLogistics` schema                                         | AI generates `bestMonths`, `weatherRisk`, `avgTempRange` etc. per territory — but these are **hardcoded climate knowledge**, not connected to the user's shoot dates or the script's ext/int ratio. |
| Grant matching           | ✅ Basic  | `reports/service.py` `_load_analysis_datasets()` → grants table                                 | Loads open/opening_soon/closing_soon grants and passes to AI prompt. No territory stacking logic.                                                                                                   |

### Key architectural facts

- **Report generation flow:** `router.py` → `ReportService.generate_analysis_report()` → `ScriptAnalysisService.generate_production_analysis()` → Anthropic Claude → `_sanitize_analysis()` → `ReportValidator.validate()` → final JSON.
- **Prompt injection:** All datasets (incentives, crew costs, comparables, grants, festivals) are loaded from Supabase, compacted via `_compact_datasets_for_prompt()`, and injected into the user message as structured JSON.
- **Post-processing:** `ReportValidator` patches hallucinated rates, caps, payment speeds, and staleness warnings using ground-truth from the injected datasets. No weather or regional incentive post-processing exists.
- **Input schema:** `CreateReportRequest` in `reports/schemas.py` already has `filming_start_date: str | None` and `filming_duration: int | None` fields — but they are only passed through to the AI prompt as raw metadata. No backend logic acts on them.
- **Script analysis:** Chunk-based extraction captures `extIntRatio`, `nightSceneCount`, `waterSceneCount`, `vfxHeavySceneCount` in `Challenges` — but these are only used in prompt instructions (e.g. "if waterSceneCount >= 5, boost Malta"), not in any backend scoring or validator logic.

---

## 2. Gap Analysis

### Gap 1: Regional Incentives Completely Absent

**Problem:** The `incentive_programs` table stores incentives at the national/territory level only. There is no concept of sub-national/regional incentives (Creative Scotland, Wales Screen, NI Screen, Screen Ireland regional funds) or stacking rules. The Eko Vibes example shows UK analysis reports 34% AVEC but zero mention of potential Creative Scotland or Wales Screen stacking.

**Root cause:**

- DB schema has no `regional_scope`, `parent_territory`, or `stacking_group` fields.
- `_load_analysis_datasets()` loads all incentives flat — no hierarchy.
- The AI prompt mentions stacking once: _"If a territory has multiple stacking incentives (e.g. national + regional), list each separately"_ — but no regional incentive data exists in the DB to stack.
- `ReportValidator` has no stacking validation logic.

### Gap 2: Weather Logic is Static

**Problem:** `weatherLogistics` is generated entirely by AI training knowledge. The prompt says _"Use filming_start_date and filming_duration to affect seasonal scoring where relevant"_ — but this is a soft instruction, not enforced. The Eko Vibes report says "February is Gauteng's rainy season" buried in section 11, disconnected from the financial model and territory scoring.

**Root cause:**

- No backend weather data source (no `territory_weather` table, no API integration).
- No post-processing in `ReportValidator` to cross-reference weather risk against shoot dates.
- `weatherLogistics` section is not connected to `locationRankings` scoring — the `score` field ignores weather entirely.

### Gap 3: No Shoot Date Input Captured (in scoring)

**Problem:** `filming_start_date` and `filming_duration` exist in the input schema but are only forwarded as raw metadata strings to the AI prompt. No backend logic computes which months the shoot spans, no derived `shoot_months` array is generated, and no scoring adjustment happens.

**Root cause:**

- `filming_start_date` is passed to the prompt as-is.
- No date parsing or month-range computation in `generate_production_analysis()`.
- Prompt instruction _"Use filming_start_date and filming_duration to affect seasonal scoring"_ relies entirely on AI compliance — which is unreliable for math-heavy date reasoning.

### Gap 4: Ext/Int Ratio Not Connected to Weather Risk

**Problem:** Script analysis captures `extIntRatio` (0.0–1.0), `nightSceneCount`, and `waterSceneCount`. The prompt has rules like _"if extIntRatio >= 0.7, boost infrastructure score for territories with outdoor infrastructure"_ — but:

1. This only affects territory scoring, not weather risk.
2. A production with 70% exterior scenes shooting in Gauteng in February should have its weather risk **elevated** and the financial impact quantified (weather delay contingency).
3. The `weatherLogistics.weatherRisk` field is not adjusted based on these ratios.

**Root cause:**

- No backend computation linking `extIntRatio` × `weatherRisk` × `shoot_months`.
- Prompt rules are purely AI-compliance-based, no post-processing validation.

### Gap 5: No Eligibility Logic for Producer Nationality

**Problem:** UK AVEC requires "Company must be liable to UK corporation tax." The report assumes UK registration without asking. A non-UK producer would need to route through a co-production treaty or a UK SPV. This is not captured in input, not asked for, and not reasoned about.

**Root cause:**

- `CreateReportRequest` has `country` (project base country) but no `producer_nationality`, `production_company_jurisdiction`, or `co_production_status` field.
- The AI prompt has no instructions to reason about eligibility based on producer jurisdiction.
- `eligibility_rules_json` in the DB stores rules but these are presented as static text, not evaluated against producer inputs.

---

## 3. Gap 1: Regional Incentive Stacking

### 3.1 What "done" looks like

- The `incentive_programs` table supports regional incentives with a `parent_territory` field (e.g. "Scotland" → parent "United Kingdom") and a `stacking_group` field.
- When the AI prompt receives incentive data, regional incentives appear alongside national ones, with explicit `stackable_with` references.
- The report's `incentiveEstimates` section shows stacked totals. Example:
  ```json
  {
    "territory": "Scotland",
    "stackedIncentives": [
      {
        "program": "Audio Visual Expenditure Credit (AVEC)",
        "rate": "34%",
        "scope": "national"
      },
      {
        "program": "Creative Scotland Production Growth Fund",
        "rate": "varies",
        "scope": "regional"
      }
    ],
    "combinedEffectiveRate": "34% + up to £500K regional grant",
    "stackingNotes": "AVEC applies UK-wide. Creative Scotland fund is additional and does not reduce AVEC qualification."
  }
  ```
- `ReportValidator` validates that stacked incentives reference real DB rows and the stacking combination is permitted.

### 3.2 Database changes

**New columns on `incentive_programs`:**

| Column             | Type    | Description                                                                                            |
| ------------------ | ------- | ------------------------------------------------------------------------------------------------------ |
| `scope`            | `text`  | `'national'` \| `'regional'` \| `'municipal'` — level of government                                    |
| `parent_territory` | `text`  | For regional incentives, the parent national territory (e.g. `'United Kingdom'` for Creative Scotland) |
| `stacking_group`   | `text`  | Stacking group ID — incentives in the same group can stack. `NULL` = standalone.                       |
| `stackable_with`   | `jsonb` | Array of `program_name` strings this incentive can stack with. `NULL` = standalone.                    |

**New seed data required:**

| Territory          | Program                                       | Scope    | Parent         | Stackable With                               |
| ------------------ | --------------------------------------------- | -------- | -------------- | -------------------------------------------- |
| Scotland           | Creative Scotland Production Growth Fund      | regional | United Kingdom | `["Audio Visual Expenditure Credit (AVEC)"]` |
| Wales              | Wales Screen / Ffilm Cymru                    | regional | United Kingdom | `["Audio Visual Expenditure Credit (AVEC)"]` |
| Northern Ireland   | NI Screen Fund                                | regional | United Kingdom | `["Audio Visual Expenditure Credit (AVEC)"]` |
| Ireland (regional) | Regional Uplift                               | regional | Ireland        | `["Section 481 Tax Credit"]`                 |
| Georgia (USA)      | Georgia Entertainment Industry Investment Act | regional | USA            | `[]`                                         |
| New Mexico         | NM Film Tax Credit                            | regional | USA            | `[]`                                         |
| British Columbia   | BC FIBC Tax Credit                            | regional | Canada         | `["Canada Federal PSTC"]`                    |

### 3.3 Service changes

**`reports/service.py` — `_load_analysis_datasets()`:**

```python
# After loading all_incentives, build stacking map
stacking_map = {}
for inc in incentives:
    group = inc.get("stacking_group")
    if group:
        stacking_map.setdefault(group, []).append(inc["program_name"])

# Inject stacking_map into datasets
datasets["stacking_map"] = stacking_map
```

**`scripts/service.py` — `_compact_datasets_for_prompt()`:**

Add `scope`, `parent_territory`, `stackable_with` to the incentives field whitelist.

### 3.4 Prompt changes

Add to `PRODUCTION_ANALYSIS_PROMPT`:

```
REGIONAL INCENTIVE STACKING RULES:
- Check the `scope` field on each incentive: "national", "regional", or "municipal".
- If a territory in locationRankings has both national AND regional incentives available, show BOTH in incentiveEstimates.
- Use the `stackable_with` field to determine valid combinations. Only show stacking when the stackable_with array confirms compatibility.
- In the incentiveEstimates entry for a regional incentive, add a `stackingNote` explaining how it layers on top of the national incentive.
- In the financialAnalysis.budgetScenarios, show both the base national rebate AND the combined national+regional amount.
- For UK territories: if shoot locations include Scotland, Wales, or Northern Ireland, ALWAYS check for regional incentives that stack on AVEC.
- For USA territories: state-level incentives (Georgia, New Mexico, New York) are the primary incentive — there is no federal film incentive to stack on.
- For Canada territories: provincial credits (BC FIBC, Ontario OCASE) can stack with federal PSTC.
```

### 3.5 Validator changes

**`reports/validator.py` — new method `_patch_stacking_logic()`:**

- For each `incentiveEstimate`, if the DB row has `stackable_with` data, verify that referenced programs also exist in the dataset.
- If the AI hallucinates a stacking combination not supported by `stackable_with`, strip it and add a warning.
- Ensure `combinedEffectiveRate` math is consistent (national rate + regional amount/rate).

### 3.6 Schema changes

**`reports/schemas.py` — `IncentiveEstimate`:**

```python
class IncentiveEstimate(BaseModel):
    # ... existing fields ...
    scope: Literal["national", "regional", "municipal"] | None = None
    parentTerritory: str | None = None
    stackableWith: list[str] | None = None
    stackingNote: str | None = None
```

---

## 4. Gap 2: Weather–Schedule Integration

### 4.1 What "done" looks like

- When `filming_start_date` and `filming_duration` are provided, the backend computes the exact shoot month range.
- A `territory_weather` table provides per-territory, per-month climate data (avg temp, rainfall mm, daylight hours, storm risk).
- The weather risk for each territory is **scored against the actual shoot months**, not generic climate knowledge.
- Weather risk impacts territory scoring:
  - If the shoot window overlaps with high-risk months AND `extIntRatio >= 0.5`, `score` is penalized by up to 10 points.
  - A `weatherRiskImpact` field in `locationRankings` shows the deduction.
- The weather risk flag appears at the **TOP** of the territory section (in `keyRisks`), not buried in section 11.

### 4.2 Database changes

**New table: `territory_weather`**

| Column                 | Type       | Description                                                                          |
| ---------------------- | ---------- | ------------------------------------------------------------------------------------ |
| `id`                   | `uuid` PK  |                                                                                      |
| `territory`            | `text`     | Territory name (FK concept to match incentive_programs.territory)                    |
| `month`                | `integer`  | 1–12                                                                                 |
| `avg_temp_high_c`      | `float`    | Average daily high °C                                                                |
| `avg_temp_low_c`       | `float`    | Average daily low °C                                                                 |
| `avg_rainfall_mm`      | `float`    | Average monthly rainfall in mm                                                       |
| `avg_daylight_hours`   | `float`    | Average daylight hours                                                               |
| `storm_risk`           | `text`     | `'low'` \| `'medium'` \| `'high'` — likelihood of production-disrupting weather      |
| `weather_notes`        | `text`     | Territory-specific seasonal notes (e.g. "Afternoon thunderstorms common in Gauteng") |
| `exterior_shoot_score` | `integer`  | 0–100 — how suitable this month is for exterior filming                              |
| `source`               | `text`     | Data source attribution                                                              |
| `last_verified_at`     | `datetime` |                                                                                      |
| `created_at`           | `datetime` |                                                                                      |

**Seed data for priority territories (per month):**

Territories to seed: United Kingdom, South Africa (Gauteng, Western Cape), Malta, Hungary, Ireland, Scotland, Czech Republic, Spain, France, Germany, Georgia (USA), New Mexico, British Columbia, Australia (NSW, Victoria), New York.

### 4.3 Service changes

**`reports/service.py` — `_load_analysis_datasets()`:**

```python
# Load territory weather data
weather_data = self._safe_query("territory_weather", lambda q: q.select("*"))
datasets["weather"] = weather_data
```

**`reports/service.py` — new method `_compute_shoot_months()`:**

```python
from datetime import date, timedelta

def _compute_shoot_months(self, filming_start_date: str | None, filming_duration: int | None) -> list[int] | None:
    """Return list of month numbers (1-12) the shoot spans. None if inputs missing."""
    if not filming_start_date:
        return None
    try:
        start = date.fromisoformat(filming_start_date[:10])
    except ValueError:
        return None

    duration_weeks = filming_duration or 4  # default 4 weeks
    end = start + timedelta(weeks=duration_weeks)

    months = set()
    current = start
    while current <= end:
        months.add(current.month)
        current += timedelta(days=15)  # step through to catch all months
    months.add(end.month)

    return sorted(months)
```

**`scripts/service.py` — `generate_production_analysis()`:**

Before building the user message, compute shoot months and inject as derived data:

```python
# In generate_production_analysis(), before building parts[]
shoot_months = self._compute_shoot_months(
    request_metadata.get("filming_start_date"),
    request_metadata.get("filming_duration"),
)
if shoot_months:
    parts.append("\n=== DERIVED: SHOOT WINDOW ===")
    parts.append(json.dumps({
        "shoot_months": shoot_months,
        "month_names": [calendar.month_abbr[m] for m in shoot_months],
        "filming_start_date": request_metadata.get("filming_start_date"),
        "filming_duration_weeks": request_metadata.get("filming_duration"),
    }))
```

### 4.4 Prompt changes

Add to `PRODUCTION_ANALYSIS_PROMPT`:

```
WEATHER–SCHEDULE INTEGRATION RULES:
- If DERIVED: SHOOT WINDOW data is present, you MUST use it to evaluate weather risk.
- For each territory in locationRankings:
  1. Look up the territory in the TERRITORY WEATHER dataset for the specific shoot months.
  2. Set weatherRisk based on actual data for those months, NOT generic climate knowledge.
  3. If avg_rainfall_mm > 100 for any shoot month, set weatherRisk = "High".
  4. If storm_risk = "high" for any shoot month, set weatherRisk = "High".
  5. If weatherRisk = "High" AND the shoot window overlaps AND this isn't just 1 month:
     - Add to keyRisks: "Shooting in [month] overlaps with [territory]'s [weather condition] — expect [X] days lost. Budget £[amount] contingency."
     - This MUST appear in keyRisks (top of territory section), NOT only in weatherLogistics.
  6. Deduct up to 10 points from the territory overall score if weatherRisk is High and extIntRatio >= 0.5.
- For weatherLogistics entries:
  - Use the TERRITORY WEATHER dataset values for avgTempRange, avgRainfall, daylightHours.
  - Set bestMonths to months where exterior_shoot_score >= 70.
  - Set seasonalConsiderations based on weather_notes from the dataset.
- CRITICAL: If a territory has weatherRisk = "High" for the shoot window, this finding MUST appear in:
  1. The territory's keyRisks array (locationRankings)
  2. The executiveSummary.keyInsights narrative
  3. The weatherLogistics entry
  Do NOT bury weather risks only in section 11.
```

### 4.5 Validator changes

**`reports/validator.py` — new method `_patch_weather_risk()`:**

```python
@classmethod
def _patch_weather_risk(
    cls,
    report: dict,
    weather_data: list[dict],
    shoot_months: list[int] | None,
    ext_int_ratio: float | None,
    warnings: list[str],
) -> None:
    """Cross-reference weather data against shoot months and ext/int ratio."""
    if not shoot_months or not weather_data:
        return

    # Index weather by territory + month
    weather_index = {}
    for w in weather_data:
        key = (w.get("territory", ""), w.get("month"))
        weather_index[key] = w

    rankings = report.get("locationRankings", [])
    for loc in rankings:
        territory = loc.get("name", "")
        high_risk_months = []
        for month in shoot_months:
            w = weather_index.get((territory, month))
            if not w:
                continue
            if w.get("storm_risk") == "high" or (w.get("avg_rainfall_mm") or 0) > 100:
                high_risk_months.append(month)

        if high_risk_months:
            # Ensure weatherRisk flagged in keyRisks
            key_risks = loc.setdefault("keyRisks", [])
            month_names = [calendar.month_abbr[m] for m in high_risk_months]
            risk_msg = f"Weather risk: shooting in {', '.join(month_names)} overlaps with adverse conditions"
            if not any("weather risk" in r.lower() for r in key_risks):
                key_risks.insert(0, risk_msg)  # Insert at TOP
                warnings.append(f"[locationRankings] {territory}: weather risk injected into keyRisks")

            # Score penalty if high ext/int ratio
            if ext_int_ratio and ext_int_ratio >= 0.5:
                penalty = min(10, len(high_risk_months) * 3)
                current_score = loc.get("score", 50)
                loc["score"] = max(0, current_score - penalty)
                loc["weatherRiskImpact"] = -penalty
                warnings.append(
                    f"[locationRankings] {territory}: score penalized by {penalty} "
                    f"(weather + {ext_int_ratio:.0%} exterior ratio)"
                )
```

Call this from `ReportValidator.validate()`:

```python
cls._patch_weather_risk(
    report,
    datasets.get("weather", []),
    datasets.get("_shoot_months"),
    datasets.get("_ext_int_ratio"),
    warnings,
)
```

### 4.6 Schema changes

**`reports/schemas.py` — `LocationRanking`:**

```python
class LocationRanking(BaseModel):
    # ... existing fields ...
    weatherRiskImpact: int | None = None  # negative score deduction from weather
```

**`reports/schemas.py` — `WeatherLogistic`:**

```python
class WeatherLogistic(BaseModel):
    # ... existing fields ...
    shootWindowOverlap: bool | None = None  # True if shoot months fall in risky period
    shootWindowRisk: str | None = None  # "Your Feb-Mar shoot overlaps with rainy season"
    estimatedDelayDays: int | None = None  # Estimated weather delay days
    contingencyBudget: str | None = None  # "£15,000–£25,000 recommended"
```

---

## 5. Gap 3: Shoot Date Input Activation

### 5.1 What "done" looks like

- `filming_start_date` and `filming_duration` are parsed in the backend, not just forwarded as strings.
- A `shoot_months` array is computed and injected as derived data into:
  1. The AI prompt (as a structured `=== DERIVED: SHOOT WINDOW ===` section)
  2. The `ReportValidator` context (for weather cross-reference)
  3. The report JSON output (new field `shootWindow` in executive summary)
- If `filming_start_date` is not provided, the report explicitly states: "No shoot dates provided — weather analysis uses annual averages."

### 5.2 Service changes

**`scripts/service.py` — `generate_production_analysis()`:**

The `_compute_shoot_months()` method (defined in Gap 2 above) is called here. Additionally:

```python
# After computing shoot_months, also derive:
shoot_window = None
if shoot_months:
    import calendar
    shoot_window = {
        "startDate": request_metadata.get("filming_start_date"),
        "durationWeeks": request_metadata.get("filming_duration"),
        "months": shoot_months,
        "monthNames": [calendar.month_abbr[m] for m in shoot_months],
        "season": _classify_season(shoot_months),  # "summer", "winter", "mixed"
    }

# Pass to datasets for validator use
datasets["_shoot_months"] = shoot_months
datasets["_shoot_window"] = shoot_window
```

**New helper `_classify_season()`:**

```python
def _classify_season(months: list[int]) -> str:
    """Classify the shoot window season (Northern Hemisphere default)."""
    summer = {5, 6, 7, 8, 9}
    winter = {11, 12, 1, 2, 3}
    if all(m in summer for m in months):
        return "summer"
    if all(m in winter for m in months):
        return "winter"
    return "mixed"
```

### 5.3 Prompt changes

Add to the prompt output schema:

```json
"executiveSummary": {
    // ... existing fields ...
    "shootWindow": {
        "months": ["Feb", "Mar"],
        "weatherNote": "Shoot window overlaps with rainy season in 2 of 5 ranked territories"
    }
}
```

Add instruction:

```
SHOOT WINDOW ACTIVATION:
- If DERIVED: SHOOT WINDOW data is present:
  1. Include a `shootWindow` object in executiveSummary with the month names and a weather summary note.
  2. All weatherLogistics entries MUST reference the specific shoot months, not generic annual data.
  3. All territory keyRisks MUST flag shoot-month-specific weather issues.
  4. Crew availability assessments in crewInsights should note if the shoot window coincides with local production peaks.
- If NO shoot window data is present:
  1. Set executiveSummary.shootWindow to null.
  2. Add a note in executiveSummary.keyInsights: "No shoot dates provided — weather analysis based on annual averages. Provide filming dates for schedule-specific risk assessment."
  3. weatherLogistics should use annual averages and state "Based on annual average — provide shoot dates for specific analysis."
```

### 5.4 Schema changes

**`reports/schemas.py` — `ExecutiveSummary`:**

```python
class ShootWindow(BaseModel):
    months: list[str]
    weatherNote: str | None = None

class ExecutiveSummary(BaseModel):
    # ... existing fields ...
    shootWindow: ShootWindow | None = None
```

---

## 6. Gap 4: Ext/Int Ratio → Weather Risk Wiring

### 6.1 What "done" looks like

- `extIntRatio` from script analysis is passed to the production analysis as a first-class derived input.
- A production with `extIntRatio >= 0.5` (50%+ exterior scenes) has its weather risk **amplified**:
  - Weather risk scoring penalty is doubled.
  - `weatherLogistics` entries include a `exteriorExposure` field: "High (72% exterior scenes)"
  - `keyRisks` explicitly mentions exterior exposure: "72% exterior scenes — weather delays will impact majority of shooting schedule"
- A production with `extIntRatio < 0.3` (mostly interior) has weather risk **dampened**:
  - Weather risk scoring penalty is halved.
  - Report notes: "Low exterior exposure (28%) — weather risk primarily affects unit moves and exterior establishing shots"

### 6.2 Service changes

**`scripts/service.py` — `generate_production_analysis()`:**

Extract ratios from script analysis and inject as derived data:

```python
# Extract scene ratios from script analysis
ext_int_ratio = None
night_scene_ratio = None
if script_analysis:
    challenges = script_analysis.challenges
    ext_int_ratio = challenges.extIntRatio
    night_count = challenges.nightSceneCount or 0
    # Compute night ratio from total scenes
    total_scenes = (challenges.extIntRatio or 0) > 0  # proxy: if we have ext/int data

# Inject derived signals
if ext_int_ratio is not None:
    parts.append("\n=== DERIVED: SCENE EXPOSURE PROFILE ===")
    parts.append(json.dumps({
        "extIntRatio": ext_int_ratio,
        "exteriorPercentage": f"{ext_int_ratio * 100:.0f}%",
        "nightSceneCount": script_analysis.challenges.nightSceneCount if script_analysis else None,
        "waterSceneCount": script_analysis.challenges.waterSceneCount if script_analysis else None,
        "weatherExposureLevel": (
            "high" if ext_int_ratio >= 0.7 else
            "medium" if ext_int_ratio >= 0.4 else
            "low"
        ),
    }))

# Pass to datasets for validator
datasets["_ext_int_ratio"] = ext_int_ratio
```

### 6.3 Prompt changes

Add to `PRODUCTION_ANALYSIS_PROMPT`:

```
SCENE EXPOSURE → WEATHER RISK RULES:
- If DERIVED: SCENE EXPOSURE PROFILE is present:
  1. If weatherExposureLevel = "high" (70%+ exterior):
     - For EVERY territory where weatherRisk = "Medium" or "High", add to keyRisks:
       "[X]% of scenes are exterior — weather disruptions will affect the majority of the shooting schedule."
     - In weatherLogistics, add `exteriorExposure: "High (X% exterior scenes)"`.
     - Boost the weather contingency recommendation by 50%.
  2. If weatherExposureLevel = "medium" (40-70% exterior):
     - Flag weather risk normally but note the mixed int/ext ratio provides some scheduling flexibility.
  3. If weatherExposureLevel = "low" (<40% exterior):
     - Note that weather risk is mitigated by low exterior exposure.
     - In weatherLogistics, add `exteriorExposure: "Low (X% exterior scenes) — most shooting is interior"`.
  4. If nightSceneCount >= 8:
     - Flag territories with short winter daylight hours in keyRisks.
     - Note increased crew turnaround costs for night shooting blocks.
  5. If waterSceneCount >= 3:
     - Flag marine/weather risk for relevant territories.
     - Note insurance implications.
```

### 6.4 Validator changes

The `_patch_weather_risk()` method in Gap 2 already handles the `ext_int_ratio` parameter. The key addition is:

```python
# In _patch_weather_risk, after computing high_risk_months:
if ext_int_ratio and ext_int_ratio >= 0.7:
    # Amplify: ensure exterior exposure is mentioned in keyRisks
    exposure_msg = f"{ext_int_ratio * 100:.0f}% exterior scenes — weather delays affect majority of schedule"
    if not any("exterior" in r.lower() for r in key_risks):
        key_risks.insert(0, exposure_msg)
```

### 6.5 Schema changes

**`reports/schemas.py` — `WeatherLogistic`:**

```python
class WeatherLogistic(BaseModel):
    # ... existing fields ...
    exteriorExposure: str | None = None  # "High (72% exterior scenes)"
```

---

## 7. Gap 5: Producer Nationality / Eligibility Logic

### 7.1 What "done" looks like

- `CreateReportRequest` includes new optional fields: `producer_country` (jurisdiction of production company) and `co_production_status`.
- When `producer_country` doesn't match a territory's eligibility requirements, the report:
  1. Flags it in `keyRisks`: "Non-UK production company — AVEC requires UK corporation tax liability. Consider establishing a UK SPV or qualifying via an official co-production treaty."
  2. Adds an `eligibilityStatus` field to `incentiveEstimates`: `"qualified"`, `"requires_co_production"`, `"requires_spv"`, `"ineligible"`.
  3. Suggests mitigation paths (co-production, SPV, treaty partner).
- The report does NOT assume eligibility — it states the assumption explicitly and flags the risk.

### 7.2 Input schema changes

**`reports/schemas.py` — `CreateReportRequest`:**

```python
class CreateReportRequest(BaseModel):
    # ... existing fields ...

    # Producer eligibility (new)
    producer_country: str | None = None  # Jurisdiction of production company
    co_production_status: Literal[
        "sole_producer",
        "co_production_treaty",
        "co_production_informal",
        "undecided",
    ] | None = None
    production_company_type: Literal[
        "uk_registered",
        "us_registered",
        "eu_registered",
        "other",
    ] | None = None
```

### 7.3 Database changes

**New columns on `incentive_programs`:**

| Column                     | Type      | Description                                                                   |
| -------------------------- | --------- | ----------------------------------------------------------------------------- |
| `nationality_requirements` | `jsonb`   | Array of jurisdiction codes that qualify directly (e.g. `["GB"]` for UK AVEC) |
| `co_production_eligible`   | `boolean` | Whether co-production treaties can satisfy nationality requirements           |
| `co_production_treaties`   | `jsonb`   | Array of countries with applicable co-production treaties                     |
| `spv_eligible`             | `boolean` | Whether establishing a local SPV satisfies the requirement                    |

**Seed data updates:**

```python
# UK AVEC
"nationality_requirements": '["GB"]',
"co_production_eligible": True,
"co_production_treaties": '["IE","FR","DE","AU","CA","ZA","NZ","IT","IL","JM","IN","MO","NZ","PL"]',
"spv_eligible": True,

# Malta MFTI
"nationality_requirements": '["MT","EU"]',  # EU producers qualify
"co_production_eligible": True,
"spv_eligible": True,

# South Africa Foreign Film Incentive
"nationality_requirements": None,  # Foreign productions welcome
"co_production_eligible": True,
"spv_eligible": False,  # Must use SA production services company
```

### 7.4 Service changes

**`scripts/service.py` — `_compact_datasets_for_prompt()`:**

Add `nationality_requirements`, `co_production_eligible`, `co_production_treaties`, `spv_eligible` to the incentives field whitelist.

### 7.5 Prompt changes

Add to `PRODUCTION_ANALYSIS_PROMPT`:

```
PRODUCER ELIGIBILITY RULES:
- If PROJECT METADATA includes producer_country:
  1. For each incentiveEstimate, check the nationality_requirements field in the dataset.
  2. If producer_country is NOT in nationality_requirements AND co_production_status is not "co_production_treaty":
     - Set eligibilityStatus = "requires_co_production" or "requires_spv" based on the programme's co_production_eligible and spv_eligible fields.
     - Add to keyRisks: "[Producer country] production company — [programme] requires [nationality] corporation tax liability. Options: (a) Establish local SPV, (b) Qualify via co-production treaty with [treaty countries], (c) Route through local production services company."
     - In incentiveEstimates.requirements, add: "Producer nationality check: [status]"
  3. If producer_country IS in nationality_requirements: set eligibilityStatus = "qualified".
  4. If producer_country is not provided:
     - Add a note in incentiveEstimates.requirements: "Eligibility assumes [nationality] production company — verify before committing."
     - Add to keyRisks: "Producer nationality not specified — eligibility for [programme] depends on company jurisdiction."
- If co_production_status = "co_production_treaty":
  - Check if the producer_country has a treaty with the territory (co_production_treaties field).
  - If treaty exists: note "Eligible via [country]-[territory] co-production treaty."
  - If no treaty: flag "No co-production treaty between [producer_country] and [territory] — alternative qualification route needed."
```

### 7.6 Validator changes

**`reports/validator.py` — new method `_patch_eligibility()`:**

```python
@classmethod
def _patch_eligibility(
    cls,
    report: dict,
    incentives_by_program: dict[str, dict],
    producer_country: str | None,
    co_production_status: str | None,
    warnings: list[str],
) -> None:
    """Ensure eligibility assumptions are explicit, not silent."""
    estimates = report.get("incentiveEstimates", [])
    for est in estimates:
        program_name = est.get("program", "")
        db_row = incentives_by_program.get(program_name)
        if not db_row:
            continue

        nat_reqs = db_row.get("nationality_requirements")
        if not nat_reqs:
            continue  # No nationality restriction

        if producer_country:
            # Check if producer qualifies directly
            if producer_country.upper() not in [n.upper() for n in nat_reqs]:
                if not est.get("eligibilityStatus"):
                    est["eligibilityStatus"] = "requires_co_production"
                    warnings.append(
                        f"[incentiveEstimates] {est.get('territory')}/{program_name}: "
                        f"producer_country={producer_country} not in nationality_requirements"
                    )
        else:
            # No producer country — add assumption warning
            reqs = est.setdefault("requirements", [])
            assumption_msg = f"Eligibility assumes qualifying {db_row.get('territory')} entity — verify company jurisdiction"
            if not any("eligibility assumes" in r.lower() for r in reqs):
                reqs.append(assumption_msg)
```

### 7.7 Schema changes

**`reports/schemas.py` — `IncentiveEstimate`:**

```python
class IncentiveEstimate(BaseModel):
    # ... existing fields ...
    eligibilityStatus: Literal[
        "qualified", "requires_co_production", "requires_spv", "ineligible", "unknown"
    ] | None = None
    eligibilityNote: str | None = None  # Human-readable explanation
```

---

## 8. Implementation Order & Dependencies

```
Phase 1: Foundation (no AI changes, backend-only)
├── 8.1  Migration: Add scope/parent_territory/stacking columns to incentive_programs
├── 8.2  Migration: Create territory_weather table
├── 8.3  Migration: Add nationality_requirements columns to incentive_programs
├── 8.4  Migration: Add producer_country/co_production_status to users/reports if needed
├── 8.5  Seed: Regional incentive programs (Creative Scotland, Wales Screen, NI Screen, etc.)
├── 8.6  Seed: territory_weather data for priority territories (12 months × ~15 territories)
└── 8.7  Seed: nationality_requirements for existing incentive programs

Phase 2: Backend Logic (no prompt changes yet)
├── 8.8  _compute_shoot_months() in reports/service.py
├── 8.9  _load_analysis_datasets() — load weather data, build stacking map
├── 8.10 Update CreateReportRequest schema (producer_country, co_production_status)
├── 8.11 _compact_datasets_for_prompt() — add new fields to whitelist
└── 8.12 Pass derived data (_shoot_months, _ext_int_ratio, _shoot_window) to validator

Phase 3: Prompt Engineering
├── 8.13 Add REGIONAL INCENTIVE STACKING RULES to PRODUCTION_ANALYSIS_PROMPT
├── 8.14 Add WEATHER–SCHEDULE INTEGRATION RULES
├── 8.15 Add SHOOT WINDOW ACTIVATION rules
├── 8.16 Add SCENE EXPOSURE → WEATHER RISK RULES
├── 8.17 Add PRODUCER ELIGIBILITY RULES
└── 8.18 Update response JSON schema in prompt (new fields)

Phase 4: Validator & Post-Processing
├── 8.19 ReportValidator._patch_stacking_logic()
├── 8.20 ReportValidator._patch_weather_risk()
├── 8.21 ReportValidator._patch_eligibility()
└── 8.22 Update _sanitize_analysis() to preserve new fields

Phase 5: Schema & Output
├── 8.23 Update Pydantic schemas (LocationRanking, WeatherLogistic, IncentiveEstimate, ExecutiveSummary)
├── 8.24 Update PDF template to render new fields
└── 8.25 Update fallback_analysis() to include new field defaults

Phase 6: Testing & Validation
├── 8.26 Unit tests for _compute_shoot_months()
├── 8.27 Unit tests for _patch_weather_risk()
├── 8.28 Unit tests for _patch_stacking_logic()
├── 8.29 Unit tests for _patch_eligibility()
├── 8.30 Integration test: full report with shoot dates + high ext ratio
└── 8.31 Regression test: existing reports still pass validation
```

### Critical path

```
Phase 1.1 (DB) → Phase 2.8-2.9 (logic) → Phase 3.14 (prompt) → Phase 4.20 (validator)
```

Weather–schedule integration is the highest-impact gap. It touches the most files and delivers the most visible improvement.

---

## 9. Database Migrations Required

### Migration 1: Regional Incentive Fields

```python
# alembic/versions/xxxx_add_regional_incentive_fields.py
_NEW_COLUMNS = [
    ("scope", sa.Text()),                    # 'national' | 'regional' | 'municipal'
    ("parent_territory", sa.Text()),         # For regional: parent national territory
    ("stacking_group", sa.Text()),           # Group ID for stackable incentives
    ("stackable_with", sa.Text()),           # JSON array of compatible program_names
]
# Applied to: incentive_programs table
# Default scope='national' for existing rows via data migration
```

### Migration 2: Nationality Requirements

```python
_NEW_COLUMNS = [
    ("nationality_requirements", sa.Text()),  # JSON array of qualifying country codes
    ("co_production_eligible", sa.Boolean()),
    ("co_production_treaties", sa.Text()),     # JSON array of treaty partner codes
    ("spv_eligible", sa.Boolean()),
]
# Applied to: incentive_programs table
```

### Migration 3: Territory Weather Table

```python
# Full CREATE TABLE — see Gap 2 section 4.2 for schema
# Seed with ~180 rows (15 territories × 12 months)
```

### Migration 4: Seed Regional Incentives

```python
# INSERT rows for Creative Scotland, Wales Screen, NI Screen,
# Georgia Entertainment Act, NM Film Tax Credit, BC FIBC, etc.
# UPDATE existing rows to set scope='national'
```

### Migration 5: Seed Nationality Requirements

```python
# UPDATE existing incentive rows with nationality_requirements,
# co_production_eligible, co_production_treaties, spv_eligible
```

---

## 10. Prompt Engineering Changes

### Summary of all prompt additions

The `PRODUCTION_ANALYSIS_PROMPT` in `scripts/service.py` needs these additions (in order):

1. **REGIONAL INCENTIVE STACKING RULES** (~200 words) — after existing DATA INTEGRITY RULES
2. **WEATHER–SCHEDULE INTEGRATION RULES** (~300 words) — after SCRIPT SIGNAL → TERRITORY SCORING RULES
3. **SHOOT WINDOW ACTIVATION** (~150 words) — new section
4. **SCENE EXPOSURE → WEATHER RISK RULES** (~200 words) — new section
5. **PRODUCER ELIGIBILITY RULES** (~250 words) — new section

**Token budget impact:** ~1,100 additional words ≈ ~1,500 tokens added to system prompt. Current system prompt is ~3,500 tokens. New total ~5,000 tokens — well within Anthropic's context window.

### New derived data sections injected into user message

| Section                                   | Condition                       | Content                                                                    |
| ----------------------------------------- | ------------------------------- | -------------------------------------------------------------------------- |
| `=== DERIVED: SHOOT WINDOW ===`           | `filming_start_date` present    | `{shoot_months, month_names, season}`                                      |
| `=== DERIVED: SCENE EXPOSURE PROFILE ===` | `script_analysis` present       | `{extIntRatio, exteriorPercentage, nightSceneCount, weatherExposureLevel}` |
| `=== TERRITORY WEATHER DATA ===`          | `territory_weather` data loaded | Per-territory per-month climate data for shoot months only                 |

### Response schema additions

```json
// In locationRankings items:
"weatherRiskImpact": -8,  // NEW: score deduction from weather

// In incentiveEstimates items:
"scope": "regional",  // NEW
"parentTerritory": "United Kingdom",  // NEW
"stackableWith": ["AVEC"],  // NEW
"stackingNote": "Stacks on top of AVEC — combined effective value: 34% + £500K grant",  // NEW
"eligibilityStatus": "qualified",  // NEW
"eligibilityNote": "UK-registered company qualifies directly",  // NEW

// In executiveSummary:
"shootWindow": { "months": ["Feb", "Mar"], "weatherNote": "..." },  // NEW

// In weatherLogistics items:
"shootWindowOverlap": true,  // NEW
"shootWindowRisk": "Your Feb-Mar shoot overlaps with Gauteng rainy season",  // NEW
"exteriorExposure": "High (72% exterior scenes)",  // NEW
"estimatedDelayDays": 3,  // NEW
"contingencyBudget": "£15,000–£25,000"  // NEW
```

---

## 11. Validator Changes

### Updated `ReportValidator.validate()` signature

```python
@classmethod
def validate(cls, report: dict, datasets: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    incentives_by_program = _index_incentives(datasets.get("incentives", []))

    # Existing patches
    cls._patch_incentive_estimates(report, incentives_by_program, warnings)
    cls._patch_location_rankings(report, incentives_by_program, warnings)
    cls._patch_territory_deep_dives(report, incentives_by_program, warnings)
    cls._patch_executive_summary(report, incentives_by_program, warnings)
    cls._patch_crew_insights(report, datasets.get("crew_costs", []), warnings)

    # NEW patches
    cls._patch_stacking_logic(report, incentives_by_program, warnings)
    cls._patch_weather_risk(
        report,
        datasets.get("weather", []),
        datasets.get("_shoot_months"),
        datasets.get("_ext_int_ratio"),
        warnings,
    )
    cls._patch_eligibility(
        report,
        incentives_by_program,
        datasets.get("_producer_country"),
        datasets.get("_co_production_status"),
        warnings,
    )

    return report, warnings
```

### New validator methods summary

| Method                    | Purpose                                                                                     | Inputs                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `_patch_stacking_logic()` | Validate stacking combinations against `stackable_with` DB field                            | `incentives_by_program`                                             |
| `_patch_weather_risk()`   | Cross-ref weather data × shoot months × ext/int ratio; inject keyRisks; apply score penalty | `weather_data`, `shoot_months`, `ext_int_ratio`                     |
| `_patch_eligibility()`    | Check producer nationality against `nationality_requirements`; add assumption warnings      | `incentives_by_program`, `producer_country`, `co_production_status` |

---

## 12. Schema Changes

### Complete list of Pydantic model changes

**`reports/schemas.py`:**

```python
# ── New models ──

class ShootWindow(BaseModel):
    months: list[str]
    weatherNote: str | None = None

class StackedIncentive(BaseModel):
    program: str
    rate: str
    scope: Literal["national", "regional", "municipal"]

# ── Modified models ──

class LocationRanking(BaseModel):
    # ... all existing fields unchanged ...
    weatherRiskImpact: int | None = None

class IncentiveEstimate(BaseModel):
    # ... all existing fields unchanged ...
    scope: Literal["national", "regional", "municipal"] | None = None
    parentTerritory: str | None = None
    stackableWith: list[str] | None = None
    stackingNote: str | None = None
    eligibilityStatus: Literal[
        "qualified", "requires_co_production", "requires_spv", "ineligible", "unknown"
    ] | None = None
    eligibilityNote: str | None = None

class WeatherLogistic(BaseModel):
    # ... all existing fields unchanged ...
    shootWindowOverlap: bool | None = None
    shootWindowRisk: str | None = None
    exteriorExposure: str | None = None
    estimatedDelayDays: int | None = None
    contingencyBudget: str | None = None

class ExecutiveSummary(BaseModel):
    # ... all existing fields unchanged ...
    shootWindow: ShootWindow | None = None

class CreateReportRequest(BaseModel):
    # ... all existing fields unchanged ...
    producer_country: str | None = None
    co_production_status: Literal[
        "sole_producer", "co_production_treaty", "co_production_informal", "undecided"
    ] | None = None
    production_company_type: Literal[
        "uk_registered", "us_registered", "eu_registered", "other"
    ] | None = None
```

---

## 13. Testing Strategy

### Unit tests

| Test                                  | File                              | Validates                                                 |
| ------------------------------------- | --------------------------------- | --------------------------------------------------------- |
| `test_compute_shoot_months_basic`     | `tests/test_reports_service.py`   | Feb start + 6 weeks → [2, 3]                              |
| `test_compute_shoot_months_year_wrap` | `tests/test_reports_service.py`   | Nov start + 12 weeks → [11, 12, 1, 2]                     |
| `test_compute_shoot_months_no_input`  | `tests/test_reports_service.py`   | None → None                                               |
| `test_classify_season`                | `tests/test_reports_service.py`   | Summer/winter/mixed classification                        |
| `test_patch_weather_risk_high_ext`    | `tests/test_reports_validator.py` | 70% ext + Feb Gauteng → score penalty + keyRisk injection |
| `test_patch_weather_risk_low_ext`     | `tests/test_reports_validator.py` | 20% ext + Feb Gauteng → no penalty                        |
| `test_patch_weather_risk_no_data`     | `tests/test_reports_validator.py` | No weather data → no changes                              |
| `test_patch_stacking_valid`           | `tests/test_reports_validator.py` | AVEC + Creative Scotland → both in output                 |
| `test_patch_stacking_invalid`         | `tests/test_reports_validator.py` | Hallucinated stacking → stripped + warning                |
| `test_patch_eligibility_uk_producer`  | `tests/test_reports_validator.py` | GB producer + AVEC → "qualified"                          |
| `test_patch_eligibility_non_uk`       | `tests/test_reports_validator.py` | ZA producer + AVEC → "requires_co_production"             |
| `test_patch_eligibility_no_producer`  | `tests/test_reports_validator.py` | No producer_country → assumption warning added            |

### Integration tests

| Test                                   | Description                                                                                                                                                                      |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_full_report_with_shoot_dates`    | Submit report with `filming_start_date=2026-02-01`, `filming_duration=6`, genre=Drama, territories=[UK, South Africa]. Assert weather risk appears in keyRisks for South Africa. |
| `test_full_report_high_ext_ratio`      | Submit script with 70%+ exterior scenes. Assert weatherLogistics includes exteriorExposure field.                                                                                |
| `test_full_report_regional_incentives` | Submit report with territories=[Scotland]. Assert both AVEC and Creative Scotland appear in incentiveEstimates.                                                                  |
| `test_full_report_non_uk_producer`     | Submit report with `producer_country=ZA`, territories=[UK]. Assert eligibility warning on AVEC.                                                                                  |

### Regression tests

| Test                                   | Description                                                                                                                                         |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_existing_report_still_validates` | Load an existing completed report JSON. Run through updated `ReportValidator.validate()`. Assert no regressions (new fields are optional/nullable). |
| `test_no_shoot_dates_graceful`         | Submit report without `filming_start_date`. Assert report generates without errors, weather section uses generic data.                              |

---

## Appendix A: File Change Map

| File                                 | Changes                                                                                                                                                                         |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/modules/reports/schemas.py`     | New fields on `CreateReportRequest`, `LocationRanking`, `IncentiveEstimate`, `WeatherLogistic`, `ExecutiveSummary`. New `ShootWindow` model.                                    |
| `app/modules/reports/service.py`     | `_compute_shoot_months()`, `_classify_season()`, updated `_load_analysis_datasets()` (weather, stacking map), pass derived data to validator.                                   |
| `app/modules/reports/validator.py`   | `_patch_stacking_logic()`, `_patch_weather_risk()`, `_patch_eligibility()`, updated `validate()`.                                                                               |
| `app/modules/scripts/service.py`     | Updated `PRODUCTION_ANALYSIS_PROMPT` (~5 new rule sections), `_compact_datasets_for_prompt()` whitelist update, `generate_production_analysis()` injects derived data sections. |
| `app/modules/scripts/schemas.py`     | No changes (extIntRatio already captured).                                                                                                                                      |
| `app/modules/incentives/service.py`  | Updated field maps for new columns.                                                                                                                                             |
| `app/modules/incentives/schemas.py`  | New optional fields: `scope`, `parentTerritory`, `stackableWith`, etc.                                                                                                          |
| `app/templates/pdf/report_base.html` | Render stacking notes, weather risk highlights, eligibility flags, shoot window info.                                                                                           |
| `alembic/versions/`                  | 3-5 new migrations (regional fields, weather table, nationality fields, seed data).                                                                                             |
| `tests/test_reports_service.py`      | New tests for shoot month computation.                                                                                                                                          |
| `tests/test_reports_validator.py`    | New tests for weather, stacking, eligibility validation.                                                                                                                        |

---

## Appendix B: Eko Vibes Example — Expected Output After Fix

For the Eko Vibes production shooting in Gauteng, February, with 70% exterior scenes:

**Before (current):**

- UK section shows 34% AVEC, no regional stacking.
- Weather table says "February is Gauteng's rainy season" — buried in section 11.
- No shoot date awareness.
- No eligibility check.

**After (with all 5 gaps fixed):**

```
Territory: South Africa (Gauteng)
Score: 62 → 54 (weatherRiskImpact: -8)
keyRisks: [
  "⚠️ 72% exterior scenes — weather delays will affect majority of shooting schedule",
  "⚠️ February shoot overlaps with Gauteng's peak rainy season — expect 3-5 weather delay days",
  "Budget £18,000-£30,000 weather contingency for February shoot",
  "DTIC payment timeline 9-15 months — budget cash flow accordingly"
]

Territory: Scotland
incentiveEstimates: [
  { program: "AVEC", rate: "34%", scope: "national", eligibilityStatus: "qualified" },
  { program: "Creative Scotland Production Growth Fund", rate: "up to £500K", scope: "regional",
    stackingNote: "Stacks on AVEC — additional regional funding for productions shooting in Scotland" }
]

executiveSummary.shootWindow: {
  months: ["Feb", "Mar"],
  weatherNote: "Shoot window overlaps with rainy season in South Africa. UK and Malta unaffected."
}

executiveSummary.keyInsights:
"Malta offers the highest rebate (40%) but your February shoot window in Gauteng carries
significant weather risk — 72% of scenes are exterior and February is peak rainy season.
Budget £18-30K contingency. UK qualifies for 34% AVEC; if any shooting days move to Scotland,
Creative Scotland fund can stack an additional grant on top."
```

---

_End of implementation guide._
