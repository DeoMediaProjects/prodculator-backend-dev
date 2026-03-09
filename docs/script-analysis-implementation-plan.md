# Script Analysis Scalability Implementation Plan

## Objective

Improve uploaded-script parsing accuracy and reliability for large scripts by replacing single-shot truncated analysis with a chunked, staged pipeline and stage-specific Anthropic runtime settings.

## Why This Change

Current behavior trims script text to 12,000 characters before analysis (`app/modules/scripts/service.py`), which drops most content for longer scripts and can bias output toward opening/middle/final segments.

This plan addresses:

- Incomplete coverage of long scripts.
- JSON fragility from unconstrained model output.
- Limited observability of truncation/fallback events.
- Single global token/timeout settings that do not match different call types.

## Target Architecture

Implement a 3-stage script analysis pipeline:

1. Chunk extraction stage (map)
- Parse script into ordered chunks (token-aware with overlap).
- Extract structured signals from each chunk (locations, cast/activity intensity, stunts, night scenes, VFX signals, etc.).

2. Aggregation stage (reduce)
- Merge chunk-level signals into one canonical script profile.
- Resolve duplicates and frequency conflicts deterministically.

3. Final report stage
- Use aggregated script profile + metadata + datasets to generate production analysis output.

### Design principles

- Token-aware limits, not character-only truncation.
- Schema-constrained model output for all machine-readable responses.
- Explicit handling of model truncation (`stop_reason == "max_tokens"`).
- Deterministic merge logic outside the LLM where feasible.

## Stage-Specific Environment Variables

Add these new settings in `app/core/config.py` and `.env.example`:

- `ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK=1500`
- `ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE=3000`
- `ANTHROPIC_MAX_TOKENS_REPORT=7000`
- `ANTHROPIC_TIMEOUT_SCRIPT_CHUNK=120`
- `ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE=150`
- `ANTHROPIC_TIMEOUT_REPORT=180`
- `SCRIPT_CHUNK_TARGET_TOKENS=1800`
- `SCRIPT_CHUNK_OVERLAP_TOKENS=200`
- `SCRIPT_MAX_CHUNKS=80`

Backward compatibility:

- Keep existing `ANTHROPIC_MAX_TOKENS` and `ANTHROPIC_ANALYSIS_TIMEOUT` as fallbacks.
- If stage-specific vars are missing, use legacy values.

## Implementation Phases

## Phase 1: Configuration and plumbing

Scope:

- Add stage-specific settings to `Settings`.
- Add helper methods in `ScriptAnalysisService` to select token/timeout per stage.
- Log effective runtime config at analysis start.

Deliverables:

- Config support merged with backward compatibility.
- Unit tests for config fallback behavior.

Acceptance criteria:

- Service runs unchanged if only legacy env vars exist.
- Stage-specific env vars override legacy values when present.

## Phase 2: Chunking and extraction (map)

Scope:

- Add script segmentation utility:
  - Prefer scene boundaries for FDX/Fountain when available.
  - Fall back to token-window chunking for plain text/PDF extraction output.
- Create chunk-level extraction prompt + JSON schema output mode.
- Retry policy for rate limits and truncation recovery.

Deliverables:

- `analyze_script_chunks(...)` method returning normalized chunk summaries.
- Structured extractor schema validated before merge.

Acceptance criteria:

- Large scripts (>100 pages) process without dropping to a single 12k-char sample.
- Chunk calls never rely on free-form JSON repair as primary path.

## Phase 3: Aggregation (reduce)

Scope:

- Build deterministic merge logic for chunk outputs:
  - Location dedupe + frequency rollups.
  - Max/weighted synthesis for production complexity signals.
  - Evidence notes collected for traceability.
- Optional LLM-assisted aggregate pass using reduced structured input.

Deliverables:

- `aggregate_chunk_analysis(...)` that outputs `ScriptAnalysisResult` payload.
- Confidence scoring per major section.

Acceptance criteria:

- Aggregated output stable across reruns with same input.
- Output quality improves on long-script benchmarks.

## Phase 4: Integrate with report generation

Scope:

- Replace current `analyze(...)` internals to use chunk+aggregate flow.
- Keep external route contracts unchanged (`/api/scripts/analyze`, paid report background task).
- Preserve existing fallback behavior but add explicit failure reason metadata.

Deliverables:

- Drop-in integration with existing report flow in `app/modules/reports/router.py`.

Acceptance criteria:

- Existing API responses remain schema-compatible.
- Paid/b2b processing succeeds for scripts previously degraded by truncation.

## Phase 5: Observability and guardrails

Scope:

- Add metrics/log fields:
  - `script_chars`, `estimated_input_tokens`, `chunk_count`, `dropped_chunks`, `stop_reason`, `fallback_used`.
- Add alert thresholds for high fallback rates or truncation incidents.

Deliverables:

- Structured logs and dashboard-ready counters.

Acceptance criteria:

- Operators can identify why/where failures happen without reproducing locally.

## Files Expected to Change

- `app/core/config.py`
- `.env.example`
- `app/modules/scripts/service.py`
- `app/modules/scripts/schemas.py` (if chunk schemas are added)
- `app/modules/reports/router.py` (minimal integration updates, if needed)
- `tests/modules/scripts/*` (new and updated tests)

## Test Plan

1. Unit tests
- Stage-specific env resolution and fallback behavior.
- Chunk boundary generation and overlap correctness.
- Aggregation merge correctness for dedupe and frequency.

2. Integration tests
- End-to-end script analysis with small, medium, and large scripts.
- Paid report background flow with large script fixture.

3. Failure-mode tests
- Simulate max-token truncation on chunk stage.
- Simulate timeout and verify retry/backoff behavior.
- Verify fallback path includes explicit error context.

## Rollout Strategy

1. Ship behind feature flag:
- `SCRIPT_ANALYSIS_CHUNKED_ENABLED=false` by default.

2. Canary enablement:
- Enable for internal/admin users first.
- Monitor fallback/truncation/error rates for 48-72 hours.

3. Gradual expansion:
- 10% -> 50% -> 100% of paid/b2b traffic.

4. Post-rollout cleanup:
- Remove legacy hard-trim-only logic after stability window.

## Recommended Immediate Env Values

If implementing this plan now, use:

- `ANTHROPIC_MAX_TOKENS_SCRIPT_CHUNK=1500`
- `ANTHROPIC_MAX_TOKENS_SCRIPT_AGGREGATE=3000`
- `ANTHROPIC_MAX_TOKENS_REPORT=7000`
- `ANTHROPIC_TIMEOUT_SCRIPT_CHUNK=120`
- `ANTHROPIC_TIMEOUT_SCRIPT_AGGREGATE=150`
- `ANTHROPIC_TIMEOUT_REPORT=180`

Temporary fallback (before stage split is coded):

- `ANTHROPIC_MAX_TOKENS=7000`
- `ANTHROPIC_ANALYSIS_TIMEOUT=180`

## Risks and Mitigations

- Risk: Higher token usage increases cost.
- Mitigation: Cap chunk count, enforce concise chunk schema, and reduce per-chunk max tokens.

- Risk: Latency increase for very large scripts.
- Mitigation: Parallel chunk processing with bounded concurrency and retry budget.

- Risk: Schema drift or parse issues.
- Mitigation: JSON schema output mode + strict validation before merge.

## Definition of Done

- Long scripts are processed with full coverage via chunk pipeline.
- Stage-specific env controls are active in production.
- Fallback usage is rare and observable.
- Output quality for large scripts is measurably better than baseline.
