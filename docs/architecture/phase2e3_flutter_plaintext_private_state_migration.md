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
is suppressed by the same boundary and rechecks the gate immediately before
deletion. Cache writes and clears are one retained mutation stream, and
compatibility saved-search and tracker writes are a second retained stream.
The production coordinator drains both streams before reading any in-memory,
cache, or compatibility value. An already-admitted clear includes its disk and
in-memory clearing before the drain completes. Whether that clear removes
legacy controller private state is captured when the clear is admitted, so a
later `preparing` state cannot preserve controller-only records after the disk
clear. A clear or compatibility write that has not yet been admitted is
rejected once preparation publishes its blocking state.

## 13. In-Memory Inventory

The coordinator reads copies of the controller saved-search and tracker lists.
Each nonempty legacy projection carries the normalized compatibility authority
that produced it. Saved-search and tracker projections must agree, and that
authority must match the compatibility source before the coordinator reads or
merges remote rows. A candidate-server connection test therefore cannot leak
its private rows into migration for the currently configured server.
Compatibility mutations reject a mismatched family authority, and public cache
writes omit a mismatched private projection instead of relabeling it.
Inventory does not clear or mutate controller state.

## 14. Persisted-Cache Inventory

The migration-specific cache reader strictly decodes the full schema and reads
private lists directly. It also carries the cache snapshot's normalized API
authority in memory. A cache containing private values must match the current
compatibility authority before any compatibility read, preparation, resume, or
remote deletion. Corrupt, incomplete, ambiguous, or authority-mismatched cache
content fails closed. The raw cache JSON is recursively scanned for duplicate
object keys before `jsonDecode`; decoded-key aliases such as Unicode escapes
are duplicates too. Every public object and row must also survive a lossless
decode and canonical re-encode through its production model. Ambiguous keys,
missing or defaulted nested search, job, detail, health, update-run, or source
fields therefore fail before inventory instead of being rewritten during
private state removal.

## 15. Expired-Cache Migration Read

Migration inspection ignores the normal seven-day restoration age while
retaining strict schema and value validation. An absent cache is represented
separately from an existing public-only cache.

## 16. Compatibility Saved-Search Inventory

The migration-only adapter explicitly reads the complete compatibility
saved-search list through an exact-schema raw-response decoder. A non-list
response, non-object row, missing field, unknown field, or invalid nested
request fails the complete inventory instead of being dropped or defaulted.
The nested decoder matches the compatibility endpoint's complete
`VacancySearchRequest` serialization rather than the narrower AtlasVault
request JSON. Every criterion represented by the reviewed AtlasVault payload
is mapped explicitly. Compatibility-only criteria must equal their current
no-op defaults; a non-default value fails migration instead of being silently
discarded. The endpoint's intentionally absent `include_facets` value maps to
the historical saved-search default of `true`.
Ordinary controller reads remain blocked while migration is pending. Legacy
API and cache timestamps are accepted only as valid, timezone-bearing
ISO-8601 values with offsets bounded to `-14:00...+14:00`, then normalized to
UTC seconds before strict AtlasVault journal and payload validation. This
covers the backend's fractional `+00:00` values without relaxing the encrypted
format.

## 17. Compatibility Tracker Inventory

The migration-only adapter explicitly reads the complete compatibility
tracker list with the same exact raw-response policy and records the remote
ID-to-job-key deletion handles internally. Tracker timestamps use the same
bounded legacy normalization; timezone-free, impossible-calendar, and
out-of-range offset values fail closed.

## 18. Deterministic Deduplication

Saved searches are keyed and sorted by name. Tracker records are keyed and
sorted by job key. Canonically identical copies merge without changing array
or filter semantics.

## 19. Conflict Detection

Different values for one logical key, duplicate remote handles, malformed
records, an unknown remote record after commit, or a changed expected record
halts migration without deleting the conflicting value. Timestamp
normalization happens before cross-source comparison so equivalent backend and
canonical UTC representations deduplicate deterministically.

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
merged inventory, canonical original remote snapshots, remote deletion
handles, the normalized compatibility authority, resource hashes, progress
lists, cache state, selection progress, and monotonic rollback
intent/store-removal flags. The original snapshots remain separate from
complementary optional values merged from memory or cache. Every journal-era
compatibility read is authority-checked before and after the asynchronous call,
and every delete is checked immediately before dispatch. This binding remains
required when the local cache is absent or public-only. Unknown, noncanonical,
or inconsistent fields fail closed.

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
discarded resources. After rollback is read back as complete, the owner remains
in a blocking `restoringLegacy` state while the controller drains admitted
plaintext work and reinstalls the preserved strict cache private snapshot.
Only then may the owner publish legacy authority. A restart therefore does not
present empty saved-search and tracker lists while claiming rollback is ready,
and a cache containing private state requires no compatibility-network
refresh. When no private cache state exists, the controller instead performs
both strict migration-only compatibility reads against the unchanged,
generation-bound authority and installs their results atomically before
publishing legacy readiness. A failed or stale read installs nothing. The
coordinator separately inspects rollback availability from the protected
journal. The owner exposes Discard after an interrupted preparation only when
the journal remains at `prepared` or `encrypted_verified`, no plaintext
deletion or cache clearing is recorded, and no selected vault exists. While
the explicit discard operation is pending, the owner publishes a blocking
`discarding` state and fixed rollback-specific progress text rather than
reusing encrypted-copy preparation presentation. Post-commit journals never
expose Discard. If rollback completes but legacy
projection restoration fails, the owner remains `recoveryRequired`; it does
not reopen compatibility mutations or persisted private-cache writes.

## 31. Compatibility Saved-Search Deletion

Expected names are processed in sorted order. Each delete is followed by a
read-back and one journal compare-and-swap progress update. An already-absent
expected name is adopted once.

## 32. Compatibility Tracker Deletion

Expected tracker handles are processed in deterministic job-key order. Record
ID, job key, and the canonical original remote content are revalidated before
deletion and after read-back. Resume uses the same protected snapshots, not
the richer merged encrypted inventory.

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
returns the owner to legacy authority after clearing the journal and restoring
the preserved local private snapshot. Owner revision, controller authority
generation, runtime activity, and cache authority are rechecked around the
asynchronous read so a hide or superseding transition cannot republish stale
private state.

Owner authority-inspection completions are revision-fenced on both success and
failure. Hiding or disposing the owner while an inspection is suspended leaves
the presentation hidden and prevents a late notification.

Test Connection, Save and Reload, and Local Save Refresh each receive a
normalized source-authority operation token. Every asynchronous health,
search, compatibility, tracker, update, and source read rechecks that token
before publication. A newer operation supersedes older candidate-server work,
so stale private projections cannot overwrite the saved authority.

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

The phase reuses the reviewed compatibility list and delete routes while the
migration path applies stricter response decoding than ordinary compatibility
UI reads. No Python route, API schema, or service implementation changes.

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
authoritative reclassification after a failed explicit Resume. Later
exact-head cycles added pre-commit cache byte preservation, suppression of
explicit clearing after an asynchronous gate crossing, retained clear draining
before inventory, complete controller cleanup for an admitted legacy clear,
draining every admitted compatibility mutation before the first inventory
source read, and normalization of valid legacy backend timestamps before
strict journal and encrypted-payload validation. The final Codex cycles bind
expired-cache private values to their cached compatibility authority, reject
lossy compatibility inventory decoding, and enforce the ISO-8601 maximum UTC
offset before migration side effects. The subsequent exact-head correction
locks the strict compatibility decoder to the backend's complete
`VacancySearchRequest` shape and rejects any non-default criterion that the
reviewed encrypted payload cannot preserve. The final review correction
persists canonical compatibility rows separately from merged encrypted
records, validates finalization and resume against those original snapshots,
and rejects any cached public row or nested object that cannot round-trip
losslessly before migration side effects. The next exact-head cycle binds the
protected journal to the reviewed compatibility authority even without cached
private rows and rejects recursive duplicate JSON keys before any cache
rewrite; deterministic resume and escaped-key regressions cover both
corrections. The subsequent restart-and-rollback correction keeps legacy
authority blocked until the preserved strict cache snapshot is restored,
proves the restore requires no compatibility request, and fails closed rather
than publishing an empty legacy-ready presentation. The following exact-head
cycle binds the in-memory projection to its actual compatibility authority, so
records loaded by a candidate-server connection test cannot merge into another
server's migration. It also restores remote-only legacy records through strict
migration reads when no private cache snapshot exists, while retaining the
cache-backed zero-network rollback path. The next exact-head correction keeps
failed post-rollback restoration in a blocking recovery state, exposes safe
pre-commit discard after an interrupted preparation, fences failed authority
inspection after hide or disposal, and source-binds overlapping connection
operations so stale candidate reads cannot republish private state.

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
