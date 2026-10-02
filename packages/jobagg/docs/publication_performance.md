# Bounded publication preparation

Publication prepares a projection of every accepted observation before applying
the publication record limit. A limit of 40 changed jobs therefore does not bound
the preparation workload. Accepted detail lookup joins immutable successful
attempts to a task identity; the task may already be pending for another refresh.

## Lookup indexes

`Worker.initialize()` installs two idempotent indexes in both new and existing
workspaces:

- `idx_remediation_tasks_detail_identity(source_id, kind, external_id, task_id)`
- `idx_remediation_attempts_detail_lineage(task_id, source_id, kind, status,
  finished_at DESC, attempt_id)`

The SQL predicate, ordering, artifact hash checks, accepted proof comparison and
description comparison are unchanged. Every matching successful historical
attempt remains eligible evidence. Failed attempts, unfinished attempts, changed
artifacts and mismatched identities remain excluded. Initialization does not
rewrite an implementation marker or reset attempts, quotas, holds or concurrency.
Read-only previews do not install indexes.

Explicit worker indexes are copied into publication projections after their table
rows are copied and verified. They also support the publisher's repeated lineage
checks against a sealed projection. Index creation is bounded by the projection's
destination progress handler.

## Deadlines and diagnostics

The projection enforces its deadline during source SQL, destination SQL, row
copy/readback and accepted artifact hashing. When SQLite raises `SQLITE_INTERRUPT`
and the configured deadline has expired, the projection reports `TimeoutError`
with the current phase. An unrelated database interruption remains its original
database error. Snapshot integrity validation follows the same rule. Failure
rolls back the unpublished projection, clears both SQL progress handlers and
removes only the snapshot creator's unpublished temporary files.

The projection receipt includes `timings` outside the deterministic manifest:
initial diagnostics, accepted detail lineage resolution, per-table copy,
readback and index creation, and total elapsed seconds. These observations do not
change the manifest hash, acceptance filters or completeness certification.

## Reproducing a measurement

From `packages/jobagg`, run:

```sh
PYTHONPATH=. python tools/benchmark_publication_lineage.py \
  /absolute/path/to/worker/jobs.sqlite3 \
  /absolute/path/to/a/new/local/benchmark-directory
```

The source opens read-only. The tool first creates an online SQLite backup in the
separate output directory. Index creation/removal and projections operate only on
that backup. Use a local output directory with space for the database and two
projections. Immutable accepted artifacts must remain available at their recorded
paths. The tool compares exact ordered query rows, exact matching evidence and
complete deterministic projection manifests before and after the migration.
Separate deadlines bound backup, representative lookups and full projections.

A single before/after run benefits from filesystem caching and is not a
controlled cold-cache benchmark. Query plans establish whether full historical
attempt scans were eliminated. Elapsed times and size overhead apply to the
measured snapshot; they do not establish live source freshness or catch-up rate.

## Source recovery and deployment

EU Careers' observed redirect to `selection.eu-careers.europa.eu` requires an
exact reviewed redirect URL in its source configuration. Other host, protocol
and redirect checks continue to apply. A changing IDB count during enumeration
defers the first two failed attempts by 15 minutes. A third inventory-change
failure with the same input binding blocks the listing task for review. The
persisted semantic count survives worker restarts and intervening host/budget
deferrals; a successful inventory starts a fresh retry sequence. Captures and
attempt history remain intact, and no incomplete inventory is accepted. Stable
count shortages and invalid identities still fail validation.

Historical blocked or dead-letter tasks require a separately audited selected
repair after deployment; code changes alone must not clear their history.
HTTP 403, TLS review stops and unresolved identity mismatches require their own
source evidence. A zero eligible backlog alone does not certify all sources:
held and blocked tasks are excluded from eligibility.

A production deployment requires the existing reviewed single-marker process:
verify the exact old/new code and registry changes at a completed publication
boundary, pause with the maintenance marker, hold the shared owner lock, preserve
protected database/policy/attempt/evidence state, and deliberately record the
reviewed full implementation fingerprint. Never adopt an observed hash
automatically. Resume only after read-only preview and integrity checks succeed.
After deployment, measure several worker/publication cycles and source listing
and detail ages; let adaptive concurrency use those validated observations.

## Measurement on 29 September 2026

One read-only online backup of the operational worker contained 4,194 observed
jobs and 27,984 historical attempts, with a 3,456,380,928-byte database. Complete
projections ran sequentially on that fixed local copy:

| Measurement | Unindexed | Indexed |
| --- | ---: | ---: |
| Complete projection | 214.296 s | 34.003 s |
| Accepted detail lineage phase | 208.097 s | 27.990 s |
| SQL over 250 sampled identities | 11.937 s | 0.008408 s |
| Matching helper over those identities | 13.250 s | 1.617 s |

Both complete deterministic manifests matched exactly, including every table
row count and readback digest:
`87dbd99d33bbabf34e08c6aa1046ac299df8ba7da3a5ba2ceff60443df94c29c`.
The 250 sampled identities spanned 40 sources; every ordered SQL row and exact
accepted artifact binding matched. Both projections passed SQLite quick_check.
The original query plan scanned historical attempts; indexed plans searched both
identity and successful-attempt indexes.

Index creation took about 0.096 seconds on the local copy. The indexes occupied
5,005,312 bytes; the source copy reused free pages, so its file size did not grow.
The indexed projection grew by 1,413,120 bytes. These are measured index costs,
not estimates of future storage growth.

The observed complete-projection ratio was about 6.3 times. The indexed run had
a warmer filesystem cache, and other local tests were running concurrently;
this single experiment does not establish a controlled production speedup.
Artifact hashing and row verification remain necessary and consume time.

A contemporaneous source audit found nine stale listings: WFP, TBI, UNHCR,
Global Fund, World Bank, UNOPS, OSCE, IDB and EU Careers. The EU redirect and
IDB changing-total repairs address two proven mechanisms. Host access/TLS
review stops, historical task dispositions and other identity failures still
require selected evidence-backed operational recovery.

Publication plans against the same fixed local backups of 50 output databases
were also exactly equal after excluding only creation-time and database-path
metadata. Both plans proposed 40 changes and 5 listing frames, preserved all
54 existing shorter-text rejections, and reported 711 unchanged records.
No publication execution or live output writes were performed.
