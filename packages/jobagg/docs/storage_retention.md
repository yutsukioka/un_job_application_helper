# Storage retention and exact cold archives

The storage policy controls redundant publication-copy growth while preserving
the live databases and previously fetched evidence. It does not guarantee a
constant disk footprint or certify that every source is current or complete.
Older attachment evidence may remain in historical archives rather than the live
database; those archives must remain available.

The separate [artifact compression API](storage_compression.md) adds a versioned
resolver and explicit migration tools. Existing retention and publication readers
have not adopted that manifest; committing the API does not enable migration.

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

The default remains disabled, with a 20-second/two-group budget when enabled.
Reviewed configurations may choose up to 120 seconds and eight groups per tick;
at least two newest completed groups remain protected. For a measured backlog,
a 60-second/four-group pass is a bounded starting point to validate, not an
assumed sustainable setting. Worker and publication limits remain independent. As before, maintenance time
is additional to `total_timeout_seconds` for worker/publication. Account for
the sum when reviewing a schedule: a long invocation makes the next cron tick
return `lock_busy`, not overlap. The larger allowance is an explicit tuning
option, not a changed production setting.

Normally retention runs after terminal worker/publication work under the shared
owner lock. If the reserve is already exhausted, an enabled dispatcher may enter
**maintenance only** after validating the mount, sentinel and runtime paths and
acquiring that same lock. Operator maintenance and unresolved publication still
block cleanup. Only a fresh storage check that meets the unchanged reserve can
admit the worker. Otherwise the invocation reports its retention outcome and
capacity failure without launching providers or establishing a fetch-failure
hold. A prior fetch-failure hold is never cleared by maintenance. Each invocation
uses at most one maintenance budget, before or after work.

Retention still needs free space for its small durable receipts; this is not a
zero-free-space guarantee. A direct bounded retention command remains available
after the operator checks the mounted volume. Maintenance errors and deadline
deferrals are reported separately in `storage_retention` and persistent health;
post-publication maintenance failure does not change publication health. Dry-run
retains the hard reserve check and performs no pruning.

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

### Worker implementation binding

The worker binds every Python file in the `jobagg` package, including storage
maintenance modules, plus the registry and robots policy. Any change during a
fetch tick refuses acceptance; a changed fingerprint at the next startup refuses
the existing workspace. This protects against accidental and unreviewed changes.

A legitimate deployment needs a reviewed update to the workspace marker. At a
clean publication boundary, pause dispatch and acquire the shared owner lock.
Compare the exact old and new file manifests, verify the registry and robots
policy, preserve the original marker, and update its implementation hash only
for the reviewed code. Confirm a read-only worker preview before resuming. Keep
the database, captures, attempts, task holds, quotas and concurrency history.
This marker update is an explicit deployment step; the worker must never adopt a
new hash automatically just because code changed. Without that step, even an
intentional maintenance change correctly pauses fetching.

### Capacity limits

New unique fetches, details, attachments, logs and publication evidence continue
to accumulate. Cold compression is **not** run automatically by the dispatcher.
The publisher still renders complete exports for affected datasets. On macOS,
supported filesystems now use copy-on-write clones for sealed export copies,
including rollback copies and live replacement. Each clone has its own inode;
later writes cannot change another copy. Hash readback, fsync, atomic replacement
and recovery journals remain required. Unsupported platforms/filesystems use
verified streaming copies; capacity, permission and I/O failures remain errors.
This reduces duplicate physical allocation, not unique export creation. Logical
file sizes can therefore overstate reclaimable physical space.

Each pruning pass scans historical metadata and hashes selected copies. Durable
validation progress retains completed file hashes, bound to the exact group
controls, expected manifest and file identity including ctime. Changed evidence
invalidates progress, and the complete group is rechecked before deletion intent.
This progress has the same trusted-local-owner assumption as existing retention
journals; it is not protection against a writer forging both evidence and cache.
An interrupted hash of a single file restarts; that file still needs to fit the
configured budget. Check retained progress, eligible backlog, actual reclaimed
space and creation rates over repeated cycles. Increase the bounded budget only
through a reviewed configuration change when measured throughput requires it.

A successful individual cleanup does not demonstrate sustainable retention.
The [October recurrence evidence and validation plan](storage-recurrence-20261014.md)
distinguishes created derivatives, removed copies and fresh fetched data. Provider
task backlog and unrelated publication timeout fixes remain separate work.

The older `jobagg.retained_artifacts` scanner accepts a completed and verified
`storage-retention.json` retirement receipt and validates a cold archived plan
before scanning remaining generations. It still rejects unfinished or changed
receipts and corrupt archives. The two tools protect different evidence: the
older tool stores exact export-copy bytes, while the dispatcher prunes verified
duplicate export copies after publication.

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
  packages/jobagg/tests/test_worker_implementation_binding.py \
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
