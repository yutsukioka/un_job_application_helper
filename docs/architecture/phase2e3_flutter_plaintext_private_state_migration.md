# Phase 2E-3 Flutter Plaintext Private-State Migration

## 1. Purpose

Phase 2E-3 provides an explicit, interruption-safe migration of legacy Flutter
saved searches and tracker records into the Android AtlasVault authority.

## 2. Scope

The phase migrates the existing Flutter private record families only. It does
not add export/import interoperability, Windows storage, cloud sync, recovery
UI, automatic migration, or a new backend route.

## 3. Phase 2E-2 Baseline

Phase 2E-2 supplies API-24 Android Keystore protection, no-backup local-store
files, create-only key and store operations, SHA-256 compare-and-swap writes,
an explicit private runtime, compatibility endpoint suppression, and a
plaintext-cache write guard.

## 4. Existing Plaintext Authorities

Legacy private values may exist in controller memory, the local cache,
compatibility saved-search state, and compatibility tracker state. Migration
inventories all four authorities before creating any resource.

## 5. Existing Encrypted Authority

The AtlasVault private runtime reads a selected encrypted local store and its
device-bound Android vault key. It remains inactive until an explicit user
operation succeeds.

## 6. No-Dual-Authority Rule

Legacy writes are allowed only before preparation. Once the migration journal
exists, private cache writes and ordinary compatibility private operations are
blocked. After completion, only the encrypted runtime supplies private state.

## 7. User-Confirmation Model

Inventory is explicit. Preparation and finalization use two separate dialogs,
and neither operation runs from construction, bootstrap, widget appearance, or
an authority-state change.

## 8. Preparation Confirmation

`Prepare Encrypted Migration` creates and verifies an encrypted copy while all
plaintext authorities remain unchanged. The prepared resources may be
discarded before finalization.

## 9. Finalization Confirmation

`Remove Plaintext & Activate AtlasVault` states that verification completed,
plaintext deletion is about to start, rollback then becomes unavailable,
interruption remains resumable, Android protection is device-local, and
Flutter interoperability and Windows secure storage remain absent.

## 10. Point of No Return

The journal compare-and-swap transition from `encrypted_verified` to
`commit_in_progress` is the point of no return. The owner blocks ordinary
private operations before requesting that transition.

## 11. Rollback Boundary

Rollback is accepted only for `prepared` and `encrypted_verified`. It verifies
that plaintext and selection are unchanged and verifies staged hashes. The
journal persists rollback intent before store deletion and persists verified
store absence before key deletion. Retries adopt an already-absent exact
resource and never recreate a store or key that the user chose to discard.
The journal is cleared last.

## 12. Resume-Only Boundary

At `commit_in_progress` or later, discard fails fixed. Resume revalidates the
journal and resources and continues only unfinished idempotent steps.

While migration is preparing, prepared, finalizing, resumable, completion
pending, or unresolved, ordinary public operations may continue but their
persisted-cache writes are suppressed. They never reuse the post-activation
public-only cache path, so the reviewed plaintext cache bytes and digest remain
unchanged until explicit rollback or verified removal. Explicit cache clearing
is suppressed by the same boundary, and migration-specific reads drain any
cache write admitted before preparation before hashing authoritative state.

## 13. In-Memory Inventory

The coordinator reads copies of the controller saved-search and tracker lists.
Inventory does not clear or mutate controller state.

## 14. Persisted-Cache Inventory

The migration-specific cache reader strictly decodes the full schema and reads
private lists directly. Corrupt, incomplete, or ambiguous cache content fails
closed.

## 15. Expired-Cache Migration Read

Migration inspection ignores the normal seven-day restoration age while
retaining strict schema and value validation. An absent cache is represented
separately from an existing public-only cache.

## 16. Compatibility Saved-Search Inventory

The migration-only adapter explicitly reads the complete compatibility
saved-search list. Ordinary controller reads remain blocked while migration is
pending.

## 17. Compatibility Tracker Inventory

The migration-only adapter explicitly reads the complete compatibility
tracker list and records the remote ID-to-job-key deletion handles internally.

## 18. Deterministic Deduplication

Saved searches are keyed and sorted by name. Tracker records are keyed and
sorted by job key. Canonically identical copies merge without changing array
or filter semantics.

## 19. Conflict Detection

Different values for one logical key, duplicate remote handles, malformed
records, an unknown remote record after commit, or a changed expected record
halts migration without deleting the conflicting value.

## 20. Inventory Digest

Canonical JSON for the merged saved-search and tracker arrays is hashed with
SHA-256. Preparation and finalization require the current inventory to match
the reviewed digest.

## 21. Protected Migration Journal

The journal is stored by the Android protected-journal boundary in no-backup
storage. Dart handles canonical journal plaintext only in temporary memory.

## 22. Journal Master-Key Encryption

Native Android storage encrypts the journal with the non-exportable Keystore
master key and purpose-bound AES-GCM associated data before disk persistence.

## 23. Journal Schema

The strict version-1 journal records non-semantic transaction IDs, canonical
inventory, remote deletion handles, resource hashes, progress lists, cache
state, selection progress, and monotonic rollback intent/store-removal flags.
Unknown or inconsistent fields fail closed.

## 24. Journal Stages

Stages are `prepared`, `encrypted_verified`, `commit_in_progress`,
`plaintext_removed`, `selection_committed`, and `completion_pending`.
Transitions are forward-only and at most one stage at a time.

## 25. Selected-Vault Registry

The Android selected-vault marker is create-only, protected by the same native
storage boundary, and read back after creation. Existing unrelated selection
is never overwritten.

## 26. Vault-Key Generation

Preparation generates a non-semantic vault ID, store ID, and 32-byte key using
reviewed secure randomness. The journal stores only the key digest.

## 27. Encrypted-Store Construction

Each migrated logical record is converted to the reviewed AtlasVault payload,
sealed with record-specific HKDF and AES-256-GCM, and collected in deterministic
order in a new local-store envelope.

## 28. Record Mapping

Saved searches map to `saved_search` payloads. Tracker/application rows map to
`saved_job` payloads while preserving status, notes, dates, and job identity.

## 29. Store Read-Back Verification

After create, the store and key are read back. Every record is authenticated,
decrypted in temporary memory, projected, and compared with the canonical
inventory before the journal enters `encrypted_verified`.

## 30. Pre-Commit Rollback

Prepared rollback removes only hash-verified staged resources. It performs no
compatibility delete, no cache rewrite, no selection operation, and no private
runtime activation. A process interruption after store or key deletion resumes
the persisted rollback operation; generic preparation resume cannot reconstruct
discarded resources.

## 31. Compatibility Saved-Search Deletion

Expected names are processed in sorted order. Each delete is followed by a
read-back and one journal compare-and-swap progress update. An already-absent
expected name is adopted once.

## 32. Compatibility Tracker Deletion

Expected tracker handles are processed in deterministic job-key order. Record
ID, job key, and canonical content are revalidated before deletion and after
read-back.

## 33. Unexpected Remote Mutation Behavior

Unknown, changed, or conflicting remote records are preserved. The coordinator
stops with a fixed migration failure and retains the journal for reviewed
recovery; it never broadens the deletion set.

## 34. Local-Cache Private Removal

The cache rewrite requires the recorded private digest, replaces both private
lists with empty arrays, preserves all public fields, writes atomically, and
reads the existing cache back. Unexpected cache disappearance fails closed.

## 35. Public-Cache Preservation

Search criteria, public results, cached jobs and details, health, update runs,
sources, timestamps, and server configuration survive the private-only cache
rewrite.

## 36. Plaintext Absence Verification

Before selection, compatibility lists, cache private lists, and controller
legacy lists must all be empty. The key and encrypted store hashes and the
decrypted inventory are verified again.

## 37. Selection Commit Point

Selection is created only after the journal reaches `plaintext_removed`.
Creation is followed by exact read-back, then a journal transition to
`selection_committed`. If create succeeds but its response or journal update is
lost, bootstrap treats only the exact journal vault ID as resumable; unrelated
selection remains a fixed recovery failure.

## 38. Runtime Activation

The existing explicit controller activation opens the selected store, installs
its committed snapshot, and leaves compatibility endpoints fenced. The
activated inventory must match the journal.

## 39. Journal-Clear-Last Completion

Selection, store, plaintext absence, and active runtime state are reverified.
The exact journal is then deleted by digest and absence is read back. If delete
reports failure but read-back proves absence, completion succeeds; an exact
remaining journal is completion-pending, while mismatched state fails closed.
No other resource is removed during completion.

## 40. Completion-Pending Behavior

If journal deletion is uncertain, encrypted authority and selection remain.
The owner publishes `completionPending`, blocks legacy operations, and offers
only explicit Resume. Any other post-commit finalization failure is classified
through authoritative journal/selection state and exposes Resume rather than
an actionless recovery state. Explicit Resume failures use the same
authoritative classification: pending work remains resumable, while a
completed but unacknowledged journal deletion publishes active authority.

## 41. Interruption Recovery

Resume adopts only exact expected resources and persisted progress. It handles
key, store, deletion, cache, selection, activation, and journal-clear
interruptions without repeating completed destructive operations. Rollback
resume separately adopts acknowledged staged-store and staged-key deletion and
returns the owner to legacy authority after clearing the journal.

## 42. Authority Bootstrap

Bootstrap reads only journal and selected-vault state. It runs before normal
cache hydration on the production Android shell and starts no migration,
compatibility request, secure-key load, store read, or runtime activation.

## 43. Selected-Inactive Behavior

Selection without a journal and without an active runtime publishes
`encryptedSelectedInactive`. Private cache fields and compatibility private
operations remain blocked while public search stays available.

## 44. Explicit Activation After Restart

The user must choose `Activate Encrypted Private Data`. Only that action reads
the device key and store and installs private state. Activation is never
automatic.

## 45. Migration Presentation Owner

The retained, serialized owner exposes counts, source-presence Booleans, fixed
status, and fixed stage only. It stores no names, queries, notes, record IDs,
vault IDs, paths, journal bytes, or keys. Late operations are revision-fenced.

## 46. Migration UI

The Settings panel offers Review, Prepare, Discard, Remove and Activate,
Resume, and Activate Encrypted Private Data only in valid states. The two
destructive boundaries use separate explicit dialogs.

## 47. Secret and Private-Data Lifetime

Keys, decrypted payloads, and canonical private bytes use mutable buffers and
are wiped where practical. Dart and library copies prevent a universal
zeroization guarantee. Errors and descriptions remain fixed and redacted.

## 48. No Automatic Migration

Construction and bootstrap perform no inventory or migration operation.
Preparation, finalization, resume, discard, and activation each require a
specific user action.

## 49. No Backend Route Change

The phase reuses the reviewed compatibility list and delete methods. No Python
route, API schema, or service implementation changes.

## 50. No iOS Interoperability

Flutter encrypted export/import and iOS-to-Flutter exchange remain deferred.
Migration creates a device-local Android vault only.

## 51. No Windows Storage

Windows DPAPI or Credential Manager integration is absent. The migration panel
states this limitation before finalization.

## 52. TDD Checkpoint A Evidence

`Test Flutter plaintext AtlasVault migration core` established missing format,
inventory, journal, storage, staging, verification, and rollback behavior.
`Add Flutter AtlasVault migration staging and rollback` made those suites and
the Android preparation/rollback journey green before Checkpoint B began.

## 53. TDD Checkpoint B Evidence

`Test Flutter plaintext AtlasVault migration journey` established missing
owner, confirmations, authority bootstrap, finalization, resume, selection,
and activation behavior. The implementation adds deterministic interruption,
cache-disappearance, retained-operation, restart-activation, and fixed
asynchronous error-redaction regressions. Exact-head Codex review added
regressions for post-commit Resume availability, interrupted exact selection,
journal-delete read-back, persisted pre-commit rollback progress, and
authoritative reclassification after a failed explicit Resume.

## 54. Android Integration Evidence

The real-device tests use Android Keystore, protected journal storage,
selected-vault storage, encrypted local-store I/O, preparation and rollback,
finalization, and explicit relaunch activation with fake private values. The
recovery suite interrupts journal, key, store, encrypted-verification,
saved-search deletion, tracker deletion, cache cleanup, selection, runtime
activation, and journal-clear boundaries; it resumes each boundary and proves
changed or unknown remote records are preserved and fail closed.

## 55. Verification

Required gates are Dart formatting, Flutter analysis, focused and full Flutter
tests, Android APK and lint, both Android migration integrations, Phase 2E-1
vectors, Phase 2E-2 regressions, Python and Swift vectors, source guards, exact
scope, protected-path checks, and artifact scans.

## 56. Go/No-Go

- Explicit migration inventory: implemented.
- Conflict detection: implemented.
- Encrypted migration journal: implemented.
- Encrypted staging: implemented.
- Cryptographic read-back: implemented.
- Pre-commit rollback: implemented.
- Compatibility saved-search cleanup: implemented.
- Compatibility tracker cleanup: implemented.
- Local-cache private removal: implemented.
- Selected-vault commit: implemented.
- Interruption resume: implemented.
- Explicit selected-vault activation: implemented.
- Silent dual authority: prevented.
- Automatic migration: not implemented.
- iOS-Flutter exchange: not implemented.
- Windows secure storage: not implemented.
- Cloud sync: not implemented.
- Production cross-platform privacy readiness: not claimed.

## 57. Deferred Work

Recovery-key import/export UI, bidirectional iOS-Flutter exchange, Windows
secure storage, linked devices, cloud sync, key rotation, and any migration of
unknown or conflicting records remain separately gated.

## 58. Next Product Gate

Phase 2E-4 must implement bidirectional iOS-Flutter encrypted export/import
using the shared envelope and recovery-wrap v2, with explicit recovery-key and
existing-vault conflict decisions and no plaintext intermediary.
