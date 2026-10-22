# Immutable artifact compression

`jobagg.storage_compression` is a standalone storage foundation. It resolves exact
legacy bytes, versioned compressed representations, and existing dispatcher cold
archive entries without changing logical paths or hashes. Publication, capture
and retention readers still use their existing paths; they have not been migrated
to this API. Do not retire their originals until their readers and dependency
checks support the new manifest. No automatic compression or migration is enabled.

Gzip uses the standard library. Install `packages/jobagg[compression]` for the
optional Zstandard runtime. `auto` prefers Zstandard level 3 when installed and
otherwise uses gzip level 6; choose an explicit codec for reproducible deployment
configuration. An explicitly requested unavailable codec fails. Small artifacts
and objects without measured savings remain uncompressed.

From the package directory, a read-only preview uses temporary scratch outside
the store and verifies an exact compression/decompression round trip:

```sh
python -m jobagg.storage_compression preview \
  --root /absolute/store --path history/plan.json --codec gzip --max-seconds 30
```

An install repeats source checks, verifies physical and reconstructed hashes,
syncs the object, and commits an additive manifest and durable journal under
`.jobagg-storage/`. Execution requires the **existing actual shared owner lock**
used by the worker and dispatcher. The separate manifest lock serializes storage
writers. Select only completed immutable artifacts under the applicable
maintenance/publication gates; this API does not discover protected dependencies.

```sh
python -m jobagg.storage_compression compress \
  --root /absolute/store --path history/plan.json --codec gzip \
  --shared-lock /absolute/store/remediation/manual-fetch-owner.lock --execute
```

This retains the original. Retirement additionally requires `--retire-legacy`,
`--readers-support-manifest` and `--retirement-approved`. These are caller
assertions of reviewed reader compatibility and dependency eligibility, not an
automated dependency inventory. A durable intent precedes retirement. Keep the
manifest, objects and journal together. Rollback to old readers requires restoring
retired artifacts at their original paths with `copy` before switching readers.

After interruption, `reconcile --root ... --shared-lock ...` verifies both
physical and reconstructed content before completing open intents. Changed
originals remain held. Invalid journals or corrupt representations stop recovery;
retain them for review. Reconciliation is a mutation and requires the same owner
lock. Dictionary compression requires retaining the exact dictionary separately;
provide its SHA-256 and bytes/file when writing and resolving, and
`--dictionary-file` when reconciling.

SQLite compression requires `--sealed-sqlite` and rejects WAL, SHM and rollback
journal sidecars. This assertion does not create a consistent backup of a live
database. `restore-sqlite` reserves declared output size plus headroom, restores
into private scratch, verifies the byte hash and SQLite integrity, and opens the
copy read-only and immutable. Scratch is removed unless `--keep` is selected;
API callers must close connections and clean up the returned snapshot.

Byte limits and cooperative time checks bound processing stages. They do not
interrupt blocking filesystem operations or SQLite integrity checks, and are not
a strict end-to-end wall-clock guarantee. Tests use synthetic artifacts; no live
migration, deployed fingerprint reseal, or provider request is part of this change.
