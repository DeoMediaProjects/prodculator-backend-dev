# Prodculator v2 engine gap assessment and implementation plan

Date: 16 September 2026. Scope: the supplied Festival Engine v2.1 and Markets/Labs/WIP v1 handoffs, the current backend and frontend report paths, and the local PostgreSQL database. This is not an audit of the deployed production database.

## Decision

The two handoff engines are not yet integrated. The proposed Sales/Distribution and Comparables layer is also not present at the requested depth. Complete the Festival and Markets/Labs/WIP upstream strategies before using their outputs to rank sales agents and distributors. Preserve the rule that report generation asks no additional questions and never converts an unknown fact into a known one.

## Verified baseline

| Area | Current implementation | Handoff target or missing commercial requirement |
| --- | --- | --- |
| Festivals | 177 local DB records. None has a structured dated deadline. Legacy matcher gates format and approximate timing, then ranks; unknown cycle status can pass the report loader. Report displays at most 5. No canonical FestivalStrategy. | 380 inventory records, of which 76 were paid-safe in the handoff's 15 September snapshot. Field-level verification, current-cycle actionability, PASS/FAIL/UNKNOWN gates, premiere sequencing, section-specific rules, and 5/10 entitlement after full-universe ranking. |
| Markets, labs and WIP | No database table, matching engine, strategy object or dedicated report section. | 203 track-level records, 43 paid-safe in the 16 September snapshot. Distinct routing, cycle gate, stage and participant gates, class-specific scoring, lifecycle sequence and 5/10 entitlement. All 203 source `hard_gates` fields are prose, so decisive rules still need structured, source-linked encoding. |
| Distributors and sales agents | 62 local distributor rows, 58 marked confirmed active. Only 7 have structured format focus. Existing matching uses genre, declared audience/representation, market reach and scouting overlap, then displays at most 4. Sales-agent versus domestic-distributor roles and many acquisition constraints are not modeled. | A sourced universe with company role, rights/acquisition type, territories, formats, genre, scale, language, origin, release channel, stage, specialization, recent titles and verification. Unknown mandatory facts remain UNKNOWN. |
| Comparables | 45 local rows; 45 have format, 40 have budget. Matching uses format, territory, genre and budget. The records do not store tone, themes, audience, release profile, festival trajectory, market history or distributor/sales-agent history. | Evidence-backed comparables with source and match reasons, then reusable verified relationships to Festival, Markets and Sales engines. Never infer revenue from a comparable title. |
| Report orchestration | Separate report fields and matcher calls; no shared Project DNA, FestivalStrategy, MarketsLabsWIPStrategy or SalesDistributionStrategy object. Frontend passes festival/distributor arrays with `any[]`. | One provenance-aware Project DNA and canonical outputs consumed by the on-screen report, PDF, Executive Summary, financial narrative and Next Steps. |

The local DB counts do not establish the live production counts. The existing festival loader's unknown-status fallback is especially unsafe for a paid recommendation. The supplied Festival snapshot has no structured `deadlines` array on any of its 76 paid-safe rows; all 76 store submission timing as prose, and seven of those prose fields still say to verify a deadline. It also lacks a normalized festival-section table. These are source-data completion tasks before dynamic, section-specific paid advice can be cut over. The official [Sundance 2027 submission page](https://www.sundance.org/festivals/sundance-film-festival/submit) shows that feature, short and episodic deadlines differ, while the [Academy's 99th Awards rules](https://www.oscars.org/oscars/rules-eligibility) scope qualifying routes by category. Festival identity or a single qualification boolean is not sufficient for section-level advice.

## Execution sequence

1. **Snapshot and migration safety.** Keep the two handoff datasets as versioned, validated source snapshots. Reconcile all 177 legacy festival IDs to the 380-record master, preserve existing admin edits in a migration backup, and dry-run changes against a production read-only snapshot before cutover. Normalise the Markets workbook's mixed string/Boolean `paid_safe` values. Do not use either static paid-safe flag as a perpetual runtime decision.
2. **Canonical Project DNA.** Normalise format, content form, technique, runtime, genre, tone, themes, languages, production countries versus story countries, audience, stage and budget once. Carry value, provenance, confidence and confirmation state. Premiere history, applicant nationality/residency, rights, secured finance, participant credits and footage readiness remain UNKNOWN unless actually supplied or verified.
3. **Festival Engine v2.1.** Add section/cycle/verification storage and non-destructive data import. Before paid cutover, verify and structure current section-level deadlines and rules from official sources; do not parse an ambiguous prose pattern into an invented date. Route non-festival records away, evaluate cycle and known hard failures before scoring, distinguish potential eligibility, sequence premiere conflicts and protect unknown premiere history. Rank the full actionable universe, then display 5 or 10 by package. Emit one FestivalStrategy with source and verification information for report and Next Steps.
4. **Markets/Labs/WIP Engine v1.** Import the 203 tracks without merging them into grants or festivals. Translate material prose hard gates into typed rules with source evidence and independent QA, leaving unstructured requirements as UNKNOWN rather than assuming a pass. Apply cycle/actionability, target-level and stage/finance/rights/participant gates before class-specific scoring. Rank the full universe, apply 5/10 and emit one MarketsLabsWIPStrategy. Market access is never secured finance.
5. **Comparables and Sales/Distribution.** Expand the data model with verified title-to-company, title-to-festival and title-to-market relationships. Research and curate company records from attributable sources. Build hard gates and strategic scoring around company role, acquisition scope, rights, format, geography, language, scale, stage and channel. Use verified comparable histories and Festival/Market intersections as evidence, not as an acquisition guarantee. Apply 5/10 only after ranking and emit SalesDistributionStrategy.
6. **One report orchestration pass.** Run Script Intelligence to Project DNA, then Locations, Incentives, Grants, Markets/Labs/WIP, Comparables, Festivals, Sales/Distribution, Financial Readiness and Next Steps using shared facts and canonical results. Wire the same results to frontend and PDF. Regenerate the Sixth Sense sample and compare old versus v2 output for contradictions, missing-source claims and financial overstatement.

## Cutover and acceptance gates

- No production data replacement until there is a restorable backup, a read-only production inventory, a migration dry run and a reviewed before/after diff. Existing IDs and unrelated admin data must survive.
- Closed, stale, unverified, archived, misrouted and known-ineligible records cannot consume paid recommendation slots. UNKNOWN never silently becomes PASS, false, zero or a guessed deadline.
- Package size changes only the final display depth. The same universe and ranking logic run for every paid package.
- Recommendations show eligibility state, conditions to confirm, reasons, source and verification date. Report generation asks no new engine-specific questions.
- Festival/market selection, meetings and comparable financial performance never become committed finance.
- Acceptance tests from both handoffs, regression tests, migration replay, frontend tests, backend suite, PDF render review and one end-to-end sample report must pass before merging the cutover.

## Work completed in this phase

The supplied Festival and Markets ZIPs were read as data, not as instructions. The Festival copies supplied under three filenames are byte-identical. `scripts/prepare_engine_handoff_snapshots.py` generated validated, versioned JSON snapshots from the canonical Festival JSON and Markets workbook. Eight new checks pass. No database rows, report behavior or live deployment were changed.

## Execution update

The report builder now constructs one provenance-aware `ProjectDNA` from existing intake and screenplay analysis. It keeps production country separate from screenplay setting and declared primary language separate from detected dialogue language. Missing premiere, rights, residency, finance and footage facts remain UNKNOWN. The new `opportunity_strategy` kernel evaluates structured, source-linked hard gates as PASS/FAIL/UNKNOWN, excludes unverified or expired cycles before ranking, ranks confirmed ahead of potential, and applies the existing 5/10 paid package depth only after full-universe evaluation. Regression tests cover known failure, unknown premiere, unstructured rules, missing dates and both package depths.

Verified future opening windows remain in the planning universe as `UPCOMING`; they are not described as open for application. Expired, undated or unverified cycles remain `NOT_ACTIONABLE`.

This is engine groundwork, not a paid recommendation cutover. The Festival snapshot still lacks structured section deadlines, while all Markets handoff hard gates remain prose. New snapshot tests enforce those known source gaps so the static paid-safe flags cannot be mistaken for complete runtime eligibility. The legacy report output remains unchanged pending source verification, data migration, renderer wiring and end-to-end review.

## Staging update

An additive Alembic migration now defines `engine_handoff_records`, `market_tracks`, `opportunity_cycles` and `opportunity_rules`. The first two store reviewed source data in isolation; the latter two require separately verified, structured cycle and eligibility evidence. The migration changes no existing festival, comparable, distributor or report row. It refuses to downgrade while any of the new tables contain data.

`python scripts/stage_engine_handoffs.py` is a read-only preflight by default. After a reviewed migration, `--apply` inserts only missing staging rows and market tracks. The command checks each snapshot's reviewed SHA-256 before loading it. Replaying it leaves admin edits untouched, and a changed source payload conflicts rather than silently replacing a staged record. The 380 Festival and 203 Markets records were imported into an isolated test database, including a replay and conflict check. No live database or paid report was changed. The backend Docker image now includes the reviewed snapshot files so the import command can run in its normal deployment environment.

Before a production import, obtain a read-only production inventory and restorable backup, compare live festival IDs to the 177 preserved legacy IDs, review the dry-run output, and run the explicit import in a maintenance window. Import alone never activates paid recommendations. Current section dates and rules still need official-source verification and human review before populating `opportunity_cycles` and `opportunity_rules`.

## First source-checked cycle tranche, 17 September 2026

The initial curated file covers three sections only: Anifilm 2027 International Feature Film for Adults, Anifilm 2027 International Short Film, and HAF25 In-Development Projects. Anifilm's [2027 statutes and competition rules](https://www.anifilm.cz/en/statutes-and-rules) confirm the 1 September to 31 December 2026 call, the section-specific runtime limits, the more-than-50-percent animation threshold, the completion/premiere window and prior-participation condition. HAF's [official IDP criteria](https://industry.hkiff.org.hk/en/haf/idp/eligibility-and-selection-criteria) confirm the 1 September to 30 October 2026 call, the 60-minute minimum, rights authority, attached team, development stage, budget and financing status, and application materials.

The source review did not establish the producer's animation percentage, premiere history, rights, attachments or readiness. Those are encoded as sourced `manual_confirmation` or UNKNOWN gates. All three cycles have `rules_complete=false`, so none can be promoted to confirmed paid eligibility. `scripts/stage_curated_cycles.py` is read-only by default and checks that the corresponding handoff records are staged first. `--apply` inserts missing cycle/rule rows only; a changed admin-curated row causes a conflict, not an overwrite. Isolated tests cover replay, missing source records, format/runtime failures and unknown conditions. The live report is still unchanged.

The next additive migration introduces `observed_open_on` separately from `cycle_open`. [Go Short's 2027 call](https://www.goshort.nl/en/news-overview/call-for-entry-go-short-2027) says submissions were open on its 23 July 2026 publication date and gives a 15 December final deadline. Its [2027 competition rules](https://www.goshort.nl/media/sghjttzm/regulations-go-short-2027-short-film-competition.pdf?v=1) support short-film, runtime and origin gates. The PDF contains an apparent stale 2026 screening-delivery date in a later section, so its full rule set needs manual QA. The [MIDPOINT Feature Launch / Focus Queer 2027 announcement](https://www.midpoint-institute.eu/cs/article/applications-open-for-midpoint-feature-launch-2027-midpoint-4MRqs0), published 3 September 2026, states applications are open and gives 26 and 19 October deadlines respectively. None of these calls was assigned a guessed exact opening date. All three were added as source-linked, rules-incomplete staging cycles only.

The strategy now collapses ranked sections of the same festival to one package slot. Its universe and eligibility counters still count sections, while displayed recommendations count distinct festivals. This avoids a festival using multiple 5/10 slots when project facts are unknown. Section choice remains conditional where format, runtime or other gates are UNKNOWN. None of this changes the paid report yet.

## Commercial-layer audit, 17 September 2026

The existing `distributors` table and matcher are a legacy shortlist, not a Sales/Distribution Engine. They do not distinguish sales agents from domestic distributors, or model acquisition type and stage, rights scope by territory, language, theatrical/streaming focus, documentary/animation/genre specialism, budget bounds, recent acquisitions or title-level evidence. The loader requires `confirmed_active`, but that flag and `verified_at` do not prove each matching attribute remains current. `source_url` is often a company homepage rather than field-level proof. The legacy matcher scores genre, declared audience, representation and festival scouting overlap; it displays four results, not the proposed 5/10 package entitlement. A missing format focus is treated as unknown yet still competes, so a score is not confirmed acquisition eligibility.

One live-path provenance error was corrected during the audit: the builder had combined recommended filming territories and screenplay settings as `production_territories` for distributor matching. It now passes only intake-declared Project DNA production countries. A regression test checks that a Kenya setting does not create a Kenya production-market claim, while an actual declared Kenya production does.

The `comparable_productions` rows and current selector also fall short of the requested defensible profile. Selection primarily uses format, territory, genre and rough budget proximity; `relevanceDescription` is left for AI to fill. The existing TMDB sync discovers top-revenue movies and stores title, year, budget, first production country and genres, not tone, themes, audience, release profile, festival/market trajectory or verified sales/distribution history. A top-revenue import is not itself evidence of commercial similarity. There are no sourced title-to-company or title-to-festival/market relationship tables. The current database counts in the baseline are local only and must not be presented as production counts.

Next implementation gates: (1) add isolated, source-provenanced company-role and comparable-relationship staging tables; (2) curate and independently verify a representative cross-format set, including acquisition scope and title histories; (3) build hard gates with UNKNOWN and separate strategic fit signals, then full-universe ranking and 5/10 entitlement; (4) wire canonical ComparableProfile and SalesDistributionStrategy into the report only after sample-report, API, PDF and frontend acceptance. Do not infer an offer, revenue outcome or committed finance from a comparable or a company match.

## Commercial engine foundation, 17 September 2026

The first and third implementation gates above now have code and isolated acceptance tests. The additive commercial staging migration defines company profiles, comparable profiles and source-linked comparable relationships separately from legacy rows. Each structured claim stores its own value, source URL and verification date. The read-only catalogue loader admits only `APPROVED_SOURCE_REVIEW` rows, rejects malformed or future-dated evidence, and requires relationships to resolve to an approved company when the relationship names a company. No legacy row is automatically promoted.

`commercial_strategy.py` matches comparables on sourced format, genre, tone, themes, form, production origin, language, audience, release profile and GBP-denominated budget where genuinely available. It requires at least two independent sourced similarities and never uses revenue as a similarity shortcut. Verified title-to-festival and title-to-market relationships can strengthen fit; verified title-to-company links support company history. The company matcher gates known format, form, technique, acquisition stage, origin, language, sales territory and budget before strategic scoring. Missing project or company evidence remains UNKNOWN. A company needs verified active status and role, plus a supported match, to be actionable. Confirmed fit additionally needs completed rule review and verified format, stage and rights-territory scope. Results are ranked across the full universe before 5/10 package depth.

This is still an engine contract rather than a populated commercial catalogue. The remaining critical work is independent source curation and QA of real companies and films, a reviewed production-data inventory and migration rehearsal, canonical report orchestration, API/PDF/frontend rendering of the new strategy, and old-versus-v2 sample-report acceptance. The storefront must not imply an acquisition offer or predict revenue from these matches.

## Expanded handoff and Devil Wears Prada regression, 17 September 2026

The `CORRECT FILES` folder adds authoritative Grants v2, ProjectFacts v1,
Markets/Labs/WIP v1, Festival v2.1, Sales/Distribution v1 and Report
Orchestration v1 handoffs, plus a 13-section regression report and implementation
note. These are specifications and source snapshots, not evidence that their
runtime logic is already live. The duplicate Sales workbook copies are
SHA-256-identical; the duplicate ProjectFacts schema copies are also identical.
For festivals the `FINAL_VERIFIED_2026-09-15` dataset has priority over the
earlier `final_380` baseline.

| Component | Frozen handoff | Current application gap |
| --- | --- | --- |
| ProjectFacts | One versioned, provenance-aware snapshot; blank territory spend UNKNOWN; user and script facts coexist. | Existing `ProjectDNA` is partial and engine-specific adapters still reconstruct facts. A persisted snapshot ID and shared contract are now being added, but every engine does not yet consume it. |
| Incentives | Total budget, territory spend and statutory qualifying spend are distinct. No project amount without required inputs. | The legacy precompute still derived amounts from total budget. The v2 request path now fails closed and publishes no budget-proxy financials. A reviewed statutory scenario calculator and report wiring remain. |
| Grants | 253-record reconciled master, verification/hard gates, 5/10, no market records in grant output. | Existing Grants v2 matcher is present, but the new 253-record source and legacy migration maps have not been rehearsed against production, and report routing still needs cross-engine QA. |
| Markets/Labs/WIP | 203 tracks, static 43 paid-safe snapshot, cycle/stage/person gates, dedicated Section 09. | Data is staged in a versioned snapshot and opportunity kernel exists; 203 prose hard gates still need typed source review. No live Section 09 or canonical strategy cutover. |
| Festivals | 380 inventory, static 76 paid-safe snapshot, section-level verification and premiere sequence. | Additive staging and a small source-checked cycle tranche exist. Most section deadlines/rules are not structured; paid report still uses legacy recommendations. |
| Comparables and Sales/Distribution | 101 companies, 205 typed sourced relationships, 100-point strategic fit, access routes, group dedupe and 5/10. | The workbook is now a checksum-verified snapshot. Its prose fields and raw access labels need field-level normalization; the current isolated commercial matcher does not yet implement the frozen scoring/portfolio contract or feed the paid report. |
| Report orchestration | One snapshot and canonical engine outputs drive 13 sections, finance buckets and Next Steps. | Current builder remains the old report shape. It cannot be called a completed v2 report until renderer, API, PDF and frontend all consume the canonical outputs. |

The current builder now emits a versioned ProjectFacts snapshot and tags its
Grants result with the same identifier. The report validator rejects a
specialist result whose snapshot ID or version differs, or whose provenance is
missing in a new snapshot-bearing report. This enforces one input state for the
first integrated engine; the remaining engines must be wired before the
13-section cutover.

The regression demonstrates four required fail-safe behaviours: do not calculate
New York/UK/France rebates from the $30m budget when spend is blank; route Film
London Production Finance Market outside Grants; do not let a possible
kids/family intake value override contradictory script evidence in festival
matching; and distinguish production comparables from sourced commercial
comparable relationships. Its proposed five sales targets are strategic examples,
not acquisition-interest evidence or proof that the live matcher would select them.

### Executable completion sequence

1. Finish the canonical ProjectFacts snapshot and require identical snapshot
   identifiers on every engine result. Add a mismatch regression that blocks
   final rendering rather than silently combining runs.
2. Replace the legacy budget-proxy incentive calculation with reviewed
   programme-specific statutory calculations using scenario spend and exact cost
   bases. Preserve UNKNOWN and do not publish project amounts until this passes
   the Devil Wears Prada blank-spend regression.
3. Reconcile Grants 253 against live IDs via the supplied migration maps and a
   restorable backup; import only after dry-run review. Complete verified cycle
   and typed hard-gate data for Markets and Festivals before paid cutover.
4. Normalize the 101-company/205-relationship commercial freeze into approved
   claims, including canonical access states and typed relationship semantics;
   implement the frozen 100-point score, parent/label dedupe and suitable-route
   diversification. Do not infer close commercial comparables for the regression
   screenplay when the graph lacks them.
5. Build/persist canonical strategy objects and the 13-section orchestrator.
   Route Section 08 Grants, 09 Markets, 10 split Comparables, 11 Festivals and 12
   Sales from those objects only; classify finance without counting pipeline as
   committed cash.
6. Wire the same payloads to API, frontend, PDF and sample renderer. Run backend,
   frontend, data import, schema, screenshot/PDF and end-to-end regression QA.
   Only then propose a paid/live cutover after production inventory and backup.

This sequence is more than a wording or prompt change. Completion remains
conditional on source maintenance, migration safety and an actual end-to-end
report run. No production database or deployed frontend change is implied by
these repository edits.

## Statutory incentive calculation, 18 September 2026

Sequence step 2 is implemented. `app/modules/reports/statutory_calculation.py`
derives a programme's qualifying base from the statutory cost figures the
producer supplied for that territory, dispatched on the programme's declared
`qs_engine_type`. `CORE_LOWER_OF` takes the lower of local core expenditure and
the recorded percentage of global core expenditure, and consumes that percentage
once rather than reapplying it as a cap. `MULTI_BUCKET` sums only the buckets the
programme declares in `programme_required_inputs`. The single-input engines read
their own canonical key, so a labour credit cannot be calculated from an eligible
local spend figure. Percentage and absolute qualifying-spend caps apply after the
base, and an unconvertible absolute cap is left unapplied rather than guessed.

The module returns no base — never a zero and never a budget-derived figure —
when a required input is unknown, when the engine is not spend-derived, when the
row carries no engine, or when the engine has no encoded rule. A supplied zero is
distinct from an absent one and calculates to zero, which is the split the whole
contract turns on.

`ReportValidator._compute_corrected_rebate` takes an optional
`statutory_qualifying_spend`. When present it replaces Step 1 entirely and
suppresses two legacy behaviours: the 15 percent above-the-line assumption, which
would discount a figure that is already net of the programme's exclusions, and
the capped-out programme switch, which would model the replacement programme's
rate against the original's statutory denominator. A capped-out programme on the
statutory path yields no figure at all. Callers that pass no statutory base — the
standalone calculator, the admin preview and migration comparisons — keep the
legacy budget-proxy calculation unchanged.

`ReportService._pre_compute_territory_financials` now takes the statutory path
for any request carrying `_territory_scenarios`, replacing the interim
fail-closed stub. Supplied amounts are converted to GBP through the same
budget-to-GBP rate the rest of the report composes from, and an input in a
currency with no resolvable rate is treated as unknown rather than passed through
unconverted. A territory whose base cannot be established is absent from
`_territory_financials` entirely, so no downstream section can read an amount for
it; `resolve_calculation_status` already reports that state as
`REQUIRES_COST_BREAKDOWN`.

The Devil Wears Prada acceptance case passes: blank territory spend produces no
project rebate amount, a scenario spend on its own is not accepted as a statutory
base, and a supplied base produces the same figure at a 10m, 30m and 90m budget.
Twenty-seven regression tests cover the engines, the caps, the zero/unknown
split, currency conversion and the capped-out case.

The wizard already collects these figures: `AnalysisWizard` sends per-territory
`calculation_inputs` with each amount's currency and its known versus
planning-assumption status, so the producer-facing half of the path needs no
change. The remaining gate is data rather than engine. A live
`incentive_programs` row without `qs_engine_type` produces no figure on the v2
path, which is the same conservative outcome as the interim stub, and it will
stay that way until those rows carry a reviewed statutory engine classification.
A production inventory of how many active rows currently hold one is the next
piece of work on this step, and it is a source-review task, not a code task.
Sequence steps 3 to 6 are unchanged.
