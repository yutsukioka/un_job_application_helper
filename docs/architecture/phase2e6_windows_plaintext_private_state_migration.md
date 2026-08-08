# Phase 2E-6 Windows Plaintext Private-State Migration

## 1. Purpose

Phase 2E-6 moves Windows saved searches and tracker records from reviewed
plaintext authorities into the existing encrypted AtlasVault runtime through an
explicit, rollback-capable transaction.

## 2. Scope

The phase adds Windows migration primitives, orchestration, presentation, cache
cleanup, selected-vault state, and recovery tests. The independent backend
prerequisite was merged first through PR #95; this phase adds no backend file,
import/export transport, cloud behavior, Apple code, or Android platform
change.

## 3. Package A Durable-Cache Prerequisite

PR #92 is the prerequisite authority. It establishes the durable desktop cache,
retains the prior temporary cache as rollback input, and provides the shared
mutation lock, replacement recovery, retirement marker, and public-cache clear
semantics reused here.

## 4. Phase 2E-5 Baseline

Phase 2E-5 provides current-user DPAPI key protection, encrypted local-store
persistence, explicit Windows activation, compatibility suppression while
active, and the public-only cache guard. It intentionally provides no plaintext
migration or selected-vault marker.

## 5. Windows Plaintext Authorities

Inventory combines controller memory, the logical desktop-cache authority,
compatibility saved searches, and compatibility tracker records. The desktop
cache internally contains durable and retained-legacy physical copies.

## 6. Durable Cache

The durable application-support cache is read without retention expiry during
inventory. Finalization removes only saved searches and tracker records while
preserving all validated public fields.

## 7. Retained Legacy Cache

The retained temporary cache remains read-only before finalization. It is
retired and deleted only after encrypted read-back and final confirmation.

## 8. Compatibility Saved-Search Authority

Compatibility saved searches are inventoried and bound by canonical content.
PR #95 adds a shared server mutation lock and an atomic conditional command:
the server locks, reads, compares the complete expected record, deletes, writes
atomically, and unlocks as one transaction. Phase 2E-6 uses that command in
deterministic name order and verifies absence afterward. It never uses the
legacy identifier-only delete route for Windows migration.

## 9. Compatibility Tracker Authority

Tracker records are bound by job key, record ID, and canonical content. The
PR #95 tracker command performs the same lock/read/compare/delete/atomic-write
transaction over the complete expected application record. Exact record IDs
remain lookup handles, but changed or unknown rows fail closed and remain on
the server.

## 10. No-Dual-Authority Rule

Every Windows legacy private operation and every migration transaction enters
the durable cache's target-adjacent OS mutation lock. Under that lock a legacy
operation reads the protected journal and selected-vault state, rejects either
authority, and otherwise keeps the lock through its endpoint or private-cache
operation. Migration keeps the same lock from final destructive inventory
through selected-vault read-back and activation. Selection is created only
after every plaintext authority is absent, preventing silent plaintext and
encrypted co-ownership across already-running processes.

Nested cache work in the same admitted async transaction uses a scoped Zone
lease to avoid self-deadlock. The lease is path-bound, active only for the
outer call, and invalidated before unlock; unrelated calls and other processes
still acquire the OS lock. Cross-process contenders use Dart's blocking
exclusive file-lock mode, so Windows lock contention waits for authority state
to become observable instead of exposing a lock violation or local path.
Admission path resolution itself does not import a legacy cache, so no
plaintext write can occur before journal and selection checks.

## 11. Two Confirmations

`Prepare Encrypted Migration` creates and verifies an encrypted copy without
deletion. `Remove Plaintext & Activate AtlasVault` starts irreversible cleanup
only after a separate confirmation.

## 12. Point of No Return

The CAS transition to `commit_in_progress` is the point of no return. It occurs
only after re-inventory agrees with the reviewed digest and prepared encrypted
resources verify. Finalization acquires the cross-process admission lock before
that re-inventory and does not release it between plaintext-absence proof and
selected-vault creation/read-back.

## 13. Rollback Boundary

`prepared` and `encrypted_verified` permit discard. Rollback acquires the same
cross-process admission lock, verifies resource digests, removes the exact
staged store and key, preserves every plaintext source, and clears and verifies
the protected journal last. Legacy admission can reopen only after that lock is
released.

## 14. Resume-Only Boundary

At and after `commit_in_progress`, rollback is rejected. Interruption recovery
continues forward and never recreates deleted plaintext.

## 15. Inventory and Deduplication

Saved searches deduplicate by name and complete canonical value. Tracker rows
deduplicate by job key and complete canonical value while retaining remote
record IDs as deletion handles.

## 16. Conflict Detection

Substantive divergence, incompatible nonempty tracker IDs, malformed caches,
authority changes, and unavailable compatibility reads fail before journal or
encrypted resource creation.

## 17. Combined Cache Digest

The logical cache digest canonically binds the durable and legacy private
digests. Each physical digest is also retained in the Windows journal so a
partially cleaned cache can be validated during resume.

## 18. Windows Journal Profile

Windows journals require format `atlasvault-windows-plaintext-migration` and
the Windows cache digest fields. Android-format bytes are rejected by a Windows
coordinator.

## 19. Android Journal Backward Compatibility

The Android default remains `atlasvault-android-plaintext-migration`; its field
set, canonical bytes, decoding, and stage behavior are unchanged.

## 20. DPAPI-Protected Journal

The journal is protected by the Phase 2E-5 current-user DPAPI boundary with
`CRYPTPROTECT_UI_FORBIDDEN`, purpose-bound entropy, digest verification,
create-only/CAS/delete semantics, fixed errors, and no path disclosure.

## 21. Protected-Blob Envelope

Windows local protected blobs use strict `AVWBLB01` framing, exact version and
purpose, bounded lengths, SHA-256 plaintext verification, no trailing bytes,
and best-effort buffer wiping.

## 22. Journal Stages

The monotonic stages are `prepared`, `encrypted_verified`,
`commit_in_progress`, `plaintext_removed`, `selection_committed`, and
`completion_pending`. Backward transitions are rejected.

## 23. Windows Selected-Vault Marker

The selected-vault payload is a strict canonical object protected by DPAPI
under its own purpose. Creation is non-overwriting, reads are strict, and clear
requires the expected vault ID.

## 24. Encrypted Staging

Preparation creates a fresh vault ID, 32-byte key, encrypted saved-search and
saved-job records, and canonical local store through the reviewed generic
runtime and Windows adapters.

## 25. Store Read-Back Verification

The staged store is read back, decrypted, hydrated, and compared with the full
inventory before the journal reaches `encrypted_verified`.

## 26. Pre-Commit Rollback

Rollback verifies that no deletion or selection occurred, validates the staged
key and store hashes, removes only those resources, restores reviewed legacy
state, and deletes the journal last.

## 27. Compatibility Saved-Search Deletion

Each expected name is re-read and compared with its journaled value, then sent
as the complete `expected` body to
`POST /api/saved-searches/{opaque-id}/conditional-delete`. Exact match returns
`deleted`; prior deletion returns `absent`; a changed record returns HTTP 412.
Only verified absence is CAS-journaled. A 412, unknown name, or new row after a
successful delete forces recovery before selection and preserves the changed
content. The Windows protected journal preserves every validated compatibility
timestamp string exactly, including fractional seconds and explicit UTC
offsets, so the request precondition matches the stored server row rather than
the normalized encrypted-payload timestamp. Android journal encoding retains
its existing normalized whole-second behavior. API and Dart errors are fixed
and private-free.

## 28. Compatibility Tracker Deletion

Each complete expected tracker record is sent to
`POST /api/tracker/{opaque-id}/conditional-delete` after a bound re-read. The
same `deleted`, `absent`, and HTTP 412 rules apply. Record ID, job key, status,
notes, applied timestamp, and updated timestamp are compared atomically;
identifier-only migration deletion is absent and ABA changes fail closed.

## 29. Durable-Cache Cleanup

The durable cache is strictly decoded and digest-checked, then rewritten with
empty private lists through the Package A replacement protocol. Public fields
are compared before and after the rewrite.

## 30. Legacy Retirement Marker

The Package A retirement marker is created and verified before legacy-file
deletion, preventing a later temporary-cache re-import.

## 31. Legacy Cache Deletion

Only the exact retained legacy cache file is deleted. Its expected private
digest is checked before deletion, and the durable public cache remains.

## 32. Cache Cleanup Intent

The adjacent canonical intent contains only the combined and physical digests
plus three progress Booleans. It contains no record, query, note, vault ID,
migration ID, path, or service URL.

## 33. Cleanup Interruption Recovery

Intent writes are flushed and recoverable under the Package A process and OS
locks. Resume adopts an acknowledged durable scrub, retirement marker, or
legacy deletion, verifies final absence, and deletes the intent last.

## 34. Plaintext-Absence Verification

Before selection, compatibility lists, controller memory, durable private
lists, retained legacy file, and cleanup intent must all be absent; the
retirement marker and prepared encrypted hashes must verify. This proof and
selected-vault creation are one admitted cross-process transaction, so another
process cannot repopulate compatibility or cache plaintext in between.

## 35. Selected-Vault Commit Point

Selected-vault creation occurs only after `plaintext_removed`. Create-only and
read-back checks make selection the encrypted-authority commit point.

## 36. Runtime Activation

The coordinator explicitly activates the existing generic private-state
runtime, reads its snapshot, and compares migrated saved searches and tracker
records with the journal.

## 37. Journal-Clear-Last

The protected journal is deleted only after selection, activation, encrypted
verification, compatibility absence, and cache cleanup all verify.

## 38. Completion-Pending Behavior

A failed journal deletion leaves `completion_pending`. Encrypted selection
remains authoritative and resume retries verification and journal deletion.

## 39. Authority Bootstrap

Startup inspects only journal and selected-vault presence. It does not
inventory, call compatibility endpoints, load the DPAPI key, read the encrypted
store, migrate, or activate automatically. An already-running legacy process
does not rely on stale in-memory authority: every subsequent private read,
write, cache load, cache import, or clear re-enters the OS lock and rechecks
journal and selection.

## 40. Selected-Inactive Behavior

With selection and no journal, compatibility private operations remain blocked
across processes and the UI offers explicit encrypted activation without
showing the vault ID. Clearing the journal therefore cannot reopen legacy
authority after commitment.

## 41. Explicit Later Activation

`Activate Encrypted Private Data` reads the selected marker and activates the
existing encrypted vault only after a user action. Migration preparation,
rollback, finalization, and every resume operation use the shared transaction
admission; neither confirmation is awaited while holding the lock.

## 42. Migration Presentation

The Windows panel shows fixed stages, source-presence indicators, counts, and
DPAPI/rollback warnings. Names, queries, notes, job keys, IDs, paths, and vault
identity remain excluded.

## 43. Secret Lifetime

Vault keys, decrypted payloads, protected-journal plaintext, and temporary
canonical secret buffers are wiped best-effort. Dart and dependency copying
prevent a universal zeroization guarantee; errors and logs contain no secrets.

## 44. Public-Cache Preservation

The durable cache retains validated search results and operational data while
private lists are scrubbed. Active encrypted mode continues to write only
`withoutPrivateState()` snapshots under the hard plaintext guard.

## 45. Public Search Continuity

Public search and public endpoints remain available while migration authority
is pending or selected-inactive. Only legacy private operations are blocked.

## 46. No Automatic Migration

Construction and startup perform no inventory, preparation, cleanup, or
activation. Every state-changing operation is an explicit panel command.

## 47. No Windows Import/Export

Windows document transport, recovery-wrap export/import, file pickers, and
installation of imported vaults remain outside this phase.

## 48. No Cloud Sync

The phase adds no device identity, pairing, backend ciphertext route, patch
exchange, revocation, or key rotation.

## 49. TDD Checkpoint A

Checkpoint A was committed before Checkpoint B. It proves Windows journal and
selection primitives, profile strictness, all-source inventory, encrypted
staging/read-back, and pre-commit rollback.

## 50. TDD Checkpoint B

Checkpoint B red commit `19b4ca19` proves missing Windows assembly, warning,
and two-cache cleanup. Its implementation adds finalization, cleanup recovery,
selection, activation, later activation, and Windows integration coverage.

## 51. Windows Integration Evidence

The happy-path harness exercises real DPAPI preparation, rollback,
finalization, cache retirement, encrypted activation, and explicit
reactivation. The recovery harness supports separate processes for journal
blocking of an already-running legacy process, rollback reopening, finalization
exclusion through selection, and OS lock release after forced process
termination, in addition to `completion_pending` prepare/verify stages. Fixed
handshake files coordinate stages without timing-only races. A deterministic
Windows contender verifies that blocking exclusive lock acquisition waits for
finalization and then observes the selected-vault fence. Dart tests also inject
saved-search and tracker changes after review and prove HTTP-412-style
precondition failure preserves each changed record and creates no selection.

## 52. Verification

Verification requires Dart formatting, Flutter analysis and full tests,
macOS/Android builds, Windows Debug/Release builds, both Windows integration
stages, prior-phase regressions, Python/Swift vectors, source guards, exact
scope, protected-path checks, and artifact checks.

## 53. Go/No-Go

- PR #92 durable cache: merged.
- Windows four-authority inventory: implemented.
- Durable and legacy cache inventory: implemented.
- Conflict detection: implemented.
- DPAPI-protected migration journal: implemented.
- Platform-specific journal profile: implemented.
- Android journal compatibility: preserved.
- Encrypted staging: implemented.
- Pre-commit rollback: implemented.
- Compatibility cleanup: implemented.
- Durable-cache private scrub: implemented.
- Legacy-cache retirement/deletion: implemented.
- Cleanup interruption recovery: implemented.
- Selected-vault commitment: implemented.
- Explicit later activation: implemented.
- Silent dual authority: prevented.
- Backend atomic conditional deletion: implemented.
- Changed-content deletion: prevented.
- Cross-process journal admission: implemented.
- Cross-process compatibility-write admission: implemented.
- Cross-process cache-write admission: implemented.
- Plaintext absence through selection: one admitted transaction.
- Process crash recovery: implemented.
- Identifier-only migration deletion: absent.
- Automatic migration: not implemented.
- Windows import/export: not implemented.
- Cloud sync: not implemented.
- Production cross-platform privacy readiness: not claimed.

## 54. Deferred Work

Windows encrypted export/import, linked-device identities, pairing,
ciphertext synchronization, device revocation, and key rotation remain
separately gated.

## 55. Next Product Gate

Phase 2E-7 is the separately authorized Windows encrypted export/import gate.
This phase does not create its branch, files, or implementation.
