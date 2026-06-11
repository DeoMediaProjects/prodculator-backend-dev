# Report Pipeline Architecture

Prodculator Production Analysis Report Generation

---

## The Former Pipeline

### How It Worked

The original report pipeline followed a simple but ultimately flawed pattern. It collected every dataset from the database, incentive programmes, crew costs, cast costs, comparables, grants, festivals, weather data, then compressed all of it into a single massive prompt and asked the AI to generate the entire report in one call.

The system prompt alone was 440 lines long. It instructed the AI to produce roughly 100 fields across 15 sections, covering everything from rebate percentages and payment timelines to territory scores, executive summaries, and formatting rules. The AI received raw database rows as context and was expected to interpret them correctly, apply the right formulas, respect programme caps, handle currency conversions, and write compelling narrative analysis, all in a single response.

Once the AI returned its JSON, a component called the ReportValidator ran 25 or more patch methods across over 3,000 lines of code. These methods systematically overwrote approximately 80 percent of what the AI had just generated, replacing its financial figures with database-authoritative values, recalculating scores, correcting incentive rates, fixing payment timelines, reformatting labels, injecting warnings, and normalising every structured field.

The validator was not performing validation. It was performing reconstruction.

### The Problems

The most fundamental issue was waste. The pipeline spent significant token budget asking the AI to generate structured data that the system already had in the database. Rebate rates, cap amounts, payment timelines, currency scores, eligibility rules, weather data, crew costs, none of these require AI judgment. They are database lookups and arithmetic. Yet the AI was asked to produce them, and the validator immediately threw them away.

This created a second problem, hallucination surface area. Every field the AI generated was a field it could get wrong. An AI asked to produce a rebate rate might return 35 percent when the database says 34 percent. An AI asked to format a cap amount might say 25 million when the actual figure is 23.5 million. An AI asked to compute a weighted score might apply the wrong formula. Each of these errors required a corresponding patch method in the validator to detect and correct. The more fields the AI touched, the more correction logic was needed.

The validator grew into a correction engine. What began as a safety net became the actual report builder, but one that worked by reacting to AI mistakes rather than constructing the report correctly from the start. When a new incentive programme was added to the database, or a rate changed, or a new field was introduced, the developer had to update both the AI prompt and the validator patch method. If either was missed, the report would contain stale or contradictory data.

Latency was another consequence. The AI was asked to produce a large JSON response containing thousands of tokens of structured data that would be overwritten. The output tokens for financial tables, score breakdowns, and formatted labels added measurable time to the API call without contributing to the final report.

Debugging was difficult. When a report contained an error, it was rarely clear whether the AI had generated the wrong value, or the validator had failed to correct it, or the validator had overcorrected something the AI got right. The interaction between a 440-line prompt and 25 patch methods created a system where causality was hard to trace.

The prompt itself was fragile. It contained inline business rules, formatting instructions, programme-specific exceptions, and scoring formulas. Any change to these rules required editing a long string template, testing the AI output against the full validator chain, and hoping the AI would consistently follow the updated instructions. It often did not.

Finally, the architecture made testing unreliable. Unit tests for the validator were really integration tests, they required mocking an AI response, running it through the full patch chain, and asserting the output. A test that passed might still produce wrong data in production if the AI returned a slightly different structure than the mock.

---

## The New Pipeline

### Design Principle

The new pipeline inverts the responsibility. Instead of asking the AI to generate the full report and then correcting most of it, a deterministic builder constructs the complete report skeleton from database data first, and the AI fills only the narrative fields it is actually qualified to write.

The AI never sees raw database rows. It receives a pre-built skeleton containing all the financial figures, scores, and structured data already computed, and its job is to write reasoning, advantages, risks, and qualitative assessments that reference those figures. The AI is a writer, not a calculator.

### Architecture

The pipeline has four stages.

Stage one is data loading and enrichment. The ReportService loads all datasets from the database, incentive programmes, crew costs, cast costs, comparables, grants, festivals, and weather data. It then computes derived signals including shoot window analysis, currency advantage scores, territory financials with rebate calculations, and budget conversions. These derived values are injected into the datasets dictionary and are available to all downstream components.

Stage two is skeleton construction. The ReportBuilder receives the enriched datasets and the user's request metadata. It constructs the complete report structure with every section populated from database-authoritative data. Territory rankings include three deterministic scoring dimensions, incentive strength, incentive reliability, and currency advantage, computed from verified programme data and live exchange rates. Incentive estimates include rates, caps, qualifying spend thresholds, payment timelines, eligibility rules, and stacking information, all drawn directly from the database. Financial analysis includes budget scenarios with rebate calculations that respect programme caps, ATL exemptions, qualifying spend types, and tax adjustments. Weather logistics, crew insights, comparables, grants, festivals, attributions, and section explainers are all built deterministically.

The builder sets approximately 15 narrative fields to null. These are the fields that genuinely require qualitative judgment, things like territory reasoning, key advantages, infrastructure assessments, crew availability descriptions, comparable relevance explanations, and the executive summary narrative. It also leaves three scoring dimensions null, cost efficiency, crew depth, and infrastructure, because these require the AI to assess qualitative factors like studio quality, crew availability during peak season, and equipment rental market depth that are not captured in structured database fields.

Stage three is AI narrative fill. The ScriptAnalysisService sends the pre-built skeleton to the AI along with the script analysis and project metadata. The AI prompt is focused and constrained, approximately 80 lines compared to the former 440. It asks the AI to return only the narrative fields, structured as a JSON object with specific keys for each section. The AI can reference the financial data in the skeleton, ensuring its narrative is grounded in the actual numbers rather than its own calculations.

When the AI response arrives, a merge function walks the skeleton and inserts the narrative content into the corresponding fields. Database-authoritative fields are never overwritten. For key risks, the AI can only append additional risks to the list the builder already populated from database warnings, it cannot remove or replace them. For the three AI-generated scoring dimensions, the values are clamped to the 0 to 100 range. If the AI call fails entirely, safe defaults are applied and the report is still usable because every structured field was already correct.

After the merge, the builder's score computation runs. It takes the three database-computed dimensions and the three AI-provided dimensions, applies the weighting table that matches the user's stated production priority, and produces the overall territory score for each location ranking. This score determines the final territory order.

Stage four is integrity assertion. The ReportValidator, now reduced from over 3,000 lines to roughly 200, runs lightweight structural checks. It verifies that all required sections exist, that scores fall within the 0 to 100 range, that financial figures are internally consistent, that all territories in the rankings have corresponding incentive data, and that no narrative fields remain null. It sorts the location rankings by descending score and refreshes the executive summary headline to match the top-ranked territory. It does not recalculate any values. It does not patch any fields. It asserts and logs.

### Why It Is Better

The AI now generates approximately 15 fields instead of 100. This eliminates hallucination for the 85 fields that are purely deterministic. A rebate rate cannot be wrong because the AI never produced it. A payment timeline cannot be stale because it came directly from the database. A score cannot use the wrong formula because the builder applied the formula, not the AI.

Token consumption drops significantly. The input no longer includes thousands of tokens of raw database rows. The output no longer includes thousands of tokens of structured data that would be overwritten. The skeleton provides context efficiently, and the narrative response is compact.

The prompt is stable. Because the AI is only asked to write narrative text, the prompt does not need to contain business rules, formatting instructions, or programme-specific exceptions. When a new incentive programme is added or a rate changes, only the database and the builder need updating. The prompt does not change.

Testing is straightforward. The builder is a pure function of its inputs. Given the same datasets and metadata, it produces the same skeleton every time. Unit tests assert specific field values without mocking AI responses. The merge function can be tested with synthetic AI output. The validator assertions can be tested independently.

Debugging is direct. If a financial figure is wrong, the issue is in the builder or the database. If a narrative is wrong, the issue is in the AI response or the merge. If a score is wrong, the formula in the builder's compute method is the single place to look. There is no ambiguity about which component is responsible for which field.

The validator is a safety net, not a correction engine. It catches structural anomalies that should not happen rather than routinely rewriting the report. A warning from the validator indicates a genuine bug, not normal operation.

The architecture separates concerns cleanly. The database owns the data. The builder owns the structure and computation. The AI owns the narrative. The validator owns the assertions. Each component has a single responsibility and a clear contract with the others.

---

## Pipeline Comparison

                                    Former Pipeline         New Pipeline

AI prompt size                      440 lines               80 lines
Fields generated by AI              approximately 100       approximately 15
Fields overwritten by validator     approximately 80        0
Validator size                      3,000 plus lines        200 lines
Validator role                      Correction engine       Assertion checker
AI output used in final report      approximately 20%       100%
Hallucination surface area          All structured fields   Narrative text only
Deterministic reproducibility       No                      Yes for all structured fields
Testing approach                    Mock AI plus patches    Pure function assertions
New incentive programme change      Prompt plus validator   Database plus builder only

---

## Component Responsibilities

ReportService orchestrates the pipeline. It loads datasets from the database, computes derived signals like currency scores and territory financials, then hands everything to the builder and the script service. It is the entry point for report generation.

ReportBuilder constructs the deterministic skeleton. It owns all structured field computation including incentive estimates, financial analysis, territory scoring dimensions, weather risk assessment, crew cost formatting, grant and festival filtering, comparable selection and relevance scoring, and section explainer text. It produces a complete report with null values only where AI judgment is required.

ScriptAnalysisService manages the AI interaction. It constructs the narrative fill prompt, calls the AI API, parses the response, merges narrative content into the skeleton, and computes overall scores once all six dimensions are available. It handles API failures gracefully by applying safe defaults.

ReportValidator performs post-generation assertions. It checks structural completeness, score bounds, financial consistency, and territory coverage. It sorts territories by final score and refreshes the executive summary headline. It logs warnings for any anomalies.

Helpers module contains shared pure functions used by both the builder and the validator. These include incentive indexing, rate formatting, cap formatting, money formatting, currency symbol lookup, and bankability label computation.

---

## Data Flow

    Database
        |
    ReportService loads datasets
        |
    ReportService computes derived signals
    (currency scores, territory financials, shoot window, FX rates)
        |
    ReportBuilder.build()
    constructs complete skeleton from enriched datasets
    (all structured fields populated, narrative fields null)
        |
    ScriptAnalysisService.generate_production_analysis_v2()
    sends skeleton plus script analysis to AI
    AI returns narrative-only JSON
    merge function inserts narratives into skeleton
    compute_overall_scores applies weighted formula
        |
    ReportValidator.assert_integrity()
    structural checks, score sorting, headline refresh
        |
    Complete Report
    (PDF rendering, persistence, delivery)

---

## Execution and Job Queue

The pipeline above is the *work*. Where that work runs is a separate concern.

Paid and B2B reports are long-running and failure-prone (multiple AI calls, PDF
render and upload, transactional emails). The `POST /api/reports` handler does
the cheap, synchronous parts inline — validate the request, read and extract the
script text in memory, run a Claude reachability pre-flight so a user is never
charged for an unreachable service, create the report row, and consume a credit
where applicable — then hands the heavy pipeline off to a worker via
`_dispatch_report_job`. The handler returns immediately with the `report_id`,
and the frontend polls `GET /api/reports/{report_id}/status`. The report row's
`status` column (`processing` / `completed` / `failed`) is the durable source of
truth for progress.

Dispatch has two modes, controlled by `REPORT_QUEUE_ENABLED`:

- **Durable queue (production).** The job is enqueued onto a Redis-backed RQ
  queue (`app/core/queue.py`) and executed by a separate worker process
  (`python -m app.worker`). Because the job lives in Redis and is owned by the
  worker, a web-process deploy, crash, or restart mid-generation no longer loses
  the work. Run at least one worker alongside the web process.
- **In-process fallback (local dev, tests).** When disabled, the job runs in
  FastAPI `BackgroundTasks` in the web process — no Redis or worker required.

`process_report_task` is the single job body for both modes. It is enqueued by
reference (RQ stores its dotted path) with plain-string arguments only; it
re-resolves `Settings` from its own environment rather than receiving a settings
object, so the worker is self-contained. On failure it records the failed status
on the report row and refunds any pay-per-report credit that was consumed, so a
report that never generated is never charged.
