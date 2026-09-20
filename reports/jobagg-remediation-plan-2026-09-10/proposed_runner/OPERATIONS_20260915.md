# Dispatcher recovery and external storage

The code revision was deployed on September 15. The interrupted publication was
recovered and verified across its 23 databases, 92 exports, and the HTTP API.
Active data is now under
`/Volumes/MacbookAirM2/un_job_application_helper/jobagg`; historical internal paths
resolve through compatibility links. The installed 15-minute cron entry is
unchanged. See the deployment receipts under
`reports/jobagg-recovery-2026-09-15/` for migration and runtime validation.
There is no automatic history pruning.

The shared snapshot implementation is loaded from the exact selected
`publication_cwd/jobagg/publication_snapshot.py`. The dispatcher and publisher use
that same validator. Historical requests stay unchanged. Their original database,
acceptance and every originally listed WAL hash must match. Only an originally
absent, newly present, stable **zero-byte regular WAL** is accepted while the shared
owner is verified; its exception is recorded in the new recovery attempt's
`snapshot_validation.json`. Symlinks, nonempty new WALs, changed bytes and missing
originally listed files remain errors.

Production configuration enables `sealed_publication_snapshots: true`.
The dispatcher creates an SQLite backup inside the run directory while still
holding the inherited owner. The backup passes integrity checking, has DELETE
journal mode, is closed, fsynced and made read-only. The request binds its exact
path/hash/size and acceptance hash. `worker_database` retains its original meaning
as provenance; the publisher reads `publication_snapshot.path`. Recovery consumes
that fixed backup even if later worker data changes. Custom older publishers keep
legacy behavior when the new flag is omitted. The snapshot preparation and
validation share the publication phase's deadline.

Every `--execute` invocation with a readable configuration writes a unique
`attempts/<id>/intent.json` before validating runtime inputs, and a terminal
`outcome.json`, including lock-busy, maintenance, storage and recovery preflight
failures. These files do not replace old run outcomes. `health.json` separates the
latest invocation, accepted worker and completed publication. Use `--health` for a
read-only view that evaluates invocation age; cron activity does not establish
publication success. `--health` remains available while the external drive is
missing. Dry-run writes nothing.

After three failed attempts with identical reasons and condition fingerprints,
cron returns `unchanged_failure_hold` (exit 75) and records that invocation without
repeating the worker or publisher. Configuration, selected Python implementation,
immutable intent/evidence, database/WAL filesystem state, storage identity or
space-condition changes re-arm **validation**, never bypass it. All failed attempts
remain preserved. `--recover-publication` also respects this hold.

The deployed configuration includes:

```json
{
  "sealed_publication_snapshots": true,
  "attempt_state_dir": "/Users/yutsukioka2/git/un_job_application_helper/private/jobagg-runtime/status",
  "storage_guard": {
    "mount_root": "/Volumes/MacbookAirM2",
    "sentinel_path": "/Volumes/MacbookAirM2/un_job_application_helper/.jobagg-storage.json",
    "sentinel_sha256": "9a48c2aa2bf9e4cf369134d4d1025f35f47791007f2075fc966c0c5a5a5138ac",
    "min_free_bytes": 21474836480
  }
}
```

The mount, sentinel hash and filesystem identity are checked before any external
mkdir, lock creation or subprocess. State, owner, worker database and command
workspace/output/state paths must remain on that volume. The local attempt-state
path must remain outside it. The free-space setting is a static 20 GiB reserve,
not a guarantee about future output size. There is no pruning or fallback onto the
internal drive. Keep cron's launch script and redirected log internal.

Paths retain their lexical absolute spelling. Historical evidence references can
continue through root's approved parent-directory symlinks after migration;
resolution validation requires the same actual file and original SHA, rather than
rewriting old immutable requests. Resolve the old publication before changing its
configuration, because unresolved intents remain bound to their exact original
configuration and command.

Validation covers subprocess timeouts/cleanup, stale owner refusal, empty versus
nonempty/symlink WALs, three-failure holds and re-arming, real temporary SQLite
backup recovery after mutable data changes, missing drive before directory
creation, sentinel identity, free-space reserve, alias migration, and health
ordering. All tests use temporary fixtures; no production requests or databases.

For the standard `-m jobagg.publish_worker` command, recovery first pins the exact
existing generation in immutable `publication_generation_binding.json`, before
starting a child. Its gate, plan, source scope, original request time window,
worker snapshot and registry must match. A missing gate, changed plan, foreign
generation or already-complete gate without a prior binding holds for review.
With a previously saved binding, a matching completed gate reconciles its original
result instead of creating another generation. Every recovery request and receipt
must echo that binding; the original request/intent/outcome remain unchanged.
Custom publishers retain responsibility for their own idempotent journal.

Storage is checked again before worker startup, before sealed backup creation,
and before publisher or recovery startup. Guarded runtime parents must already
exist. A disconnection detected at the next phase stops that phase and leaves
its evidence for review. These phase checks do not claim that physical removal
during an active filesystem write is harmless. A latest failed preflight clears
current readiness while preserving the historical last successful publication.
The owner check verifies the inherited descriptor itself, not only that some
process holds a lock on the same inode.
