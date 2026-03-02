# Production Analysis Engine — Backend Specification

**Purpose:** This document defines the full behaviour of the Production Analysis Engine — what users submit, what the AI must produce, and which sections are gated behind paid plans. It is written for the backend team building the AI report generation pipeline.

---

## 1. Overview

The Production Analysis Engine is the core feature of Scripteligence / Prodculator. A user submits a film or TV script alongside a set of production metadata. The backend AI processes both inputs and returns a structured multi-section intelligence report covering global territory rankings, tax incentive estimates, crew costs, comparable productions, weather and logistics, and funding opportunities.

The report has two modes:

| Mode | Who sees it | Triggered by |
|------|-------------|--------------|
| **Free Preview** | Unauthenticated or free-plan users | One-time email gate |
| **Full Report** | Authenticated paid users | Logged-in report creation flow |

Free previews return reduced data with explicit `isAssessmentOnly: true` flags. Full reports return complete data across all sections.

---

## 2. User Inputs

The frontend collects the following fields before sending to the backend. All fields marked **required** must be present for the AI to run. Optional fields improve AI accuracy but are not blockers.

### 2.1 Script File (required)
- **Accepted formats:** PDF, DOCX, TXT
- **Max size:** 10 MB
- The AI must parse the script to extract:
  - Implied shooting locations (interior/exterior, specific cities or regions, historical periods)
  - Technical and production complexity (night shoots, stunts, VFX sequences, water work, aerial)
  - Number of speaking roles and extras
  - Estimated shooting days (derived from scene count and complexity)
  - Narrative tone (gritty, stylised, naturalistic, period, contemporary)

### 2.2 Project Metadata

| Field | Type | Options / Notes | Required |
|-------|------|-----------------|----------|
| `title` | string | Free text project name | ✅ |
| `genre` | string[] | Multi-select: Drama, Thriller, Sci-Fi, Horror, Comedy, Romance, Action, Adventure, Fantasy, Mystery, Documentary, Biopic, Period, Western, Animation, Musical, Crime, War, Sports, Family | ✅ |
| `budgetRange` | enum | `<500k`, `500k-2m`, `2m-5m`, `5m-15m`, `15m-30m`, `30m+` (GBP) | ✅ |
| `format` | enum | Feature Film, Short, TV Series, Limited Series, Mini-Series, Documentary, Docuseries, Animated Feature, Animation Series, Commercial, Music Video, Interactive, VR | ✅ |
| `country` | enum | UK, Canada, USA, Australia, Malta, Ireland, France, Germany, Spain, Czech Republic, Hungary, Other | ✅ |
| `stateProvince` | string | USA states / Canadian provinces / Australian states — only collected when country is USA, Canada, or Australia | conditional |
| `locationStrategy` | enum | `domestic` (shooting in home country only), `open` (open to international), `international` (specifically seeking international locations) | ✅ |
| `territoriesConsidering` | string[] | Chips: UK, France, Malta, Hungary, Czech Republic, Spain, Italy, Georgia (USA), New Mexico, New York, British Columbia, Australia, New Zealand, South Africa, Portugal, Morocco, Serbia, Romania, Open to all | optional |
| `productionPriority` | enum | `incentive` (maximise rebate), `full` (financial + creative + quality balance — default), `location` (creative/location fit first) | ✅ |
| `filmingStartDate` | date string | ISO date — affects seasonal and crew availability scoring | optional |
| `filmingDuration` | number | Weeks on set — used to estimate spend thresholds | optional |
| `cameraEquipment` | string[] | Multi-select: ARRI Alexa 35, RED V-RAPTOR, Sony VENICE 2, Film 35mm, Blackmagic Cinema, Canon C70, Sony FX9, Panavision, IMAX, DJI Drone, GoPro, iPhone, Sony Alpha, Sony A7S III, Canon EOS R5, Phantom High Speed, Kinefinity Terra, Other | optional |
| `crewSize` | number | Estimated total crew headcount | optional |
| `principalCast` | number | Number of lead actors | optional |
| `supportingCast` | number | Number of supporting actors | optional |
| `targetAudience` | string | Free text e.g. "18-34, arthouse" | optional |
| `language` | string | Primary language(s) and dialects | optional |

### 2.3 Derived / Computed Inputs
The AI may also use the following values computed from the above:

- **Production intensity:** derived from `crewSize`, `filmingDuration`, `budgetRange`
- **Script complexity:** derived from scene count and the AI script parse — Low / Medium / High / Very High
- **Shooting days estimate:** extracted from script parse or approximated from `filmingDuration`

---

## 3. Report Structure — What the AI Must Return

The backend must return a single JSON object conforming to the `ScriptAnalysis` interface. Each section is described below with field-level detail.

---

### Section 1: Script Intelligence Summary

**Plan access:** Free and paid
**Purpose:** High-level characterisation of the project extracted from script parse + metadata.

| Field | Type | Description |
|-------|------|-------------|
| `genre` | string | Derived primary genre (may differ from user-selected if AI infers differently) |
| `tone` | string | Narrative tone e.g. "Gritty urban realism", "Stylised period thriller" |
| `scale` | string | Production scale label e.g. "Mid-budget international feature" |
| `complexity` | `"Low" \| "Medium" \| "High" \| "Very High"` | Calculated from shooting days and technical demands |

---

### Section 2: Location Rankings

**Plan access:** Free (top 3, `isAssessmentOnly: true`) and paid (up to 15 territories, full data)
**Purpose:** Ranked list of the best-matched global territories for this production.

Each item in `locationRankings[]` must contain:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Territory name e.g. "United Kingdom", "Malta", "British Columbia" |
| `country` | string | Parent country |
| `score` | number | Overall match score 0–100 |
| `costEfficiency` | number | 0–100 — how far below global average the production costs are |
| `crewDepth` | number | 0–100 — skilled crew availability for the project's scale and genre |
| `infrastructure` | number | 0–100 — quality and availability of studios, equipment houses, post facilities |
| `incentiveStrength` | number | 0–100 — value of available tax rebate or cash incentive |
| `currencyAdvantage` | number | 0–100 — exchange rate benefit vs GBP baseline |
| `reasoning` | string[] | 3–5 bullet points explaining why this territory ranked here |
| `isAssessmentOnly` | boolean | `true` for free-preview results |

**Scoring notes for the AI:**
- `productionPriority` directly weights the scoring:
  - `incentive` → `incentiveStrength` weight ×2
  - `location` → `crewDepth` and `infrastructure` weighted higher
  - `full` → equal weighting (default)
- `territoriesConsidering` should bias the selection toward named territories; if "Open to all" is selected, the AI chooses the globally optimal set
- `filmingStartDate` and `filmingDuration` affect seasonal relevance in the score

---

### Section 3: Tax Incentive Estimates

**Plan access:** Free (blurred, labelled "Estimate Only") and paid (full detail)
**Purpose:** Per-territory breakdown of applicable tax credits, cash rebates, and incentive programmes.

Each item in `incentiveEstimates[]` must contain:

| Field | Type | Description |
|-------|------|-------------|
| `territory` | string | Territory name |
| `program` | string | Incentive programme name e.g. "AVEC/IFTC", "UK Film Tax Relief", "Malta MFTI Cash Rebate" |
| `rate` | string | Rebate percentage e.g. "25%", "40%" |
| `cap` | string | Maximum rebate available e.g. "No cap", "€500,000" |
| `qualifyingSpend` | string | Minimum local spend to qualify e.g. "£1M minimum UK spend" |
| `estimatedRebate` | string | Estimated rebate in GBP based on the user's `budgetRange` e.g. "£1,250,000" |
| `requirements` | string[] | 3–5 eligibility criteria bullet points |
| `disclaimer` | string | Always: `"Estimate only. Final eligibility depends on official approval."` |
| `dataSource` | string | Source attribution e.g. "Prodculator backend datasets" |
| `lastUpdated` | string | ISO timestamp of when this incentive data was last verified |

**AI guidance:**
- Use the user's `budgetRange` mid-point to estimate the absolute rebate amount
- Only include territories that were ranked in Section 2
- Flag programmes that are known to have slow payment timelines or documentary-heavy requirements
- If a territory has multiple stacking incentives (e.g. national + regional), list each separately

---

### Section 4: Crew & Costs

**Plan access:** **Paid only (Professional, Studio, B2B)** — free users see a locked overlay
**Purpose:** Crew availability, daily rates, and quality assessment per territory.

Each item in `crewInsights[]` must contain:

| Field | Type | Description |
|-------|------|-------------|
| `territory` | string | Territory name |
| `availability` | `"High" \| "Medium" \| "Low"` | Skilled crew availability for this project's scale |
| `costVsUSD` | string | Daily all-crew cost benchmark e.g. "£3,200/day", "€2,800/day" |
| `qualityRating` | number | 1–5 star rating for crew quality in this territory |
| `specialties` | string[] | Top 5 crew roles this territory is particularly strong in e.g. "Director of Photography", "Production Designer", "Stunt Coordinator" |
| `tradeoff` | string | One sentence summarising the key trade-off e.g. "45% lower crew costs vs UK but limited depth for large-scale productions above 80 crew" |

**AI guidance:**
- Cross-reference `crewSize` and `format` — a 10-episode TV series in a small territory may exceed local crew depth
- `cameraEquipment` selections should influence which territories have specialist equipment operators available
- `filmingStartDate` affects availability (consider local production calendar peaks)

---

### Section 5: Comparable Productions

**Plan access:** **Paid only (Professional, Studio, B2B)** — free users see a locked overlay
**Purpose:** Real-world productions that share genre, budget range, and territory profile with the submitted project.

Each item in `comparables[]` must contain:

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Production title |
| `genre` | string | Genre label |
| `budgetRange` | string | Budget band e.g. "£5M–£15M" |
| `visualScale` | string | Scope description e.g. "Intimate drama", "Large-scale action", "International co-production" |
| `location` | string | Primary filming territory |
| `year` | number | Year of production |
| `source` | string | Data attribution e.g. "IMDb", "TMDB", "Industry database" |

**AI guidance:**
- Match on at least 2 of: genre, budget band, territory, format
- Prefer productions from the last 5 years where possible
- Include at least one production that used a territory ranked highly in Section 2 to demonstrate real-world proof of concept

---

### Section 6: Weather & Logistics

**Plan access:** **Paid only (Professional, Studio, B2B)** — free users see a locked overlay
**Purpose:** Seasonal filming windows, weather risk, and practical logistics per territory.

Each item in `weatherLogistics[]` must contain:

| Field | Type | Description |
|-------|------|-------------|
| `territory` | string | Territory name |
| `bestMonths` | string[] | Optimal months for exterior filming e.g. `["Apr", "May", "Sep", "Oct"]` |
| `weatherRisk` | `"Low" \| "Medium" \| "High"` | Risk of weather-related production delays |
| `infrastructure` | string | Production support summary e.g. "Strong studio network, multiple post houses within 30 min" |
| `travelVisa` | string | Practical notes on crew travel, work permits, and visa requirements |
| `avgTempRange` | string | Optional — temperature range in best months e.g. "18–26°C" |
| `avgRainfall` | string | Optional — rainfall summary e.g. "Low — avg 30mm/month in summer" |
| `daylightHours` | string | Optional — useful for scheduling exterior scenes e.g. "14–16 hrs in peak months" |
| `seasonalConsiderations` | string | Optional — any key seasonal notes e.g. "Avoid July–August peak tourist season in Malta" |

**AI guidance:**
- If `filmingStartDate` is provided, weight the `bestMonths` assessment against the planned shoot window and flag any mismatches
- Include permit and location-access nuances where known (e.g. Venice restricts large crew movement in peak season)

---

### Section 7: Funding & Festivals

**Plan access:** **Paid only (Professional, Studio, B2B)** — free users see a locked overlay
**Purpose:** Grant opportunities and festival recommendations matched to the project's genre, budget, and format.

Each item in `fundingOpportunities[]` must contain:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"Fund" \| "Festival"` | Category of opportunity |
| `name` | string | Fund or festival name |
| `genre` | string[] | Genres this fund/festival accepts |
| `deadline` | string | Application or submission deadline (human-readable string) |
| `notes` | string | Funding amount and organisation (for funds) or location and tier (for festivals) |
| `website` | string | Optional — URL for the fund or festival |
| `tier` | string | Optional (festivals only) — `"A-List"`, `"Tier 2"`, `"Regional"`, `"Specialized"` |

**AI guidance:**
- Match funds by `genre`, `budgetRange`, `format`, and `country`
- Match festivals by `genre`, `format`, and script tone/subject matter extracted from the parse
- Prioritise upcoming deadlines (within 6 months of report generation date)
- Include a mix: at least 2 grants and at least 3 festivals where data permits
- Cross-reference with the platform's pre-loaded festival and grant datasets from the admin panel

---

## 4. API Contract

### 4.1 Report Creation (async)

```
POST /api/reports
Body: { title, scriptPath, metadata: { ...all fields above } }
Response: { reportId, status: "processing" }
```

### 4.2 Status Polling

```
GET /api/reports/{reportId}/status
Response: { status: "processing" | "complete" | "failed", progress?: number }
```

The frontend polls this every 3 seconds with a 180-second timeout. If the backend exceeds 3 minutes, the job is considered failed.

### 4.3 Fetch Completed Report

```
GET /api/reports/{reportId}
Response: {
  id: string,
  title: string,
  reportType: "preview" | "paid",
  createdAt: string,
  analysis: ScriptAnalysis   // see full type below
}
```

### 4.4 Full Response Type

```typescript
interface ScriptAnalysis {
  genre: string;
  tone: string;
  scale: string;
  complexity: "Low" | "Medium" | "High" | "Very High";
  locationRankings: LocationRanking[];
  incentiveEstimates: IncentiveEstimate[];
  crewInsights: CrewInsight[];           // empty array for preview
  comparables: ComparableProduction[];   // empty array for preview
  weatherLogistics: WeatherLogistics[];  // empty array for preview
  fundingOpportunities: FundingOpportunity[]; // empty array for preview
}

interface LocationRanking {
  name: string;
  country: string;
  score: number;                // 0–100
  costEfficiency: number;       // 0–100
  crewDepth: number;            // 0–100
  infrastructure: number;       // 0–100
  incentiveStrength: number;    // 0–100
  currencyAdvantage: number;    // 0–100
  reasoning: string[];          // 3–5 bullet points
  isAssessmentOnly?: boolean;   // true for preview reports
}

interface IncentiveEstimate {
  territory: string;
  program: string;
  rate: string;
  cap: string;
  qualifyingSpend: string;
  estimatedRebate: string;
  requirements: string[];
  disclaimer: string;
  dataSource: string;
  lastUpdated: string;          // ISO timestamp
}

interface CrewInsight {
  territory: string;
  availability: "High" | "Medium" | "Low";
  costVsUSD: string;
  qualityRating: number;        // 1–5
  specialties: string[];        // up to 5 roles
  tradeoff: string;
}

interface ComparableProduction {
  title: string;
  genre: string;
  budgetRange: string;
  visualScale: string;
  location: string;
  year: number;
  source: string;
}

interface WeatherLogistics {
  territory: string;
  bestMonths: string[];
  weatherRisk: "Low" | "Medium" | "High";
  infrastructure: string;
  travelVisa: string;
  avgTempRange?: string;
  avgRainfall?: string;
  daylightHours?: string;
  seasonalConsiderations?: string;
}

interface FundingOpportunity {
  type: "Fund" | "Festival";
  name: string;
  genre: string[];
  deadline: string;
  notes: string;
  website?: string;
  tier?: string;                // festivals only
}
```

---

## 5. Free Preview vs Full Report — Behaviour Differences

| Behaviour | Free Preview | Full Report |
|-----------|-------------|-------------|
| `reportType` in response | `"preview"` | `"paid"` |
| `locationRankings` returned | Max 3, all with `isAssessmentOnly: true` | Up to 15, `isAssessmentOnly` omitted or `false` |
| `incentiveEstimates` returned | All fields present but frontend renders blurred | All fields, fully visible |
| `crewInsights` returned | Empty array `[]` | Full array |
| `comparables` returned | Empty array `[]` | Full array |
| `weatherLogistics` returned | Empty array `[]` | Full array |
| `fundingOpportunities` returned | Empty array `[]` | Full array |
| Script file processed by AI | No — preview is generated client-side from metadata only | Yes — full AI parse |
| Stored in user account | No | Yes |
| PDF export enabled | No | Yes |

> **Important:** Free previews are generated without sending the script to the AI. The backend only receives the metadata for preview mode and returns estimates. The full AI parse (scene extraction, complexity scoring, tone analysis) only happens for authenticated paid report requests.

---

## 6. AI Prompt Guidance — Report Quality Standards

The AI generating each section should be instructed to:

1. **Be specific, not generic.** "Malta offers a 40% cash rebate with a €3.5M cap under the MFTI programme" is useful. "Malta has good incentives" is not.

2. **Use the script parse.** Reasoning in `locationRankings` should reference details extracted from the actual script — e.g. "The script's three key harbour sequences align with Malta's established maritime shooting infrastructure."

3. **Reflect `productionPriority`.** A user who selected `incentive` as their priority should receive rankings and reasoning that foreground financial return, not creative fit.

4. **Flag uncertainty honestly.** If an incentive programme has uncertain availability or is under review, include that in `requirements[]` or the `disclaimer`.

5. **Maintain consistent territory coverage.** All sections (rankings, incentives, crew, weather) should reference the same set of territories. Do not introduce new territories in later sections that did not appear in the rankings.

6. **Return valid, parseable JSON.** The frontend maps the response directly to typed interfaces. Malformed or missing fields will cause display errors. Validate output against the type definitions in Section 4.4 before returning.

---

## 7. Data Sources the AI Should Reference

The backend dataset layer should provide the AI with:

- **Tax incentive programmes:** Territory, programme name, rate, cap, qualifying spend, eligibility rules, last verified date — managed via the admin `IncentiveDataManager`
- **Crew cost benchmarks:** Territory daily rates by department, crew depth ratings — managed via admin `CrewCostsManager`
- **Festival data:** Name, tier, genres accepted, typical deadline windows — managed via admin `FestivalsManager`
- **Grant data:** Name, genres, funding amount, organisation, deadline windows — managed via admin `GrantsManager`
- **Comparable productions:** Title, genre, budget, territory, year — managed via admin `ComparableProductionsManager`

All of the above datasets are maintained through the Prodculator admin panel and should be passed to the AI at inference time as structured context, not embedded in the prompt as static text. This allows the admin team to update data without redeploying the AI pipeline.

---

*Last updated: 2026-03-01*
*Maintained by: Prodculator frontend team*
