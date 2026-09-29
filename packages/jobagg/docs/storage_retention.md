# Storage retention and exact cold archives

The storage policy controls redundant publication-copy growth while preserving
the live databases and previously fetched evidence. It does not guarantee a
constant disk footprint or certify that every source is current or complete.
Older attachment evidence may remain in historical archives rather than the live
database; those archives must remain available.

## What is retained

| Data | Policy |
| --- | --- |
| Live databases/exports, worker database, raw HTTP responses, attachment blobs and historical evidence archives | Outside automatic deletion scope |
| Completed run `publication_snapshot.sqlite3` files | Keep at least the two newest, one latest completed snapshot per UTC day for 30 days, and one per UTC month for 12 months |
| Completed generation `exports-v2` and `before_exports` copies | Retire only after publication and export evidence validate; protect the current gate and at least two newest completed generations |
| Unresolved, incomplete or unverified groups | Preserve; ambiguous or malformed evidence stops the affected operation |
| Publication plans and row beforeimages | Stay hot until the separate cold-archive command is run; compressed copies are exactly restorable |
| Retirement receipts, publication results, checkpoints, recovery bindings and rollback blobs | Preserve |

Retirement validates original SHA-256 hashes, publication bindings, regular-file
paths and the held shared owner lock. It writes and syncs a durable intent before
unlinking the permitted copies, then records completion. Interrupted intents are
replayed before new groups. A retirement receipt records hashes and sizes, but
does **not** restore the removed duplicate bytes. Retained source evidence and
checkpoints have a different purpose from exact cold archives.

## Dispatcher integration

Add this fragment to the deployment configuration to enable retention:

```json
"storage_retention": {
  "enabled": true,
  "max_seconds": 20,
  "max_groups": 2,
  "keep_completed": 2
}
```

The default is disabled. Configuration validation limits maintenance to at most
20 seconds and two groups per tick, with at least two newest completed groups.
The dispatcher calls retention after the worker/publication phase is terminal,
while it still owns the shared lock. Unresolved publication, maintenance, a busy
owner or an unchanged-failure hold can prevent a cleanup attempt. Maintenance
errors and deadline deferrals are reported separately in `storage_retention`.
Dry-run reports the effective policy and performs no pruning.

The current implementation expects the existing physical layout under a real
Jobagg root:

```text
jobagg/
  deterministic-dispatcher-proposal/runs/
  deterministic-live-publication/
    generations/
    cold_archive/objects/
  output/
  remediation/manual-fetch-owner.lock
  ...preserved worker captures, blobs and historical archives...
```

The dispatcher derives this root from the parent of `state_dir`. Keep these
physical paths stable: publication evidence and archive manifests bind absolute
paths. Resolve any facade symlinks before supplying the root to the commands.
Change deployment configuration only at a clean publication boundary; its hash
is bound into publication recovery. Committing this code does not install cron
or enable another deployment's retention policy.

### Capacity limits

New unique fetches, details, attachments, logs and publication evidence continue
to accumulate. Cold compression is **not** run automatically by the dispatcher.
Each pruning pass scans historical metadata and hashes selected copies; a group
whose validation exceeds the time budget can repeatedly defer. Two groups per
tick provide limited catch-up capacity after skipped ticks. Check retention
status, eligible backlog and free-space trends; sustained deferrals require a
separate bounded maintenance run or a future performance change. The earlier
publication timeout and task backlog require separate fixes.

## Preview, archive and restore

From `packages/jobagg`, set `JOBAGG_STORE` to the real absolute storage root.
Both mutation commands below default to preview. Append `--execute` only to
apply a reviewed batch; they take the same nonblocking shared owner lock.

```sh
JOBAGG_STORE=/absolute/path/to/jobagg
.venv/bin/python -m jobagg.storage_retention \
  --root "$JOBAGG_STORE" --max-groups 2 --max-seconds 30

.venv/bin/python -m jobagg.storage_cold_archive archive \
  --state-dir "$JOBAGG_STORE/deterministic-live-publication" \
  --output-dir "$JOBAGG_STORE/output" \
  --dispatcher-state-dir "$JOBAGG_STORE/deterministic-dispatcher-proposal" \
  --shared-lock "$JOBAGG_STORE/remediation/manual-fetch-owner.lock" \
  --max-generations 1 --max-seconds 30
```

Cold archival compresses plans and beforeimages into deduplicated gzip objects.
Compressed and decompressed hashes must both match before hot originals are
removed. It protects the current gate, newest generations, recovery-bound plans
(including no-write proofs), and unfinished export-retirement intents. Keep its
manifest and compressed objects together. Cold-archive selection does not check
its deadline internally, so this cooperative processing budget is not a strict
wall-clock limit.

To verify or preview restoring one archive, set `JOBAGG_GENERATION` to its
32-character generation ID:

```sh
.venv/bin/python -m jobagg.storage_cold_archive verify \
  --state-dir "$JOBAGG_STORE/deterministic-live-publication" \
  --generation-id "$JOBAGG_GENERATION"

.venv/bin/python -m jobagg.storage_cold_archive restore \
  --state-dir "$JOBAGG_STORE/deterministic-live-publication" \
  --output-dir "$JOBAGG_STORE/output" \
  --shared-lock "$JOBAGG_STORE/remediation/manual-fetch-owner.lock" \
  --generation-id "$JOBAGG_GENERATION"
```

Append `--execute` to restore exact bytes. Allow sufficient free space for the
uncompressed files. Restoration preserves compressed objects for other
generations that may share them.

## Validation and deployment evidence

Tests use synthetic temporary stores and subprocesses. They cover default
read-only behavior, checkpoints, interrupted retirement/archive, exact restore,
corrupt hashes, symlink rejection, recovery-bound protection, ownership and
deadline handling. From the repository root:

```sh
PYTHONPATH=packages/jobagg python -m pytest \
  packages/jobagg/tests/test_storage_retention.py \
  packages/jobagg/tests/test_storage_cold_archive.py \
  reports/jobagg-remediation-plan-2026-09-10/proposed_runner/tests
```

On 29 September 2026, a separately authorized cleanup reclaimed 543.7 GB of
logical data (536.7 GB of redundant copies and 7.0 GB from lossless archival).
Available filesystem space increased by 550.5 GB at the measurement points.
Protected-file inventory comparisons, live database hashes, rollback blob hashes,
SQLite integrity checks and archive verification passed. These are measurements
from that deployment, not a forecast or a guarantee for another installation.
Private inventories, databases, production configuration and session scripts are
not included in this source change.
