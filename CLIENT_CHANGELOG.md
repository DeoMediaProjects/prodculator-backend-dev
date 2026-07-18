# Prodculator — Client Changelog

Running record of changes delivered under PROD-SOW-003, for Deo Media Limited.
Each entry lists what changed, why, the files touched, and how it was verified.

---

## 2026-07-14 — Phase: Signal contract merge (Implementation Plan §Days 3–5)

### Baseline recorded before any change
- Full existing backend test suite executed: **657 passed, 1 failed**.
  The single failure (`test_free_user_gets_watermarked_pdf`) is environmental —
  WeasyPrint's GTK libraries were not yet installed on the development machine,
  so PDF rendering is skipped locally. Not a code defect; being resolved by
  installing the GTK3 runtime.
- Repository migration state recorded: two open Alembic heads
  (`b2a3b4c5d6e7`, `c1d2e3f4a5b6`).

### Handoff pack reconciled against the repository (drift found and handled)
The B2B handoff pack was written against an earlier repository state. Three
drift points were identified and adapted rather than merged blindly:
1. **`sql_models.py`** — the pack's copy predates the repository's newer
   `TerritoryProfile` bankability fields (v3). Only the `ProductionSignal` v2
   model changes were merged; the newer repository fields were preserved.
2. **Migration parents** — the pack migration expected three open heads
   (`c1d2e3f4a5b6`, `i2j3k4l5m6n7`, `z8b9c0d1e2f3`); two had since been merged
   into the chain by internal work. The migration's `down_revision` was
   re-pointed to the actual current heads (`b2a3b4c5d6e7`, `c1d2e3f4a5b6`).
   The migration's column additions are idempotent, so behaviour is unchanged.
3. **`reports/service.py`** — the pack's copy predates newer repository
   features (bankability query fields, PDF waterfall numerics, distributor
   dataset). Only the signal-write logic was folded in; no newer repository
   code was removed.

### Changes delivered
**New modules (from handoff pack, as designed):**
- `app/modules/b2b/signal_normalise.py` — canonical vocabulary (formats,
  genres) + GBP budget banding. Single source of truth for segment matching.
- `app/modules/b2b/package_service.py` — section library, product templates,
  and the sufficiency preview with privacy floors (10 overall / 5 per segment).

**Updated modules (from handoff pack, merged):**
- `app/modules/b2b/service.py` — every signal read now enforces the consent
  gate (`b2b_consent = TRUE` only) and internal-row exclusion; overall privacy
  floor raised from 5 to 10.
- `app/modules/b2b/admin_router.py` — new admin package endpoints
  (`/api/admin/b2b/package/*`: library, templates, sufficiency preview),
  additions only, behind the existing `canManageB2B` permission.
- `app/models/sql_models.py` — `ProductionSignal` v2: three-way territory
  fields (`home_country`, `territories_considered`, `territories_recommended`),
  FX-normalised budget fields (`budget_amount_gbp`, `budget_currency`,
  `fx_rate_date`), audience fields (stored, never scored), governance flags
  (`b2b_consent` default false, `is_internal`), `report_runs`,
  `schema_version`, unique `script_id` (one signal per script).
- `app/modules/reports/service.py` — the signal write path now:
  FX-normalises budgets to GBP before banding (fixes the historical
  foreign-currency banding bug), canonicalises format and genres on write,
  captures the three territory fields, refuses to persist un-consented
  signals, honours consent withdrawal by deleting the prior row, and
  dedupes per script (latest wins, `report_runs` incremented).

**Database migration:**
- `alembic/versions/b2b1v2signal_production_signals_v2.py` — merges the open
  migration heads into one and adds the v2 columns idempotently; backfills
  legacy `territory` into `home_country`; existing rows default to
  `b2b_consent = FALSE` (the safe choice — un-consented rows are dark until
  re-consented). Applied cleanly to the development database; all 16 new
  columns and the unique `script_id` index verified present.

**Data cleanup tool (dry-run first, per plan §3):**
- `scripts/b2b_signal_v2_cleanup.py` — dedupes legacy rows sharing a
  `script_id` (latest wins) and normalises legacy vocabulary to canonical
  values. Runs as a dry run by default and prints the affected-rows report;
  writes nothing without `--apply`. The dry-run report for staging/production
  will be provided for review before any apply run, as agreed.

**Verification folded into the test suite:**
- The handoff pack's 17 verification checks (FX banding, consent gate,
  script dedupe, consent withdrawal, internal exclusion, sufficiency preview)
  now run inside the repository's pytest suite
  (`tests/test_b2b_signal_v2.py` + `tests/b2b_signal_v2_checks.py`).
  Two small adaptations were needed to run them against the current repo:
  the Jinja2 test stub gained the `filters` registry the newer PDF service
  uses, and the checks run in a subprocess so their dependency stubs cannot
  leak into other tests.

### Existing tests updated to the v2 contract (updated, not deleted)
Five pre-existing tests asserted v1 behaviour and were updated to the
delivered contract — no test was deleted or skipped:
- `tests/test_b2b_routes.py` — seeded signals now carry the mandatory
  governance flags (`b2b_consent=TRUE`, `is_internal=FALSE`); the overall
  privacy-floor assertion updated from 5 to 10 per the handoff contract.
- `tests/test_reports_service.py` — the three signal-write tests now pass
  consent (un-consented writes are correctly refused), and assert the
  canonicalised vocabulary ("Feature Film" → "feature", lowercased genres)
  and the stored GBP budget amount.

### Verified by
- Handoff pack verification checks: **17 passed, 0 failed** inside pytest.
- Alembic: single head `b2b1v2signal` after migration; upgrade ran cleanly.
- Database inspected post-migration: all v2 columns + unique index present.
- Cleanup tool dry run executed against the development database (no legacy
  rows locally; real dry-run report to follow on staging).
- Full regression suite after all changes: **658 passed, 1 failed** — the
  one failure is the pre-existing environmental WeasyPrint/GTK issue from
  the baseline, unrelated to this work (one net-new test vs baseline: the
  17-check verification wrapper).

---

## 2026-07-14 — Phase: B2C build, first slice (Implementation Plan §Days 5–10)

### 1. `characters` array — the fix for hallucinated character names
Root cause found and fixed: the report's narrative prompt already instructed
the AI to use names "ONLY from script_characters array", but nothing ever
supplied that array — so the AI invented names. Now:
- Script analysis extracts named speaking characters **verbatim from dialogue
  cues** during chunk extraction (generic cues like MAN / VOICE / GUARD #2
  excluded; inventing or normalising names explicitly forbidden).
- Names are aggregated across chunks (deduplicated case-insensitively,
  most-recurring first — protagonists naturally rank top) and delivered on
  the analysis result as `characters` (capped at 30).
- The narrative prompt now receives the real `script_characters` array; when
  it is empty the AI is instructed to write without personal names rather
  than invent any.
- Files: `app/modules/scripts/schemas.py`, `app/modules/scripts/service.py`.
  Chunked parsing and stage configuration untouched, per the plan.

### 2. Business Intelligence consent capture at intake (end to end)
- Backend: `CreateReportRequest` gains `b2b_consent` (default **false**), which
  flows into the report path's request metadata — the signal writer merged in
  the previous phase already refuses un-consented writes and honours
  withdrawal. File: `app/modules/reports/schemas.py`.
- Frontend: the upload form and the free-preview dialog gain a **separate,
  strictly optional** consent checkbox (distinct from the required Terms
  acceptance; never blocks report generation; resets after each upload so
  every submission is its own consent event). The request body now always
  sends an explicit true/false. Checkbox copy is a clearly marked
  **placeholder pending the solicitor's wording**. Files:
  `src/app/components/user/ScriptUpload.tsx`,
  `src/app/contexts/ScriptContext.tsx`.

### Verified by
- Four new unit tests for character aggregation/sanitisation (frequency
  ordering, case-insensitive dedupe, cap, empty default) — all passing.
- Full backend suite: **662 passed, 1 failed** (the same pre-existing
  WeasyPrint/GTK environment issue; unrelated).
- Frontend TypeScript typecheck: clean.

---

## 2026-07-16 — Phase: B2C build, festivals & distributors verification

### Production incident assist (staging/Railway)
The deployed backend was crash-looping: `JWT_SECRET_KEY` was still the
template placeholder, which the config validator correctly refuses (it would
allow forged login tokens). Diagnosed from the Railway logs; fixed by setting
a real secret in the Railway environment — no code change required.

### Festival + Distributor engine verified against the delivered reference
Reconnaissance showed the handoff's matching engine had already been ported
into the repository (`app/modules/reports/matching.py`) by internal work,
wired in the required order (festivals first, distributor matching consumes
the matched festival names). Rather than re-port it, this phase proved the
port is faithful:
- Canonical datasets confirmed seeded and identical in count to the pack:
  **177 festivals, 57 distributors** (the dev notes' "28 distributors" figure
  was stale; the delivered JSON contains 57).
- The pack's smoke tests (Tests 0–5) folded into pytest as
  `tests/test_matching_parity.py`, running against the repo engine with
  byte-for-byte copies of the canonical JSONs (`tests/data/`): completion-date
  and festival-window spec, representation strict opt-in, declared-audience
  matching (Frameline/Outfest surfacing, Breaking Glass boost), the
  festivals→distributors scouting linkage, and the baseline-unchanged
  regression. **7/7 passing.**
- The v2 sample report (EKO VIBES) received from the client is archived with
  the handoff pack as the design reference; confirmed the previously flagged
  stale Crew Cost section is already absent from this version.

### Decision recorded
Crew costs follow the approved implementation plan (replace with canonical
dataset), not the dev notes' removal instruction; the canonical crew-costs
dataset has been requested from the client. No crew-cost code touched.

### Verified by
- `tests/test_matching_parity.py`: 7 passed.
- Full backend suite: **669 passed, 1 failed** (the same pre-existing
  WeasyPrint/GTK environment issue; unrelated).

---

## 2026-07-16 — Phase: B2C build, intake form to the field contract

### Intake form rebuilt to intake_schema.json (v1.0, 2026-07-07)
New fields captured end to end (upload form → API → report metadata → B2B
signal write):
- **Expected Completion** (required) — drives the festival matcher's timing
  window and the signal's completion month.
- **Target Audience** (Kids & Family / Under 25 / Adults 25+) — declared
  only, never inferred from genre.
- **Audience Skew** — with the contract's routing rule implemented: the
  "LGBTQ+ audience" option is stored as an audience *segment* (scored by the
  matchers), never as a skew value; the true skew values are stored for
  Business Intelligence and not scored.
- **Representation opt-in block** (who made the film) — strictly opt-in,
  visually separate from audience, with copy explaining that leaving it blank
  changes nothing.
- **Open to Official Co-Production?** (yes/no/undecided) — including a
  correctness fix: the signal writer previously coerced any string to a
  boolean, so "no" would have stored as *true*; now yes→true, no→false,
  undecided→null.
- **Must Film In** — declared hard territory constraint (captured and stored;
  wiring into territory selection comes with the engine work).
- **Primary Language(s)** — upgraded from a single free-text value to up to
  five entries, per contract.

### Format options aligned to the contract — ⚠ for client confirmation
The form now offers the contract's seven format options (Feature Film,
TV Series, TV Pilot, Limited Series, Short, Documentary, Animated Feature) —
each maps to a canonical value the matchers' hard gates support. The legacy
options (Commercial, Music Video, Interactive, VR, and duplicate labels) are
no longer offered on the form but remain accepted by the API so existing
reports are unaffected. **Per the no-removals rule, please confirm in
writing that dropping those form options is approved.**

### Contract-vs-live conflict resolved (recorded)
The contract lists Location Strategy as optional free text; the live platform
uses a required three-way choice that drives territory selection. The live
behaviour is newer and deliberate, so it is retained; the schema entry is
treated as stale.

### Verified by
- New tests: co-production yes/no/undecided mapping (guarding the
  bool("no")==True bug) and full intake-contract field acceptance.
- Full backend suite: **671 passed, 1 failed** (same pre-existing
  WeasyPrint/GTK environment issue).
- Frontend TypeScript typecheck: clean.

---

## 2026-07-16 — Phase: Incentive engine reconciliation (dev handoff §6)

### Findings — the overlap the handoff flagged is already resolved in the repo
The dev handoff asked for the duplicate-engine overlap to be reconciled
(`programme_selector.py` vs `validator.py`) and the hardcoded 35% Canada
labour ratio sourced or removed. Audit of the current repository shows both
were addressed by internal work since the handoff was written:
- **One authoritative engine.** `programme_selector.py` was never ported;
  the platform has a single calculation path —
  `best_incentive()` + `ReportValidator._compute_corrected_rebate` — and the
  reports, the public/in-app What-If calculator, and the incentives service
  all call it (the code labels it "single source of truth"). Documented here
  as the answer to "which engine does the report call".
- **No fabricated ratios.** The Canada 35% hardcode is gone: labour-only
  credits now require a *sourced* `qualifying_spend_labour_pct` from the
  dataset, otherwise no number is produced.
- The selection duties `programme_selector.py` was designed for (which
  programme applies when a territory has several) exist in the live engine in
  richer form: format hard gates, nationality/SPV exclusion (CPTC vs PSTC),
  supplementary-credit exclusion (UK VFX credit), and cap-exceeded programme
  switching with plain-language advisory notes.

### Waterfall regression across every incentive record (plan §4) — built and passing
New tool `scripts/verify_incentive_waterfall.py` runs the authoritative
engine over **every** `incentive_programs` record (49, matching the canonical
dataset count exactly) at five budget points (£0.5M–£150M) and checks the
six-step waterfall invariants: qualifying spend bounded by budget, ATL only
ever reduces, gross rebate consistent with rate unless explicitly cap-clamped,
net never exceeds gross, all amounts sane. Result: **215 computations, zero
anomalies** (30 correctly-skipped zero-rate rows). Folded into pytest
(`tests/test_incentive_waterfall_regression.py`) — runs wherever a seeded
database is available (local/staging/CI), skips cleanly elsewhere.

### Verified by
- Waterfall regression: 215/215 clean against the live engine and full
  seeded dataset; pytest wrapper passes in DB mode and skips in sqlite mode.

---

## 2026-07-17 — Fixes from end-to-end local testing

### Public What-If calculator no longer rejects visitors (bug fix)
The public `/what-if` lead-magnet page and the plan-gated in-app tool share
one API endpoint, which demanded a Professional plan — so logged-out visitors
always got an error instead of the designed teaser. The endpoint now serves
two tiers from the same deterministic engine: anonymous/below-Professional
callers receive the teaser (programme, rates, rebate, qualifying spend, tier
ratings) with the premium fields redacted (currency advantage, crew figures,
net saving, minimum spend, payment timelines); Professional and above receive
the full scenario. Route tests added.

### Session refresh survives a Redis outage (resilience fix)
Refreshing a login session revokes the old token in Redis. If Redis was
unreachable, that hard-failed the whole refresh with a server error —
effectively logging every user out during a cache outage. Revocation is now
best-effort (logged when skipped); the primary control — token expiry — is
unaffected. Applied to both user and admin sessions.

### Intake form: validation and usability
- All mandatory fields now show a visible required asterisk.
- The Generate button is disabled until every mandatory field is complete,
  with a hint listing exactly what is missing.
- Expected Completion self-fills from Filming Start + Duration using the
  engine's own formula (shoot end + 20 weeks post-production) and remains
  editable; a manually entered date is never overwritten.

### Environment corrections found during testing (not code)
- The retired `claude-3-5-sonnet-20241022` model was still pinned in
  environment config, making script analysis report itself unavailable —
  updated to a current model. ⚠ Any other deployment carrying this value
  (staging/production env vars) needs the same correction.
- The defunct hosted Redis instance (DNS no longer resolves) was still
  referenced in dev config — replaced; flagged for credential rotation at
  handover.

### Crew costs — decision update
Owner has indicated crew-cost day rates should be REMOVED from the platform
(reverting to the dev-notes instruction), not replaced. Written confirmation
requested before any removal is performed, per the agreed no-removals rule.
Crew Depth quality tiers are unaffected and stay.

### Verified by
- Calculator route tests (anonymous teaser + validation) passing; full
  backend suite green apart from the long-standing local GTK/WeasyPrint
  environment issue.
- Frontend typecheck clean; behaviours verified in the running app.

---

## 2026-07-17 — Crew cost day-rates removed from the platform (owner-approved)

Per the owner's approval (relayed 2026-07-17) and the dev handoff §1: crew
COST day rates leave the platform entirely. **Crew DEPTH quality tiers are
untouched** and remain a scoring dimension.

### Cost Efficiency scoring re-anchored
Cost Efficiency was previously derived from crew day-rate arithmetic. It now
reads a curated score on the territory profile
(`territory_profiles.cost_efficiency_score` + source field, added by
migration). Per the canonical `territory_scorecard_composite.json`, **no
sourced cost data currently exists** — every territory scores a neutral 50
("no fabricated numbers" rule) until a sourced value is entered via the
admin. The report builder and both What-If calculators use the same neutral
fallback, so no territory gains or loses ranking from unsourced cost claims.

### Removed (backend)
- `crew_costs` table dropped; crew-cost scrape sources deleted
  (migration `c3d4e5f6a7b8`, idempotent).
- Crew scraper, its 16 government-statistics sources, extraction prompt,
  diff rules, and REST fetchers.
- Crew day-rate loading/FX-enrichment in the report engine, the crew-rates
  block in territory financials, the crew sections of the What-If calculator
  (crew rates, crew savings — net saving is now rebate + currency advantage),
  the Territory Comparison crew columns, the Excel export's Crew Cost
  Comparison + Crew Insights sheets, the B2B "Crew Cost Benchmarks" package
  section (production_services / crew_casting templates recomposed), the
  report-narrative crew prompts, the crew-cost section explainer, and the
  `canEditCrewCosts` admin permission.

### Removed (frontend)
- Crew-cost admin API client, `canEditCrewCosts` permission flag, crew
  rows in Territory Comparison, crew savings/rates in both What-If
  calculators, crew insights in report viewing/PDF, and crew-cost feature
  promises in FAQ/Terms copy. Crew Depth displays all retained.

### Compatibility
- The calculator API keeps accepting the old `baseline` parameter
  (deprecated, ignored) and old stored reports still render — legacy
  crew sections in previously generated reports are simply no longer shown.

### Verified by
- Full backend suite: **660 passed, 0 failed** (first fully-green run —
  the environmental PDF issue is also resolved after the GTK runtime
  install). Migration applied cleanly; crew_costs table gone and
  cost-efficiency columns present, verified against the live schema.
- Frontend TypeScript typecheck clean; updated tests passing.

### Explicitly not done (pending, by design)
- No field, route, table or column removed anywhere.
- Representation data remains sealed; no B2B read includes it.
- B2B self-service purchase remains disabled pending final pricing.
- Staging/production cleanup (`--apply`) awaits the client-reviewed dry-run
  report.

---

## 2026-07-18 — B2C report intake form: usability pass

Refinements to the report-generator intake form (`ScriptUpload.tsx`) so the
inputs are clearer and less error-prone. No scoring or figures were changed —
these are input-experience improvements only.

### Location Strategy removed (redundant)
The Location Strategy field was removed from the form: it duplicated the
information already captured by "Territories considering". The backend field
is now optional and defaults to "open" when absent, so previously generated
reports and the engine are unaffected.

### Territories grouped by continent
The territory picker now groups countries under continent headings
(Europe, North America, Africa, Asia, Oceania, South America) with each
country's sub-regions nested beneath it, in a friendlier, scannable layout.

### Currency derived from the production country
Currency is no longer pre-set to GBP. It is now suggested automatically from
the selected Production Country and can still be changed manually. Every
country in the picker maps to a currency the platform can actually convert:
- Added four currencies the platform already holds exchange rates for —
  Icelandic króna (ISK), Japanese yen (JPY), South Korean won (KRW) and
  Singapore dollar (SGD) — so Iceland, Japan, South Korea and Singapore now
  suggest their real local currency.
- Countries whose local currency the platform does not yet hold a rate for
  (e.g. India/INR, Mexico/MXN, Brazil/BRL) suggest "Other" rather than a
  wrong or fabricated rate. **To support these fully, a sourced exchange
  rate must be added to the FX table by the team — no rate was invented.**

### Multi-select fields made obvious
Fields that accept more than one value (Genres, Camera Equipment, Target
Audience, Creator Communities) now show checkboxes next to each option and a
"select one or more" / "select any that apply" helper line, so it is clear
which fields are multi-select versus single-choice.

### Territory picker made dynamic (country → regions)
The territory picker now shows countries grouped by continent, and a country's
provinces/states appear only after that country is selected — shown indented
beneath it and labelled "Regions in <country>". This removes the earlier wall
of every region at once and lets a producer drill in per country.

### Long dropdowns now scroll in place
All dropdown menus are capped in height (long lists scroll inside the menu)
and anchored directly below their field. The floating/mis-placed menu was
caused by MUI's default `selectedMenu` positioning; every dropdown now uses
the plain `menu` variant so the list opens in the right place and no longer
drifts over unrelated fields while scrolling.

### Verified by
- Frontend TypeScript typecheck: clean.
- Backend suite (fx / currency / schema / report scope): **217 passed**.
- Currency `Literal` and FX territory/rate tables confirmed consistent
  (every suggested currency is an accepted `budget_currency` value).

---

## 2026-07-18 — Security: admin permissions now fail closed

Closes the open security item flagged in the Developer Handover (§7.1). An
admin account row with a missing or NULL `role` — for example a row that
predates the `role` column on a drifted database — was being treated as a
**master_admin** (all ten permissions, including the ability to create other
admins). This was caused by defaulting a role-less row to `"master_admin"`
when the admin record is loaded.

### Fixed
- A missing/blank role now grants **zero** permissions (fail closed). Changed
  the default in three places where an admin record is hydrated
  (`admin/schemas.py`, `core/dependencies.py`, `auth/service.py` login +
  refresh) from `"master_admin"` to an empty role.
- The permission map already returned no permissions for an unknown role; the
  only gap was the default, which is now safe.
- Legitimate admins are unaffected — they carry an explicit role, and the
  seed script sets `master_admin` explicitly.

### Verified by
- New regression test (`tests/test_permissions_failclosed.py`): a role-less
  admin has zero permissions; `master_admin` retains full access.
- Full backend suite: **664 passed, 1 skipped** (skip is the environmental
  WeasyPrint/GTK PDF test).

---

## 2026-07-18 — Report cleanup, delete action, and dashboard What-If

Follow-ups from comparing a live generated report against the reference sample.

### Removed leftover crew-cost wording from reports
Crew-cost day-rates were removed earlier, but two pieces of report text still
described them — now corrected so the report matches what it actually computes:
- The **Cost Efficiency** dimension explainer no longer claims it is computed
  "through crew day rates … published rate scales". It now states honestly that
  where no verified local-cost dataset exists, the dimension shows a neutral
  baseline rather than an estimated figure.
- The **Data Sources** section no longer cites crew/cast wage statistics
  (Bureau of Labor Statistics, etc.) that no longer back any figure. It now
  states the real provenance: incentive, grant, festival and distributor
  records, each carrying its own source and verification.

### Report narrative no longer prints internal field names
The schedule/weather narrative occasionally printed raw field identifiers
(`scheduleViabilityScore`, `contingencyDaysEstimate`). The narrative prompts
now require plain-English phrasing ("a schedule-viability score of 7 and
roughly 45 contingency days").

### Delete a report from the dashboard (new)
The Reports table's delete action now works. A new ownership-scoped endpoint
(`DELETE /api/reports/{id}`) removes a report the signed-in user owns, with a
confirmation prompt and optimistic UI removal. Anonymised, consented production
signals are intentionally left intact — deleting a report is not a consent
withdrawal.

### What-If calculator embeds cleanly in the dashboard
The in-dashboard What-If tab previously rendered as a full standalone page
(its own logo header and full-height background). It now renders as a contained
card within the dashboard, dropping the duplicate branding.

### Verified by
- New tests: report delete succeeds for the owner, is denied (403) for another
  user, and returns 404 when the report doesn't exist.
- Full backend suite: **667 passed, 1 skipped**. Frontend typecheck clean.

---

## 2026-07-18 — Report narrative reliability + What-If dashboard polish

### Fixed: "AI narrative summary unavailable" on reports
The report's written narrative was failing with a timeout. The narrative is a
large generation on a high-capability model, and it was being requested as a
single blocking call with a 120-second ceiling — which it routinely exceeded,
so all retries failed and the report fell back to "narrative unavailable".
- The narrative call is now **streamed**, which keeps the connection alive for
  the full generation instead of tripping a read-timeout (this is Anthropic's
  documented approach for long requests).
- The report-stage timeout ceiling was raised (it runs in a background worker,
  so a longer allowance is safe). Schema-constrained script-analysis calls are
  unchanged.
- Note: report generation for a complex script may take a few minutes; it runs
  in the background and the report appears when ready. If faster turnaround is
  preferred over maximum narrative depth, the narrative model can be pointed at
  a lower-latency model via configuration — no code change needed.

### What-If calculator now matches the dashboard theme
Following the earlier embedding fix, the calculator was still light-themed
inside the dark dashboard. When embedded it now uses the app's black/gold
palette so it matches the surrounding surfaces (and the Territories tool).
The standalone /what-if and /tools/what-if pages keep their light theme.

### What-If results scroll within their own container
The results table is capped to about ten rows with an internal scrollbar and a
pinned header, so the calculator sits in its own scroll area instead of
stretching the whole dashboard page.

### Verified by
- Full backend suite: **667 passed, 1 skipped** (includes an updated
  narrative-call test covering the streaming path).
