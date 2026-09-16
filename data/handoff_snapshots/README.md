# Engine handoff snapshots

These are validated source snapshots, not production recommendation tables.

| File | Source archive SHA-256 | Snapshot |
| --- | --- | --- |
| `festivals_v2_1_2026-09-15.json` | `d5f134fa9b7c99b2b56ab47c9578a3ac25fdd2245764d8377011b03ed58446f0` | 380 inventory records, 76 paid-safe on 15 September 2026 |
| `markets_labs_wip_v1_2026-09-16.json` | `fbd0a1cb9b42d8523bfc3f43b3e8de25ecff5322fec964b677aa4cd204a050df` | 203 track-level records, 43 paid-safe on 16 September 2026 |

The Markets source workbook mixes Excel Boolean cells and strings `TRUE`/`FALSE` in its `paid_safe` column. The preparation script converts only those representations to JSON Booleans. Its other cell values are copied without editorial changes.

Run `scripts/prepare_engine_handoff_snapshots.py` with the two original ZIP paths to reproduce these files. No import should treat the dated `paid_safe` fields as permanent. Current cycle, deadline, verification, routing and project-specific eligibility must be evaluated at runtime before any paid recommendation appears.

Important Festival limitation: none of the 76 snapshot paid-safe rows has a structured `deadlines` array. Submission timing appears in prose, including seven rows that say a deadline needs verification. Current, section-specific dates must be verified and stored separately before runtime cutover; they must not be guessed from this file.
