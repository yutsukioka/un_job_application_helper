# PR #143 explicit historical representation implementation

Prepared 24 October 2026 (Africa/Nairobi). This implements the additive portion of
[the approved archival proposal](pr143-storage-reader-migration-proposal-2026-10-24.md).
It is preparation for deployment. Production remains fenced, M0 is preserved and
readiness remains false. No payload is copied from the HDD.

## Reader and provenance contract

`historical_storage` loads an explicit grant using its independently pinned
SHA-256. A grant binds one canonical logical path and original producer hash,
length and pre-archive physical identity to an immutable per-artifact snapshot of
the existing representation manifest. The physical object must be Zstandard
level 3 without a dictionary. Later additions to the general manifest cannot
silently change an earlier grant. Existing originals must retain their original
identity; a changed original does not become a successful compressed fallback.
All reconstructed bytes, including JSON fields omitted from a projection, pass
through the existing logical hash and length verifier. Missing codecs, changed
controls or object identities, aliases, partial consumption and expired deadlines
fail closed. The bound is 1 GiB per artifact and 16 MiB per grant/control.

`historical_plans.read_plan(..., archive=grant)` preserves the original strict
UTF-8/JSON selector, duplicate-key rejection, null/absent semantics and retained
memory limit. Its record identifies the old physical stamp as provenance; it does
not pretend that stamp describes a new uncompressed file. Occurrence build and
read APIs take an explicit mapping of canonical plan paths to grants. They retain
original plan/result hashes, destination mapping and commit-journal checks. The
legacy file path remains the default. There is no implicit startup scan or global
path substitution.

A catalogue attestation requires the actual owner, the original verified
(non-null) dependency hash and identity, and an idle native authority when one is
active. The caller must use the existing authority controller transaction. The
attestation is a separate entry in the protected review ledger, pinned to the
catalogue epoch, inventory, database and owner. It invalidates containment review.
It does not edit an original dependency, historical proof or held disposition.
`runtime_control_catalog.read_archived_dependency` is an explicit offline reader;
ordinary reconciliation is not allowed to manufacture a historical successful
exit from surviving representation bytes.

Only originally sealed historical SQLite images may use a grant. Restoration
checks the exact original bytes, available physical reserve, SQLite header and
integrity in private caller-owned scratch before returning an immutable read-only
snapshot. The scratch context removes its own temporary restoration on exit.
Live database/WAL/SHM compression remains prohibited. The existing storage APIs
now carry one absolute deadline through preview, installation, reads and restore;
SQLite integrity checking also receives a progress handler. Cooperative checks
cannot preempt a blocked kernel filesystem operation; outer supervision remains
necessary.

## Additive operator pages

`archive_migration.prepare` requires explicit original producer bindings and a
pinned dependency/reader review. This is an offline operation, not runtime
admission. Its review requires a complete writer inventory and exact operation
fence hash; those assertions must be backed by a real review before using any
production inventory. The compiler checks the actual owner, unchanged config,
complete publication gate, generation chronology and dispatcher recovery bindings.
It protects current/previous, unfinished and referenced recovery generations, plus
explicit additional protected paths. Unknown snapshot seals, unverified export
backups and artifacts without original producer proof remain ineligible.

The immutable review admits at most 64 artifacts and 8 GiB; an execution page
defaults to four items / 2 GiB and must fit a finite deadline no greater than 900
seconds. Preview scratch must be registered under that operation's root. Each
item reserves 20 GiB physical headroom plus a conservative coexistence allowance,
checks original and producer identities, records an exclusive durable intent,
installs and fully verifies the representation, seals its grant and checkpoints.
On restart, changed fences/configuration/protection/implementation/producer inputs
are rejected. Completed and installed grants are verified again. The actual owner
is borrowed without being closed or unlocked. An unknown manifest commit retains
the object and open intent, including when the post-error manifest reread fails.

Every original is retained. The executor has no source-removal option, grants zero
removed bytes/headroom and always returns `production_readiness_granted: false`.
An interrupted page can leave additive evidence for the same intent to reconcile;
it cannot erase useful source data or turn uncertain history into acceptance.
The existing general compression retirement switches are not used by this
executor and are not a deployment procedure.

Automatic approval review rejected the initial retirement-capable executor as
outside the retained authorization. The user subsequently authorized [guarded removal in isolated copies only](pr143-isolated-archive-removal-2026-10-24.md). That separate operation is now implemented; the additive executor remains unchanged and production retirement is not authorized.

## Validation and remaining deployment gates

The regression fixtures exercise legacy, dual and compressed-only reads; Unicode
and skipped duplicate JSON; corruption, alias, bound, partial read and deadline
failures; SQLite scratch cleanup; the real historical occurrence/destination
mapping; protected native catalogue holds; crash recovery at install/grant/final
checkpoint; changed operator boundaries; and interrupted publication with an older
compressed-only plan. Current and previous publication inputs stay hot in that
publication fixture. No provider requests or production authority changes are
involved. CI installs the optional compression dependency to run these cases.

The final validation receipt records the actual full-suite results and full-module
seal. These tests do not resolve the installed 100 GB category/total ceiling, the
complete active storage registry, approved measured runtime settings, compatible
source handoff/rollback, or the final admitted-cycle rehearsal on the exact
migrated layout. An additive rehearsal cannot establish freed capacity. Preserve
the accepted native rehearsal batch and its configuration; do not reset it to
idle to make a new candidate appear ready.

R1/R3: source and vacancy classification, enumeration and text completeness are
unchanged; representation survival certifies none of them. R2/R4: holds, original
proofs, owner/fence and recovery inputs remain intact. R5: one cooperative clock,
finite bytes/pages and reserve checks apply. R6: explicit full-module reseal and
an isolated immutable candidate are required. R7/R8: report tested behavior and
remaining gates separately; no claim of production activation or storage
compliance follows from passing archive tests.
