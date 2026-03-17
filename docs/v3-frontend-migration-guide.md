# V3 Frontend Migration Guide

This document details every change the frontend must make to align with the v3 backend implementation.

---

## 1. Report Creation — Input Changes

**Endpoint:** `POST /api/reports` (multipart/form-data)

The request body is sent as a JSON string in the `body` field, with an optional `script_file` file upload.

### Removed Fields

| Field | Action |
|-------|--------|
| `budget_range` | **Removed entirely.** Do not send this field. |

### New Required Fields

| Field | Type | Notes |
|-------|------|-------|
| `budget_amount` | `float` | Actual budget figure in the specified currency. Must be > 0. |
| `budget_currency` | `string` | One of: `"GBP"`, `"USD"`, `"EUR"`, `"ZAR"`, `"CAD"`, `"AUD"`, `"NGN"`, `"HUF"`, `"CZK"`, `"MAD"`, `"NZD"`, `"RON"`, `"RSD"`, `"OTHER"`. Defaults to `"GBP"`. |

### Example: v2 vs v3 Payload

**v2 (old):**
```json
{
  "script_title": "My Script",
  "report_type": "paid",
  "genre": ["Drama"],
  "budget_range": "2m-5m",
  "format": "Feature Film",
  "country": "UK",
  "location_strategy": "open",
  "production_priority": "full"
}
```

**v3 (new):**
```json
{
  "script_title": "My Script",
  "report_type": "paid",
  "genre": ["Drama"],
  "budget_amount": 3000000,
  "budget_currency": "GBP",
  "format": "Feature Film",
  "country": "UK",
  "location_strategy": "open",
  "production_priority": "full"
}
```

### Unchanged Fields (for reference)

All other fields remain the same:

- `script_title` (string, required)
- `report_type` (`"preview"` | `"paid"` | `"b2b"`, default `"paid"`)
- `genre` (list of strings, required)
- `format` (string enum, required) — options: `"Feature Film"`, `"Short Film"`, `"TV Series"`, `"Limited Series"`, `"Mini-Series"`, `"Documentary"`, `"Docuseries"`, `"Animation"`, `"Animated Feature"`, `"Animation Series"`, `"Commercial"`, `"Music Video"`, `"Interactive"`, `"VR"`
- `country` (string, required)
- `location_strategy` (`"domestic"` | `"open"` | `"international"`, required)
- `production_priority` (`"incentive"` | `"full"` | `"location"`, default `"full"`)
- `email` (string, optional — required for unauthenticated preview reports)
- `state_province` (string, optional)
- `territories_considering` (list of strings, optional)
- `filming_start_date` (string, optional)
- `filming_duration` (int, optional)
- `camera_equipment` (list of strings, optional)
- `crew_size` (int, optional)
- `principal_cast` (int, optional)
- `supporting_cast` (int, optional)
- `target_audience` (string, optional)
- `language` (string, optional)
- `producer_country` (string, optional)
- `co_production_status` (`"sole_producer"` | `"co_production_treaty"` | `"co_production_informal"` | `"undecided"`, optional)

### Form Replacement

Replace the budget range dropdown/selector with:
- A **numeric input** for `budget_amount` (e.g., a currency-formatted number field)
- A **currency dropdown** for `budget_currency` with the 14 supported currencies

---

## 2. Response Schema Changes

All report data is returned in the `analysis` field of `ReportResponse` (a dict conforming to `ScriptAnalysis`).

### 2a. Executive Summary — New Fields

The `executiveSummary` object now includes:

| Field | Type | Description |
|-------|------|-------------|
| `headlineNetBudget` | `string \| null` | The single most important number — net effective budget after incentives. Display prominently (e.g., large font at top of report). Example: `"£2,150,000"` |
| `actionTimeline` | `ActionTimelineItem[] \| null` | Ordered list of next steps with deadlines. Each item has: `action` (string), `deadline` (string \| null), `note` (string \| null) |
| `keyFlags` | `string[] \| null` | Max 3 top-level risk/opportunity warnings. Display as alert banners. |

**Removed from Executive Summary:**
| Field | Action |
|-------|--------|
| `budgetRange` | **Removed.** Stop reading/displaying this field. |

### 2b. Location Rankings — New Fields

Each item in `locationRankings` now includes:

| Field | Type | Description |
|-------|------|-------------|
| `incentiveReliability` | `int \| null` | 0-100 score for the 6th scoring dimension. Display alongside the other 5 dimension scores. |
| `bankabilityLabel` | `string \| null` | One of `"BANKABLE"`, `"VERIFY FIRST"`, `"NOT BANKABLE"`. Display as a colored badge: green for BANKABLE, amber/yellow for VERIFY FIRST, red for NOT BANKABLE. |

The scoring system is now **6 dimensions** (was 5):
1. Cost Efficiency (`costEfficiency`)
2. Crew Depth (`crewDepth`)
3. Infrastructure (`infrastructure`)
4. Incentive Strength (`incentiveStrength`)
5. Currency Advantage (`currencyAdvantage`)
6. **Incentive Reliability** (`incentiveReliability`) — **NEW**

### 2c. Incentive Estimates — New Field

Each item in `incentiveEstimates` now includes:

| Field | Type | Description |
|-------|------|-------------|
| `bankabilityLabel` | `string \| null` | Same as location rankings — `"BANKABLE"`, `"VERIFY FIRST"`, or `"NOT BANKABLE"`. |

### 2d. Financial Scenarios — Expanded (6-Step Working)

Each `FinancialScenario` in `financialAnalysis.budgetScenarios` now includes the full 6-step calculation:

| Field | Type | Description |
|-------|------|-------------|
| `totalBudget` | `string \| null` | Starting budget figure (e.g., `"£3,000,000"`) |
| `qualifyingSpendPct` | `string \| null` | Percentage that qualifies (e.g., `"80%"`) |
| `qualifyingSpend` | `string \| null` | Budget × qualifying % (e.g., `"£2,400,000"`) |
| `atlDeduction` | `string \| null` | Above-the-line deduction (e.g., `"£600,000"`) |
| `netQualifyingSpend` | `string \| null` | After ATL deduction (e.g., `"£1,800,000"`) |
| `programme` | `string \| null` | Incentive programme name |
| `rateGross` | `string \| null` | Gross rebate rate (e.g., `"25%"`) |
| `rateNet` | `string \| null` | Effective net rate after deductions |
| `grossRebate` | `string \| null` | Gross rebate amount |
| `netRebate` | `string \| null` | Net rebate amount |
| `netBudget` | `string \| null` | Final net budget after rebate |
| `notes` | `string \| null` | Additional notes or caveats |

**Legacy fields still present** (for transition): `localSpend`, `rebateRate`. These may be removed in a future version.

**Recommended UI:** Display as a stepped breakdown showing each calculation step, not just a simple table.

### 2e. Script Analysis — New Field

The top-level `analysis` object now includes:

| Field | Type | Description |
|-------|------|-------------|
| `sectionExplainers` | `dict[string, string] \| null` | Hardcoded plain-English descriptions for each report section. Keys are section identifiers (e.g., `"locationRankings"`, `"incentiveEstimates"`, `"financialAnalysis"`). Display as info/help text below each section heading. |

### 2f. Scoring Methodology — Updated

`scoringMethodology.dimensions` now returns **6 dimensions** instead of 5, including the new "Incentive Reliability" dimension.

---

## 3. UI Elements to Add

### 3a. Headline Net Budget
- Display `executiveSummary.headlineNetBudget` as the most prominent figure in the report
- Suggested styling: large font, highlighted/boxed, near the top of the executive summary

### 3b. Bankability Labels
- Display on location cards, territory deep dives, and incentive estimate cards
- Color coding:
  - `"BANKABLE"` → green badge
  - `"VERIFY FIRST"` → amber/yellow badge
  - `"NOT BANKABLE"` → red badge

### 3c. Key Flags
- Display `executiveSummary.keyFlags` (max 3 items) as alert/warning banners
- Suggested styling: colored alert boxes near the top of the report

### 3d. Action Timeline
- Display `executiveSummary.actionTimeline` as a timeline or checklist
- Each item has `action`, `deadline` (optional), and `note` (optional)

### 3e. Incentive Reliability Score
- Add as the 6th bar/metric in location ranking score breakdowns
- Display alongside costEfficiency, crewDepth, infrastructure, incentiveStrength, currencyAdvantage

### 3f. Section Explainers
- Display `sectionExplainers` values as help/info text below each section heading
- These are static descriptions — no formatting or variable interpolation needed on the frontend

### 3g. 6-Step Financial Working
- Replace the simple financial table with a stepped breakdown
- Show each step: Total Budget → Qualifying Spend → ATL Deduction → Net Qualifying Spend → Rebate Rate → Net Rebate → Net Budget
- Fall back to legacy `localSpend`/`rebateRate` display if the v3 fields are null

---

## 4. UI Elements to Remove

| Element | Reason |
|---------|--------|
| Budget range dropdown/selector | Replaced by `budget_amount` + `budget_currency` inputs |
| `budgetRange` display in executive summary | Field removed from schema |

---

## 5. API Endpoints (Unchanged)

All endpoints remain the same:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/reports` | Required | Create a report (multipart: `body` JSON + `script_file`) |
| `GET` | `/api/reports` | Required | List user's reports |
| `GET` | `/api/reports/{id}` | Required | Get a specific report |
| `GET` | `/api/reports/{id}/status` | Required | Poll report processing status |
| `GET` | `/api/reports/{id}/pdf` | Required | Get/generate PDF |
| `GET` | `/api/reports/shared/{token}` | Optional | View a shared report |

### Status Polling

The `ReportStatusResponse` is unchanged:
```json
{
  "status": "processing" | "completed" | "failed",
  "report_id": "...",
  "message": "...",
  "error": "...",
  "progress": 0-100
}
```

---

## 6. Summary of Breaking Changes

1. **`budget_range` removed** — sending it will be ignored; `budget_amount` is now required
2. **`budgetRange` removed from `executiveSummary`** — stop reading this field
3. **6 scoring dimensions** — UI displaying 5 dimensions must add Incentive Reliability
4. **Financial scenarios expanded** — new fields for 6-step working; legacy fields still present but deprecated
