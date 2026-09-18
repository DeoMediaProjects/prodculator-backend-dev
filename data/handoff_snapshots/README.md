# Engine handoff snapshots

These are validated source snapshots, not production recommendation tables.

| File | Source archive SHA-256 | Snapshot |
| --- | --- | --- |
| `festivals_v2_1_2026-09-15.json` | `d5f134fa9b7c99b2b56ab47c9578a3ac25fdd2245764d8377011b03ed58446f0` | 380 inventory records, 76 paid-safe on 15 September 2026 |
| `markets_labs_wip_v1_2026-09-16.json` | `fbd0a1cb9b42d8523bfc3f43b3e8de25ecff5322fec964b677aa4cd204a050df` | 203 track-level records, 43 paid-safe on 16 September 2026 |
| `sales_distribution_v1_2026-09-16.json` | `f110ba8a89653e387939e983452342327b58a01092157227316fb52bf1448bdc` | 101 company records and 205 sourced title-company relationships; source staging only |
| `grants_v2_2026-09-04.json` | `7c5bc7576aae435fbcc6926d7cb9997e8dcf0c9edf5a02ab84dfb89a95091e1a` | 253 grant records, 204 paid-match-eligible in the 4 September 2026 freeze, plus the five migration maps |

The Markets source workbook mixes Excel Boolean cells and strings `TRUE`/`FALSE` in its `paid_safe` column. The preparation script converts only those representations to JSON Booleans. Its other cell values are copied without editorial changes.

Run `scripts/prepare_engine_handoff_snapshots.py` with the two original ZIP paths to reproduce these files. No import should treat the dated `paid_safe` fields as permanent. Current cycle, deadline, verification, routing and project-specific eligibility must be evaluated at runtime before any paid recommendation appears.

Important Festival limitation: none of the 76 snapshot paid-safe rows has a structured `deadlines` array. Submission timing appears in prose, including seven rows that say a deadline needs verification. Current, section-specific dates must be verified and stored separately before runtime cutover; they must not be guessed from this file.

The Sales/Distribution snapshot is a faithful read of the frozen workbook's
`Master` and `Comparable Relationships` sheets. It retains raw source labels,
including access states that do not map directly to the canonical engine
vocabulary. `scripts/prepare_commercial_handoff_snapshot.py` reproduces it from
the reviewed workbook and rejects a changed workbook hash. Do not import this
snapshot directly as approved paid matches. Review material fields and access
routes individually, and never infer a sales relationship from a distribution
relationship or vice versa.

The Grants snapshot carries the master and all five migration maps together,
because the maps are what decide which live rows survive and separating them
invites an import run against one without the other.
`scripts/prepare_grants_handoff_snapshot.py` reproduces it and rejects a changed
master hash.

It also carries an `open_questions` block recording what the freeze does NOT
settle: three duplicate canonical titles, one record still routed to the
Incentive Engine, and four legacy mappings that resolve to no live ID. These are
not defects in the snapshot; they are decisions awaiting a human, and
`scripts/reconcile_grants_v2.py` refuses to report the migration as ready while
any of them stands. Do not resolve a duplicate by title string — the handoff's
own instructions require semantic review.
