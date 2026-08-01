# Atlas Storage Retention and Cache Policy

Status: Accepted design record with staged follow-up work
Last updated: 2026-08-01

## Purpose

Atlas keeps current vacancies usable offline while also preserving historical
vacancy evidence. This document records which retention behavior is deliberate,
which duplication is technical debt, and how production and diagnostic storage
should diverge as the product matures.

## Decisions

| Area | Decision |
| --- | --- |
| Native Apple detail cache | Preserve historical details during normal incremental refresh. Do not evict a detail merely because a vacancy closes or disappears from the current listing. |
| Cached diagnostics | Split compact user-facing detail from raw diagnostics in a future API/cache schema. Diagnostics must not be included in the default production cache. |
| jobagg run archives | Do not delete all previous archives indiscriminately. Introduce verified, tiered retention for production and failure-oriented retention for debugging. |
| Flutter listing cache | Replace duplicated listing arrays with one canonical job collection plus result-key references in a versioned cache schema. |
| Flutter desktop location | Store the cache under the OS application-support directory, never the system temporary directory. Import a legacy temporary cache without overwriting durable data. |

## 1. Native Detail History Is Intentional

The normal Apple cache warmup requests missing details and writes individual
files under the user's Application Support directory. It does not enumerate and
delete files simply because their job keys are absent from a later listing. This
is intentional.

Historical details support:

- viewing vacancies after they close or are removed from the source site;
- explaining saved searches and application-tracker records later;
- comparing changed descriptions, deadlines, grades, and classifications;
- diagnosing source-parser regressions without re-downloading an unavailable page.

The retention invariant is:

> Normal refresh may add or improve a cached detail, but absence from the latest
> listing is not deletion evidence.

Automatic age-based detail eviction must not be introduced without a separate,
user-visible retention policy. An explicit **Clear Local Data** action may remove
local copies after confirmation. The server database and archives remain the
authoritative history; a device cache must not become the only copy.

### Current boundary

`startDetailCacheWarmupIfNeeded` follows the keep-history behavior. The explicit
full-local-save path, `replaceLocalSaveWhenComplete`, stages the details in the
new snapshot and swaps the entire detail directory atomically. That replacement
operation is intentionally distinct from normal refresh, but it can omit details
that are not present in the replacement snapshot.

If the product requirement becomes "never prune details during any refresh or
replacement," the replacement path must merge the old detail directory into the
staging directory before commit. That should be a focused follow-up with disk-use
limits and tests; this storage-location change does not alter Apple cache behavior.

## 2. Large Diagnostic Duplication

The detail API currently returns normalized job fields and generated
`display_sections`, while the latter can also contain a formatted copy of raw
source data. Description text can therefore appear in the normalized description,
structured display sections, and raw diagnostics. Persisting all three forms on
every client wastes disk and transfer bandwidth.

### Production-ready direction

1. Make the normal detail response compact and user-facing.
   - Return canonical fields, normalized sections, deadline data, and URLs.
   - Exclude raw source JSON, parser diagnostics, source features, and repeated
     record dumps by default.
2. Expose diagnostics separately.
   - Use an explicit diagnostic endpoint or `include_diagnostics=true` request.
   - Apply authorization and redaction before exposing headers, cookies, tokens,
     or source-specific identifiers.
3. Store canonical content once.
   - Give large description/section bodies content hashes.
   - Reference the canonical body from display metadata instead of copying it
     into several fields.
4. Keep raw evidence server-side.
   - Compress raw payloads and associate them with source run IDs and job keys.
   - Clients fetch diagnostics only on demand and do not put them in the normal
     offline cache.
5. Measure the result.
   - Track compact response bytes, diagnostic bytes, cache bytes per job, and
     deduplication ratio before enforcing quotas.

### Debug-purpose direction

- Preserve full raw payloads for failed, degraded, parser-drift, or manually
  selected runs.
- Package diagnostics by run and source, compressed with a manifest and hashes.
- Retain request timing, endpoint, status, parser decision, and redacted response.
- Use an explicit TTL and size quota for successful debug captures; retain failed
  captures longer.
- Never put credentials, session cookies, authorization headers, or personal
  application data in diagnostic archives.

## 3. jobagg Full-Archive Retention

Each scheduled bundle publication currently allocates another timestamped archive
and moves the previous canonical outputs into it. Deleting every past archive can
remove the only convenient rollback point and evidence needed to explain parser
or classification changes. Keeping every full archive forever is also not viable.

### Why unconditional deletion is risky

- A newly published database can be structurally valid but semantically damaged.
- Closed source pages may no longer allow reconstruction of the prior details.
- Duplicate-source selection and noncanonical files are represented in archives.
- A parser regression may only become visible several runs later.
- Incident investigation benefits from exact run manifests and hashes.

### Production-ready future policy

Use configurable tiered retention rather than one rule for every archive. A
reasonable initial policy is:

- keep the current canonical output and at least two known-good rollback sets;
- keep seven daily, eight weekly, and twelve monthly successful checkpoints;
- keep failed or degraded publication evidence for at least 30 days;
- retain long-term historical jobs in the consolidated database independently of
  filesystem checkpoint retention.

Before garbage collection:

1. Write a run manifest containing source IDs, counts, health, schema version,
   file sizes, and SHA-256 hashes.
2. Verify the current publication and newest rollback checkpoint.
3. Copy required long-term checkpoints to durable/off-device storage.
4. Delete only archives selected by the retention policy.
5. Record every deletion in a garbage-collection report.

Storage can be reduced further with compressed archives, reflinks/hard links on
supported filesystems, or content-addressed blobs. SQLite online backups or
change-oriented snapshots are preferable to repeatedly copying unchanged files.

### Debug-purpose future policy

- Keep complete archives for all failed, blocked, degraded, pagination-incomplete,
  verified-empty-transition, and large-count-drift runs.
- Keep full successful runs only when explicitly requested or sampled.
- Store HTTP/parser fixtures separately from production rollback checkpoints.
- Apply a separate debug quota and TTL, with an override for an active incident.
- Redact secrets and make debug archives easy to delete as one run-scoped unit.

`--no-archive` is suitable only when another transactional backup and rollback
mechanism is already in place. It is not itself a retention strategy.

## 4. Flutter Listing Duplication

Flutter cache schema version 1 stores both:

- `search_response.results`, the currently displayed result subset; and
- `cached_all_jobs`, the complete offline listing.

The same job rows often occur in both arrays. The duplication increases JSON
encoding time, memory use, disk writes, migration payload size, and startup parsing.

### Recommended schema version 2

Persist each listing row once:

```text
jobs_by_key             canonical job records
visible_result_job_keys ordered keys for the saved search result
search_response         totals, offsets, facets, labels; no embedded jobs
```

The migration should:

1. Read both version 1 arrays and merge rows by stable `job_key`.
2. Prefer the most complete/newest row when duplicate values differ.
3. Reconstruct visible results from ordered keys.
4. Continue reading version 1 for at least one release while writing version 2.
5. Validate that every result key resolves before committing the new cache.

For a larger production cache, SQLite is the stronger destination: one jobs table,
normalized locations/classifications, full-text search, indexed filters, and an
atomic database replacement. The JSON version 2 design is a smaller intermediate
step and should be implemented in a separate PR because it changes cache and
AtlasVault migration contracts.

## 5. Durable Flutter Desktop Cache

Flutter desktop previously stored `atlas-local-cache-v1.json` under
`Directory.systemTemp/atlas_flutter`. Operating systems and cleanup tools may
remove that directory, so it cannot support a true offline experience.

The cache now resolves through the official Flutter
[`path_provider`](https://pub.dev/packages/path_provider) application-support API
and adds an `Atlas` namespace:

- macOS: the user's Application Support area under `Atlas`;
- Windows: the user's application-data area under `Atlas`;
- Linux: the platform application-support/data area under `Atlas`;
- Android: the existing native app-files location remains preferred.

On first use, Atlas copies the legacy temporary cache only when the persistent
target does not exist. The copy is staged and renamed in the target directory,
the target wins every conflict, and the legacy file is left untouched. If the OS
cannot provide persistent application support, Atlas disables disk caching rather
than silently returning to temporary storage.

## Follow-up Order

1. Ship and observe the durable Flutter desktop location migration.
2. Introduce compact production detail responses and on-demand diagnostics.
3. Add archive manifests and tiered garbage collection.
4. Migrate Flutter cache schema version 1 to a deduplicated version 2 or SQLite.
5. Decide whether explicit Apple full-save replacement must also merge all
   historical detail files.
