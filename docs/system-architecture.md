# Prodculator Backend — Complete System Architecture

> **Last Updated:** March 14, 2026  
> **Codebase Branch:** `feat/s3-storage`  
> **Stack:** Python 3.11 · FastAPI · SQLModel/SQLAlchemy · Anthropic Claude · Stripe · AWS S3 · Redis · SendGrid · Firebase Auth

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Core Infrastructure](#3-core-infrastructure)
4. [Authentication & Authorization](#4-authentication--authorization)
5. [How Scriptelligence Works](#5-how-scriptelligence-works-the-core-product)
6. [Report Generation Pipeline](#6-report-generation-pipeline)
7. [Data Scraper & Sync Engine](#7-data-scraper--sync-engine)
8. [Admin System](#8-admin-system)
9. [Payments & Subscriptions](#9-payments--subscriptions)
10. [Auxiliary Modules](#10-auxiliary-modules)
11. [Data Model](#11-data-model)
12. [API Routes Summary](#12-api-routes-summary)
13. [Configuration & Environment](#13-configuration--environment)
14. [Deployment](#14-deployment)

---

## 1. System Overview

**Prodculator** (also referred to as **Scriptelligence**) is a Production Intelligence Platform for the film and television industry. Its core value proposition:

> _Upload a screenplay, provide basic project metadata, and receive a comprehensive, AI-generated production intelligence report — covering optimal filming territories, tax incentive analysis, crew cost benchmarks, comparable productions, weather logistics, festival/grant opportunities, and financial modelling._

The backend is a **FastAPI** application that orchestrates:

- **Script Analysis** — AI-powered extraction of production signals (locations, budget indicators, VFX requirements, challenges) from uploaded screenplays using Anthropic Claude.
- **Production Analysis** — A second AI pass that cross-references the script signals with curated admin datasets (incentives, crew costs, festivals, grants, weather, comparables) to produce a territory-ranked intelligence report.
- **PDF Report Generation** — HTML-to-PDF rendering via WeasyPrint, stored in AWS S3 with presigned URL access.
- **Admin Data Management** — CRUD interfaces for all reference datasets that feed the AI analysis.
- **Automated Data Scraping** — Scheduled scraper that fetches and extracts structured data from government sources, film commissions, and statistics bureaus.
- **User Management & Payments** — Email/password + Google (Firebase) auth, Stripe payments, subscription management.

---

## 2. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                          FRONTEND (React/Vite)                       │
│   Upload Script → Fill Metadata → Generate Report → View/Download    │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ HTTPS (JWT Bearer)
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        FASTAPI BACKEND                               │
│                                                                      │
│  ┌──────────┐  ┌────────────┐  ┌───────────┐  ┌──────────────────┐  │
│  │   Auth   │  │  Scripts   │  │  Reports  │  │  Admin / CRUD    │  │
│  │  Module  │  │  Module    │  │  Module   │  │  Modules         │  │
│  └────┬─────┘  └─────┬──────┘  └─────┬─────┘  └────────┬─────────┘  │
│       │              │               │                  │            │
│  ┌────▼──────────────▼───────────────▼──────────────────▼─────────┐  │
│  │                     Core Services                              │  │
│  │  DatabaseClient · StorageClient · Security · Scheduler · FX   │  │
│  └──────┬──────────────┬─────────────────┬────────────────────────┘  │
└─────────┼──────────────┼─────────────────┼───────────────────────────┘
          │              │                 │
    ┌─────▼────┐  ┌──────▼──────┐  ┌──────▼──────┐
    │ SQLite / │  │  Anthropic  │  │   AWS S3    │
    │ Postgres │  │   Claude    │  │  (Reports)  │
    └──────────┘  └─────────────┘  └─────────────┘
          │
    ┌─────▼────┐  ┌─────────────┐  ┌─────────────┐
    │  Redis   │  │   Stripe    │  │  SendGrid   │
    │ (Cache)  │  │ (Payments)  │  │  (Email)    │
    └──────────┘  └─────────────┘  └─────────────┘
```

---

## 3. Core Infrastructure

### 3.1 Database (`app/core/db.py`, `app/core/database_client.py`)

- **ORM:** SQLModel (Pydantic + SQLAlchemy) with sync sessions.
- **Database:** SQLite for local dev, PostgreSQL for production.
- **DatabaseClient:** A Supabase-compatible query builder that wraps SQLAlchemy. Provides a fluent `.table("reports").select("*").eq("id", report_id).single().execute()` API so the codebase can use a consistent query pattern regardless of backend.
- **Migrations:** Alembic with a rich version history (~25+ migrations).
- **Auto-schema:** On startup, `init_db()` runs `SQLModel.metadata.create_all(engine)` when `AUTO_CREATE_DB_SCHEMA=True`.

### 3.2 Storage (`app/core/storage.py`)

Dual-mode storage client:

| Mode      | Trigger                                | Behavior                                                               |
| --------- | -------------------------------------- | ---------------------------------------------------------------------- |
| **S3**    | `AWS_S3_BUCKET_NAME` + credentials set | Uses `boto3` to upload/download under `<prefix>/<bucket_label>/<path>` |
| **Local** | No AWS credentials                     | Falls back to `./storage/<bucket>/` filesystem                         |

- **Reports bucket** — PDF files stored as `reports/<user_id>/<report_id>.pdf`.
- **Presigned URLs** — S3 keys are stored in the DB. Fresh presigned URLs (15-min TTL) are generated at API response time, never at upload time — this prevents stale URLs.

### 3.3 Caching (`app/core/cache.py`)

- **Redis** — async client used for token blocklist (revoked JWTs) and FX rate caching (24h TTL).
- Graceful degradation — if Redis is unavailable, auth still works (token validity checked via signature only).

### 3.4 Scheduler (`app/core/scheduler.py`)

- **APScheduler** `BackgroundScheduler` with a daily cron trigger.
- Checks `sync_settings` table for enabled resource types and runs scrapers when `next_scheduled ≤ now`.
- Schedule options: `monthly`, `quarterly`, `biannual`, `annual`.

### 3.5 FX Service (`app/modules/fx/service.py`)

- Real-time exchange rates via **ExchangeRate-API** with Redis caching.
- Hardcoded GBP-base fallback rates for offline/dev environments.
- Used to FX-enrich crew/cast cost data (converting local currencies → GBP) before injecting into AI prompts.

---

## 4. Authentication & Authorization

### 4.1 User Auth (`app/modules/auth/`)

Two auth paths:

1. **Email/Password** — Signup creates a user row + issues JWT pair (access + refresh). Password hashed with Argon2.
2. **Google OAuth** — Frontend uses Firebase `signInWithPopup`, sends the Firebase ID token to `POST /api/auth/google`, backend verifies via `firebase-admin`, upserts user, issues JWT pair.

**JWT mechanics:**

- `HS256` signed with `JWT_SECRET_KEY`.
- Access token: 1 hour TTL. Refresh token: 14 days.
- Token revocation via Redis blocklist (on sign-out).
- `get_current_user` dependency: decode JWT → check blocklist → load user profile → check `is_blocked`.

### 4.2 Admin Auth (`app/modules/admin/`)

Separate auth flow for admins:

- Email/password only (Argon2 hashed).
- Role-based access control (RBAC) with 4 roles: `master_admin`, `senior_admin`, `data_admin`, `support_admin`.
- Permissions are granular: `canManageAdmins`, `canEditIncentiveData`, `canEditCrewCosts`, `canViewBusinessMetrics`, etc.
- Enforced via `RequirePermission` dependency.

---

## 5. How Scriptelligence Works (The Core Product)

Scriptelligence is the AI-powered intelligence engine at the heart of Prodculator. It transforms a raw screenplay + project metadata into a comprehensive production planning report. The pipeline has **two major AI stages** and a **post-processing validation layer**.

### 5.1 Stage 1: Script Analysis (`app/modules/scripts/service.py`)

**Purpose:** Extract production-relevant signals from the raw screenplay text.

#### Input

- A screenplay file (PDF, TXT, Fountain, or FDX format, up to 50MB).
- Uploaded via `POST /api/scripts/analyze`.

#### Process: Chunked Analysis

The script is processed through a **chunked AI analysis pipeline**:

1. **Text Extraction** — `pdfplumber` for PDFs, UTF-8 decode for text formats.

2. **Scene-aware Chunking** — The script text is split into chunks by detecting scene headings (`INT.`, `EXT.`, `INT/EXT.`, etc.):
   - Target chunk size: ~1,800 tokens (configurable via `SCRIPT_CHUNK_TARGET_TOKENS`).
   - Overlap between chunks: ~200 tokens for continuity.
   - Maximum chunks: 80 (configurable).
   - Large scenes are sub-split; small scenes are packed together.

3. **Per-Chunk AI Extraction** — Each chunk is sent to Claude with a constrained JSON schema (`SCRIPT_CHUNK_OUTPUT_SCHEMA`):

   ```
   For each chunk, Claude extracts:
   ├── locations[]        — name, country, territory, frequency, isMainLocation
   ├── budgetEstimate     — range (micro/low/medium/high/tentpole), indicators
   ├── productionScale    — crewSize, principalCast, supportingCast, extras, shootDays
   ├── equipment          — cameraEquipment, specialEquipment[], vfxRequirements
   ├── metadata           — genres[], format, tone, targetAudience
   └── challenges         — weather, historical, permits, stunts, animals, water,
                            night shooting, scene counts (ext/int/night/water/vfx)
   ```

4. **Aggregation** — Results from all successful chunks are merged:
   - **Locations:** Deduplicated by (territory, name, country) key; frequencies summed; `isMainLocation` OR'd across chunks.
   - **Budget:** Modal budget range across chunks (micro/low/medium/high/tentpole).
   - **Production Scale:** Weighted modal values using scale orderings.
   - **Equipment:** Modal camera type; VFX requirements weighted by order (minimal → moderate → heavy → intensive).
   - **Challenges:** Boolean flags are activated if ≥20% of chunks signal them. Scene counts (ext, int, night, water, VFX) are summed.
   - **Section Confidence:** Each section gets a confidence score based on how many chunks contributed data vs total chunks.

5. **Fallback** — If chunked analysis completely fails, a safe default `ScriptAnalysisResult` is returned.

#### Output: `ScriptAnalysisResult`

```python
ScriptAnalysisResult:
  locations: List[Location]          # Sorted by frequency, max 20
  budgetEstimate: BudgetEstimate     # range, minUSD, maxUSD, confidence, indicators
  productionScale: ProductionScale   # crew/cast sizes, shooting days
  equipment: Equipment               # camera, special equipment, VFX level
  metadata: Metadata                 # genres, format, tone, audience
  challenges: Challenges             # flags + scene signal counts
  rawResponse: str                   # JSON telemetry blob (mode, chunk stats, confidence)
```

### 5.2 Stage 2: Production Analysis (Deterministic Builder + AI Narrative Fill)

**Purpose:** Cross-reference script signals + user metadata + admin datasets → comprehensive territory-ranked intelligence report.

#### Architecture: Builder → AI → Merge → Assert

The pipeline uses a **deterministic-first** approach: `ReportBuilder` constructs a complete report skeleton from DB data (all financial figures, scores, structured fields), then the AI fills only ~15 narrative/qualitative fields via a focused prompt.

```
Datasets → ReportBuilder.build() → skeleton (narratives = null)
  → AI call (skeleton + script analysis) → narrative-only JSON
  → merge narratives into skeleton
  → compute_overall_scores() → weighted 6-dimension scoring
  → ReportValidator.assert_integrity() → lightweight checks
  → PDF → persist
```

#### Inputs

1. **Script Analysis Result** (from Stage 1) — or `None` for preview reports.
2. **Request Metadata** — User-provided project details:
   - Genre, budget range, format, country, location strategy, production priority
   - Optional: territories to consider, filming dates, crew size, producer country, co-production status
3. **Admin Datasets** — Loaded from the database:
   - Incentive programs (with FX freshness annotations)
   - Crew costs (FX-enriched to budget currency)
   - Cast costs (FX-enriched to budget currency)
   - Comparable productions, Grant opportunities, Film festivals
   - Territory weather data, Stacking map

#### ReportBuilder (`app/modules/reports/builder.py`)

Constructs the complete report skeleton deterministically. All financial data, incentive rates, scoring (3 of 6 dimensions), eligibility checks, weather risk, stacking logic, and budget scenarios are computed from DB data without any AI involvement.

**Six scoring dimensions** — 3 deterministic from DB, 3 AI-generated:
- DB: `incentiveStrength`, `incentiveReliability`, `currencyAdvantage`
- AI: `costEfficiency`, `crewDepth`, `infrastructure`

**Territory selection** uses user-submitted `territories_considering`, filtered to those with active incentive data. Supplementary-only territories are excluded from rankings.

#### AI Narrative Fill (`_NARRATIVE_FILL_PROMPT`, ~50 lines)

The AI receives the pre-built skeleton (for context) plus script analysis, and returns ONLY narrative fields: `genre`, `tone`, `scale`, `complexity`, location reasoning/advantages/risks, crew narratives, comparable descriptions, weather notes, deep dive narratives, and `alternativeStrategy`.

#### Output: Production Analysis Report

```
ScriptAnalysis Report:
├── executiveSummary        — key insights, recommended territory, budget, shoot window
├── locationRankings[]      — scored territories with sub-scores, rebates, advantages, risks
├── financialAnalysis       — budget scenarios per territory, crew cost comparison
├── territoryDeepDives[]    — detailed profiles for top 3 territories
├── incentiveEstimates[]    — per-programme rate, cap, eligibility, stacking info
├── crewInsights[]          — availability, cost, quality per territory
├── castInsights[]          — rate ranges for cast roles per territory
├── comparables[]           — similar productions with relevance descriptions
├── weatherLogistics[]      — monthly weather, shoot window risk, delay estimates
├── fundingOpportunities[]  — grants + festivals matching the project
├── alternativeStrategy     — multi-territory or alternative approach recommendation
├── attributions[]          — data source citations per territory
└── sectionExplainers       — hardcoded explanatory text per section
```

### 5.3 Stage 3: Integrity Assertions (`ReportValidator.assert_integrity()`)

**Purpose:** Lightweight structural validation after builder + AI merge. Verifies required fields, score bounds, financial consistency, and fills safe defaults for any missing narrative fields.

Unlike the old architecture (which ran 25+ patch methods to overwrite AI output), the builder constructs correct data upfront, so the validator only asserts correctness rather than correcting it.

### 5.4 Preview vs Paid Reports

| Feature                   | Preview (Free)                          | Paid / B2B                         |
| ------------------------- | --------------------------------------- | ---------------------------------- |
| **Auth Required**         | No (email gating)                       | Yes                                |
| **Script Upload**         | No                                      | Yes (required)                     |
| **Script Analysis**       | Skipped                                 | Full chunked analysis              |
| **Location Rankings**     | 3 territories, `isAssessmentOnly: true` | Up to 5 territories                |
| **Financial Analysis**    | Empty `{}`                              | Full budget scenarios + crew costs |
| **Territory Deep Dives**  | Empty `[]`                              | Top 3 detailed profiles            |
| **Crew/Cast/Weather/etc** | Empty `[]`                              | Fully populated                    |
| **PDF Generation**        | No                                      | Yes (WeasyPrint → S3)              |
| **DB Record**             | No                                      | Yes (reports table)                |
| **Processing**            | Synchronous                             | Background task                    |
| **Email Notifications**   | No                                      | Processing started + report ready  |

---

## 6. Report Generation Pipeline

### 6.1 Preview Flow (Synchronous)

```
POST /api/reports  (body: {report_type: "preview", metadata...})
  │
  ├─ Email gating check (is email blocked?)
  ├─ Load admin datasets (incentives, crew costs, etc.)
  ├─ Inject derived data (shoot window, scene exposure, territory financials)
  ├─ ReportBuilder.build() → deterministic skeleton (3 territories, preview mode)
  ├─ AI narrative fill → merge into skeleton → compute scores
  ├─ ReportValidator.assert_integrity() → structural checks
  ├─ Record email gating usage
  └─ Return PreviewReportResponse { analysis: {...} }
```

### 6.2 Paid/B2B Flow (Asynchronous)

```
POST /api/reports  (multipart: script_file + body JSON)
  │
  ├─ Auth required (JWT)
  ├─ Validate script file (type, size)
  ├─ Extract text from script (in-memory, never stored)
  ├─ Create report row in DB (status: "processing")
  ├─ Queue background task → return { status: "processing", report_id }
  │
  └─ Background Task (process_report_task):
      ├─ Load report row + metadata
      ├─ Send "processing started" email
      ├─ Script Analysis (Stage 1) → ScriptAnalysisResult
      ├─ Production Analysis (Stage 2):
      │   ├─ Load datasets + inject derived data
      │   ├─ ReportBuilder.build() → deterministic skeleton
      │   ├─ AI narrative fill (focused ~50-line prompt)
      │   ├─ Merge narratives → compute overall scores
      │   └─ ReportValidator.assert_integrity()
      ├─ PDF Generation
      │   ├─ Render Jinja2 HTML template
      │   ├─ WeasyPrint → PDF bytes
      │   └─ Upload to S3 (or local storage)
      ├─ Mark report completed (store report_data + S3 key)
      ├─ Send "report ready" email
      └─ On failure: mark report failed, send failure email
```

### 6.3 Accessing Reports

- `GET /api/reports` — List all user's reports (excludes previews).
- `GET /api/reports/{id}` — Get report with fresh presigned PDF URL.
- `GET /api/reports/{id}/status` — Poll processing status.
- `GET /api/reports/{id}/pdf` — Stream PDF bytes directly from S3.
- `GET /api/reports/shared/{share_token}` — Public access via share token (no auth).

---

## 7. Data Scraper & Sync Engine

### 7.1 Architecture (`app/modules/scraper/`)

The scraper keeps admin datasets fresh by pulling from authoritative external sources:

```
Sources (sources.py)         Fetcher (fetcher.py)        Extractor (extractor.py)
┌───────────────────┐       ┌───────────────────┐       ┌───────────────────┐
│ 800+ source defs  │──────▶│ HTTP fetch + strip │──────▶│ Claude AI extract │
│ across 18         │       │ robots.txt check   │       │ structured JSON   │
│ territories       │       │ PDF support        │       │ per resource type │
└───────────────────┘       └───────────────────┘       └────────┬──────────┘
                                                                 │
                            Differ (differ.py)                   │
                            ┌───────────────────┐                │
                            │ Match against DB   │◀───────────────┘
                            │ Generate diffs     │
                            │ Queue as pending   │
                            │ changes for admin  │
                            │ review             │
                            └───────────────────┘
```

### 7.2 Sources (`app/modules/scraper/sources.py`)

- **866 lines** defining `DEFAULT_SOURCES` — covering 18 territories (US, CA, GB, IE, FR, DE, ES, IT, CZ, HU, AU, NZ, ZA, NG, MT, IS + multi-country).
- 4 resource types: `incentives`, `crew_costs`, `grants`, `festivals`.
- Each source has: `url`, `label`, `territory`, `resource_type`, `source_authority` (government_incentive, national_statistics), flags for `is_pdf`, `use_bls_api`, `use_rest_api`.
- **Important:** Union/CBA rate sources (SAG-AFTRA, ACTRA, BECTU, etc.) have been removed — crew cost data uses **only government statistical agency sources** (BLS, Statistics Canada, ONS, INSEE, etc.) to avoid IP exposure.

### 7.3 Fetcher (`app/modules/scraper/fetcher.py`)

- HTTP fetch with `httpx`, respects `robots.txt`.
- HTML stripping (removes script/style/nav tags, collapses whitespace).
- Truncation to `SCRAPER_MAX_TEXT_CHARS` (60,000 chars).
- PDF text extraction via `pdfplumber`.

### 7.4 Extractor (`app/modules/scraper/extractor.py`)

- Sends cleaned text to **Anthropic Claude** with resource-type-specific extraction prompts.
- Returns structured JSON arrays of incentive programs, crew costs, grants, or festivals.
- Each resource type has a detailed extraction schema (rate_gross, rate_net, rate_type, eligibility rules, etc.).

### 7.5 Differ (`app/modules/scraper/differ.py`)

- Matches extracted records against existing DB rows using composite keys (e.g., territory + program_name for incentives).
- Generates diffs only for tracked fields per resource type.
- Queues changes as `pending_changes` rows for **admin review** before applying.
- Territory name normalization (UK → United Kingdom, US → United States, etc.).
- Stale date validation (rejects scraped dates before 2024).

### 7.6 API Sources (`app/modules/scraper/scrapers/`)

Specialized scrapers per resource type:

- **`incentives.py`** — Fetch → Extract → Diff for film incentive programs.
- **`crew_costs.py`** — Includes BLS API integration for US occupational wages (Camera Operator, Producer/Director, Film/Video Editor, Sound Engineer). Also handles PDF sources and standard web scraping.
- **`grants.py`** — Grant opportunity extraction.
- **`festivals.py`** — Film festival data extraction.
- **`api_sources.py`** — REST API source handlers.

### 7.7 Scheduling

- **APScheduler** runs daily at midnight.
- Checks `sync_settings` table for each resource type.
- Runs scrapers when `next_scheduled ≤ now`.
- Default schedule: biannual (182 days).
- Sources seeded on app startup via `ScraperService.seed_sources()` — upserts defaults, disables deprecated URLs.

---

## 8. Admin System

### 8.1 Admin Modules

Each data domain has its own admin router with full CRUD:

| Module                      | Route Prefix              | Manages                                                      |
| --------------------------- | ------------------------- | ------------------------------------------------------------ |
| `admin/`                    | `/api/admin`              | Users, comparables, reports, business metrics, sync triggers |
| `festivals/admin_router`    | `/api/admin/festivals`    | Film festivals                                               |
| `incentives/admin_router`   | `/api/admin/incentives`   | Incentive programs                                           |
| `crew_costs/admin_router`   | `/api/admin/crew-costs`   | Crew & cast rates                                            |
| `grants/admin_router`       | `/api/admin/grants`       | Grant opportunities                                          |
| `data_sources/admin_router` | `/api/admin/data-sources` | Data source health/status                                    |
| `subscribers/admin_router`  | `/api/admin/subscribers`  | Newsletter subscribers                                       |
| `email_gating/admin_router` | `/api/admin/email-gating` | Email block/allow rules                                      |
| `pdf_reports/admin_router`  | `/api/admin/pdf-reports`  | PDF template management                                      |
| `admin/admin_users_router`  | `/api/admin/admins`       | Admin user management                                        |
| `email/router`              | `/api/admin/email`        | Email template testing                                       |

### 8.2 Pending Changes Workflow

Scraped data doesn't auto-apply to production datasets. Instead:

1. Scraper extracts new/changed data → creates `pending_changes` rows.
2. Admin reviews changes in the admin dashboard.
3. Admin approves or rejects each change.
4. Approved changes are applied to the target table.

This ensures data quality for the AI analysis pipeline.

---

## 9. Payments & Subscriptions

### 9.1 Stripe Integration (`app/modules/payments/`)

- **Checkout Sessions** — One-time payments and subscription signups via Stripe Checkout.
- **Price IDs** — Configured per plan (Single/Studio) and currency (USD/GBP).
- **Webhook Handler** — Processes Stripe events:
  - `checkout.session.completed` → Create/update subscription
  - `customer.subscription.updated` → Sync status
  - `customer.subscription.deleted` → Mark cancelled
  - `invoice.paid` / `invoice.payment_failed` → Email notifications
- **Customer Portal** — Redirect to Stripe's hosted portal for billing management.

### 9.2 Subscription Service (`app/modules/subscriptions/`)

- Checks active subscription and report limits.
- Report counting per billing period.
- Plans: `free` (1 report), `single`, `studio` (unlimited).

---

## 10. Auxiliary Modules

### 10.1 Email Service (`app/modules/email/`)

- **SendGrid** integration with Jinja2 HTML templates.
- Template types: `welcome`, `report_ready`, `processing_started`, `payment_confirmation`, `grant_alert`, `festival_deadline`, `admin_invite`.
- Templates stored in `app/templates/emails/`.

### 10.2 Email Gating (`app/modules/email_gating/`)

- Controls free preview report access.
- Tracks emails that have generated previews.
- Admin can block specific emails from generating free reports.

### 10.3 Watchlist (`app/modules/watchlist/`)

- Users can add/remove territories to a personal watchlist.
- Used for territory-based alert notifications.

### 10.4 Festivals & Grants (Public Routes)

- `GET /api/festivals` — Public listing of upcoming film festivals.
- `GET /api/grants` — Public listing of open grant opportunities.

### 10.5 PDF Service (`app/modules/reports/pdf_service.py`)

- Renders report data → HTML via Jinja2 template (`templates/pdf/report_base.html`).
- Converts HTML → PDF via **WeasyPrint**.
- Uploads PDF to S3 (or local storage).
- Returns S3 object key for DB storage.

---

## 11. Data Model

### 11.1 Core Tables (`app/models/sql_models.py`)

| Table                    | Purpose                       | Key Fields                                                                       |
| ------------------------ | ----------------------------- | -------------------------------------------------------------------------------- |
| `users`                  | User accounts                 | email, password_hash, user_type, plan, credits_remaining, is_blocked             |
| `admins`                 | Admin accounts                | email, password_hash, role (master/senior/data/support)                          |
| `reports`                | Generated reports             | user_id, script_title, status, report_type, report_data (JSON), pdf_url (S3 key) |
| `subscriptions`          | Stripe subscriptions          | user_id, stripe_customer_id, plan_type, status, report_limit                     |
| `territory_watchlist`    | User territory preferences    | user_id, territory                                                               |
| `comparable_productions` | Reference productions         | title, year, budget_usd, genre, territory, tmdb_id                               |
| `email_gating_records`   | Free report access control    | email, report_generated, blocked                                                 |
| `data_sources`           | External data source registry | name, slug, category, status, sync_schedule                                      |

### 11.2 Admin-Managed Dataset Tables

| Table                 | Purpose                               | Used In Reports                           |
| --------------------- | ------------------------------------- | ----------------------------------------- |
| `incentive_programs`  | Film tax incentives, rebates, credits | ✅ Incentive estimates, location scoring  |
| `crew_costs`          | Crew & cast rate benchmarks           | ✅ Crew/cast insights, financial analysis |
| `grant_opportunities` | Film funding / grants                 | ✅ Funding opportunities section          |
| `film_festivals`      | Festival listings + deadlines         | ✅ Funding opportunities section          |
| `territory_weather`   | Monthly weather data per territory    | ✅ Weather logistics, risk scoring        |
| `scrape_sources`      | Web scraping source registry          | Used by scraper engine                    |
| `scrape_runs`         | Scraping execution logs               | Used by scraper engine                    |
| `sync_settings`       | Per-resource-type sync config         | Used by scheduler                         |
| `pending_changes`     | Scraped data awaiting admin review    | Used by admin workflow                    |

---

## 12. API Routes Summary

### Public Routes (No Auth)

| Method | Path                          | Description                         |
| ------ | ----------------------------- | ----------------------------------- |
| GET    | `/api/health`                 | Health check                        |
| POST   | `/api/auth/signup`            | Register new user                   |
| POST   | `/api/auth/signin`            | Email/password login                |
| POST   | `/api/auth/google`            | Google OAuth via Firebase           |
| POST   | `/api/auth/refresh`           | Refresh JWT tokens                  |
| POST   | `/api/reports`                | Create preview report (email gated) |
| GET    | `/api/reports/shared/{token}` | View shared report                  |
| GET    | `/api/festivals`              | List upcoming festivals             |
| GET    | `/api/grants`                 | List open grants                    |

### Authenticated User Routes

| Method   | Path                       | Description                |
| -------- | -------------------------- | -------------------------- |
| POST     | `/api/scripts/validate`    | Validate script file       |
| POST     | `/api/scripts/analyze`     | Analyze script (AI)        |
| POST     | `/api/reports`             | Create paid/b2b report     |
| GET      | `/api/reports`             | List user's reports        |
| GET      | `/api/reports/{id}`        | Get report details         |
| GET      | `/api/reports/{id}/status` | Poll report status         |
| GET      | `/api/reports/{id}/pdf`    | Download PDF               |
| GET/POST | `/api/watchlist`           | Manage territory watchlist |
| GET      | `/api/subscriptions`       | Subscription status        |
| POST     | `/api/payments/checkout`   | Start Stripe checkout      |
| POST     | `/api/payments/webhook`    | Stripe webhook             |

### Admin Routes

| Method | Path                      | Description         |
| ------ | ------------------------- | ------------------- |
| POST   | `/api/admin/auth/login`   | Admin login         |
| GET    | `/api/admin/users`        | List users          |
| GET    | `/api/admin/metrics`      | Business metrics    |
| CRUD   | `/api/admin/festivals`    | Manage festivals    |
| CRUD   | `/api/admin/incentives`   | Manage incentives   |
| CRUD   | `/api/admin/crew-costs`   | Manage crew costs   |
| CRUD   | `/api/admin/grants`       | Manage grants       |
| CRUD   | `/api/admin/data-sources` | Manage data sources |
| CRUD   | `/api/admin/email-gating` | Manage email rules  |
| CRUD   | `/api/admin/admins`       | Manage admin users  |

---

## 13. Configuration & Environment

All configuration is in `app/core/config.py` via Pydantic Settings (`.env` file takes priority over shell env):

| Category          | Key Settings                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------- |
| **App**           | `APP_ENV`, `DEBUG`, `LOG_LEVEL`, `FRONTEND_URL`, `BACKEND_URL`                                                      |
| **Database**      | `DB_URL` (sqlite or postgres connection string)                                                                     |
| **Auth**          | `JWT_SECRET_KEY`, token TTLs, `FIREBASE_PROJECT_ID`, `FIREBASE_SERVICE_ACCOUNT_JSON`                                |
| **AI**            | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (claude-3-5-sonnet-20241022), max tokens, timeouts (per-stage configurable)  |
| **Chunking**      | `SCRIPT_ANALYSIS_CHUNKED_ENABLED`, `SCRIPT_CHUNK_TARGET_TOKENS`, `SCRIPT_CHUNK_OVERLAP_TOKENS`, `SCRIPT_MAX_CHUNKS` |
| **Storage**       | `AWS_S3_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION`, presigned URL expiry           |
| **Payments**      | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, price IDs per plan/currency                                           |
| **Email**         | `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`                                                                           |
| **External APIs** | `TMDB_API_KEY`, `EXCHANGE_RATE_API_KEY`, `BLS_API_KEY`, `FRED_API_KEY`, `GOOGLE_MAPS_API_KEY`                       |
| **Scraper**       | `SCRAPER_ENABLED`, `SCRAPER_REQUEST_TIMEOUT`, `SCRAPER_MAX_TEXT_CHARS`                                              |

---

## 14. Deployment

### Docker

```dockerfile
# Dockerfile + docker-compose.yml provided
# Services: app (FastAPI), postgres, redis
```

### Makefile Commands

| Command            | Description                      |
| ------------------ | -------------------------------- |
| `make install`     | Create venv + install deps       |
| `make dev`         | Run with auto-reload (port 8001) |
| `make run`         | Production run                   |
| `make test`        | Run pytest                       |
| `make lint`        | Ruff linting                     |
| `make format`      | Ruff formatting                  |
| `make db-upgrade`  | Run Alembic migrations           |
| `make db-revision` | Create new migration             |

### Production Checklist

- [ ] Set `APP_ENV=production`, `DEBUG=False`
- [ ] Configure PostgreSQL via `DB_URL`
- [ ] Set strong `JWT_SECRET_KEY`
- [ ] Configure AWS S3 credentials
- [ ] Set Anthropic API key
- [ ] Configure Stripe keys + webhook secret
- [ ] Configure SendGrid API key
- [ ] Configure Firebase service account
- [ ] Set up Redis instance
- [ ] Run `alembic upgrade head`

---

## Summary

Prodculator/Scriptelligence is a sophisticated production intelligence platform that:

1. **Ingests** screenplays and extracts production signals via AI-powered chunked analysis.
2. **Cross-references** those signals with curated, admin-managed industry datasets (incentives, crew costs, weather, festivals, grants).
3. **Generates** comprehensive territory-ranked intelligence reports using a highly constrained AI pipeline with post-processing validation to prevent hallucination.
4. **Delivers** results as interactive web reports and downloadable PDFs.
5. **Maintains** data freshness through automated web scraping with admin review workflow.
6. **Monetizes** via Stripe with free preview (email-gated) → paid (single/studio) tiers.
