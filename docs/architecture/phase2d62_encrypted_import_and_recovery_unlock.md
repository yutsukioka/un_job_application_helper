# Phase 2D-62: Encrypted Import And Recovery Unlock

## 1. Purpose

Phase 2D-62 consumes the reviewed Phase 2D-61 encrypted backup to restore one
vault on a clean installation and enables explicit production recovery-key
unlock. It does not render or author private records.

## 2. Scope

The phase is limited to the exact 27-file repository allowlist. It adds the
import coordinator, import presentation, recovery unlock provider, create-only
storage operations, dynamic capability resolution, host and composition
integration, contract text, and deterministic tests.

It does not change Python, shared vectors, package manifests, Xcode projects,
app entry, process ownership, navigation, recovery export, local-vault
creation, persistence primitives, runtime activation, private models, cloud
sync, or migration.

## 3. Reconstructed Phase 2D-61 Baseline

Git and GitHub reconstruction identified Phase 2D-61 as merged through PR #82
with the reviewed 24-file tree on `origin/master`. Python, Swift, both iOS
schemes, GitGuardian, Codex, Copilot, and review-thread gates were verified
before Phase 2D-62 began.

## 4. Clean-Install-Only Policy

Normal restore requires no selected vault and no pending local-vault creation
or conflicting recovery import. The host and runtime must remain locked, the
lifecycle must be active, protected data must be available, and terminal or
reconciliation work must be absent.

An existing selected vault is never replaced, cleared, or merged.

## 5. Existing Export Format

Import consumes `atlasvault-export` version 1 without changing its wire
format. The envelope contains complete validated vault metadata and ordered
encrypted records and tombstones. It contains no local store ID, path,
selection, Keychain item, raw key, recovery text, or plaintext payload.

## 6. Existing Recovery Wrap

Recovery import requires exactly one valid vault-bound recovery-key wrap v2
with ID `primary-recovery-v2`. Passphrase-only metadata, missing recovery
wraps, duplicate recovery wraps, and malformed wrap metadata are rejected by
this workflow. The reusable export parser remains format-generic.

## 7. Import File Boundary

Only an explicitly user-selected local file is read. Import rejects remote
URLs, symbolic links, directories, non-regular files, zero-length files, and
files whose resource metadata or read result violates the fixed boundary.
Errors do not disclose the URL or filename.

## 8. File-Size Limit

The maximum encrypted export size is 128 MiB. Size is checked before and after
the single file read.

## 9. Security-Scoped File Access

The production reader standardizes the local URL, starts security-scoped
access when available, and stops it in `defer`. The selected URL is never
stored in a journal or observable presentation state.

## 10. Canonical Export Identity

The selected bytes are strictly decoded through
`AtlasVaultEncryptedExportEnvelope.decodeStrict` and canonically re-encoded.
SHA-256 identity and resume comparison use only those canonical bytes.
Semantically valid noncanonical input therefore maps to one stable identity.
Recovery import additionally requires every encrypted record ID to be unique.
Duplicate IDs are rejected before recovery verification, journaling, or local
store creation so the restored store remains valid for later mutations.

## 11. Recovery-Key Verification

The full entered code is consumed from a one-shot secret buffer, decoded as
strict UTF-8, parsed by `AtlasVaultRecoveryKeyCodec`, and used to unwrap the
single recovery wrap with the validated export vault ID. The result must be
exactly 32 bytes.

Wrong input, a modified wrap, or wrong vault binding returns a fixed failure
before persistence.

## 12. Temporary Hydration Verification

Every encrypted record and tombstone is hydrated with the recovered key before
the journal is written. Hydrated values are discarded immediately and never
enter SwiftUI, observable state, errors, or logs. A corrupt record blocks
restore.

## 13. Import Journal

The device-only Keychain journal uses service
`com.atlasvault.recovery-import`, account `pending-v1`, and
`afterFirstUnlockThisDeviceOnly`.

Its exact envelope contains:

- `format` and strict integer `version`;
- canonical lowercase import, export, and store UUIDs;
- validated opaque vault ID;
- strict UTC-seconds creation timestamp;
- lowercase SHA-256 export, local-store, and vault-key fingerprints.

Import, export, and store IDs must be pairwise distinct.

## 14. Journal Privacy

The journal contains no export bytes, recovery key, recovery text, vault key,
file URL, local-store URL, path, encrypted records, plaintext records, or
public-search state. Its description is fixed and redacted.

## 15. Journal Digests

The three SHA-256 values are verification fingerprints, not recovery material.
They bind resume and reset to the canonical export, exact canonical local
store, and recovered vault key. Uppercase, malformed, or non-64-character
digests fail strict decoding.

## 16. Imported Local-Store Envelope

The imported local store remains `atlasvault-local-store` version 1. Its store
ID is a new lowercase UUID independent of export and import IDs. Created and
updated timestamps are the import timestamp. Validated export metadata and
encrypted records are preserved exactly and in order.

The import ID and export ID are not serialized into the local store.

## 17. Store-First Ordering

After verification, restore writes the journal and then atomically creates the
local store with `overwrite: false`. An existing destination is never
replaced. Canonical read-back and SHA-256 equality are required before key
creation.

## 18. Create-Only Keychain Key

`AtlasVaultKeyCreating.createVaultKey` validates the vault ID and exactly 32
key bytes, then calls Keychain add with device-only accessibility. Duplicate
items map to a fixed collision. It never calls update. Historical
`saveVaultKey` upsert behavior remains unchanged for existing callers.

## 19. Create-Only Selected-Vault Registration

`AtlasVaultSelectionCreating.createSelection` reuses the strict selected-vault
envelope and performs Keychain add only. A duplicate maps to a fixed existing
selection failure and never updates the existing item. Historical selection
upsert behavior remains unchanged.

## 20. Selection Commit Point

Selection is written only after the exact store and exact Keychain key exist
and have been read back. It is the restore commit point.

## 21. Selection Read-Back

The coordinator reads selection back and requires the same validated vault ID.
A missing, different, or unreadable selection fails closed.

## 22. Journal Completion

The journal is cleared only after selection read-back. Clear is the final
normal transaction operation.

## 23. Durability-Unconfirmed Policy

`committedDurabilityUnconfirmed` stops before Keychain key creation and
selection registration. The journal and any valid store remain. Explicit
resume reselects the same export, re-enters the recovery key, and verifies the
existing store, then explicitly synchronizes its parent directory before
progressing. A failed retry remains durability-verification-required and
creates neither a Keychain key nor a selection.

## 24. Interrupted Resume

Resume always requires explicit file reselection and full recovery-key
re-entry because neither is journaled. The canonical export digest, export ID,
vault ID, local-store digest, and vault-key digest must match.

Supported stages are journal only, journal plus store, journal plus store and
key, and committed matching selection with a journal. Matching existing
resources are reused; missing resources are created in order; differing
resources fail recovery-required. Equivalent concurrent operations coalesce,
and unused joining secrets are cleared.

## 25. Completion-Pending Flow

If selection creation and read-back succeed but journal clearing fails, the
restore is committed and no resource is rolled back. The selection gate keeps
the host closed. Explicit finish requires the same export and recovery key,
reverifies store, key, and selection, clears the journal, and then opens the
ordinary unlock panel.

## 26. Explicit Pending Reset

`Discard Incomplete Restore` is available only for a journal-backed,
unselected restore and requires a second UI confirmation. It is never
automatic.

## 27. Reset Hash Binding

Reset validates both the store and key fingerprints before deleting either.
It then removes only the exact store file and exact Keychain key and clears the
journal last. Missing partial resources are tolerated. A mismatch or any
selection preserves all resources and fails closed. Directories are retained.

## 28. No Destructive Overwrite

Import never overwrites a store, vault-key item, selection item, pending
transaction owned by another import, or unrelated recovery data. Exact journal
ownership is revalidated before every persistent transaction stage.
Production composition also shares one non-reentrant pending-transaction
authority between local-vault creation and recovery import. The authority
holds the complete creation check-and-run sequence and every journal-backed
import operation, so neither transaction can pass its opposing-journal check
while the other is waiting to create its journal.

## 29. Pending-Transaction Selection Gate

The production selector is wrapped by one combined gate. A selected vault is
visible only when both the local-vault creation journal and recovery-import
journal are absent. Journal read failure returns no selection. The
recovery-export setup journal intentionally does not hide an existing vault.
The gate checks recovery-import state even when the underlying registry has no
selection and publishes that fixed pending state to all production roots.
While an import journal exists, roots suppress local-vault creation and the
production creation coordinator is independently wrapped by a fail-closed
journal gate. Restore resume/reset remains available.
The journal gates remain fail-closed defense in depth; the shared transaction
authority closes the check-then-create race between the two journal writers.

## 30. Dynamic Unlock Capabilities

Unlock capabilities are resolved each time a selected-vault controller is
created. The historical `currentProduction` compatibility value remains
local-key-only. A fixed resolver preserves existing hosts and tests.

## 31. Local-Key Capability

Local-key unlock is available only when the selected vault's device-only
Keychain item exists and contains exactly 32 bytes. Missing or malformed data
is unavailable. A Keychain read error does not suppress an otherwise valid
recovery capability.

## 32. Recovery-Key Capability

Recovery unlock is available only when the selected local store strictly
validates and contains exactly one valid recovery wrap v2 bound to that vault.

## 33. Passphrase Unavailable

Passphrase remains unavailable in production. V1 passphrase metadata remains
decode-compatible but is not a production unlock provider.

## 34. Recovery-Unlock Provider

The provider loads the selected encrypted local store without a local key,
strictly validates versioned metadata, requires one recovery wrap, parses the
entered code, unwraps with the selected vault ID, and returns exactly 32 bytes
to the existing activation request. Errors are fixed and redacted.

The provider performs no store, Keychain, selection, runtime, or network write.

## 35. Vault-Aware Derivation

Recovery derivation receives both the validated request vault ID and consumed
secret bytes. The historical one-argument dependency initializer remains
source-compatible by wrapping its closure.

## 36. Recovery Unlock Is Session-Only

The recovered vault key exists only for the activation session. Recovery
unlock does not call save/create key APIs and does not silently recreate a
deleted device-local Keychain key.

## 37. Host Capability Resolution

Selected-vault completion resolves capabilities before controller creation and
rechecks selection operation identity, generation, lifetime, and terminal
state after the await. Failure or zero methods publishes fixed
key-unavailable with no controller. Stale resolution cannot create a
controller. Activating and reconciliation states preserve the resolved
capability snapshot.

## 38. Import Presentation Owner

One private-free `ObservableObject` owns fixed states for ready, reading,
recovery entry, verification, import, pause, durability verification,
completion pending, recovery required, failure, and completion. It stores no
URL, export bytes, secret, identifier, path, or private record.

## 39. Import View

The view presents fixed warnings, a local recovery `SecureField`, explicit
restore confirmation, explicit pause/resume, and separately confirmed
incomplete-restore reset. It displays no vault, export, store, record, or path
identifier. The restore action captures and forwards the actual confirmation
value before clearing local state; it never substitutes a hard-coded approval.

## 40. File Importer

`fileImporter` is opened only by an explicit button and is restricted to the
`.atlasvault` filename type. Resume and finish require another explicit file
selection.

## 41. Secret Lifetime

Recovery text exists only in view-local state and one-shot secret buffers. The
view clears local text before async submission, pause, and state transitions.
Pre-confirmation sheet dismissal requires an explicit drained pause. Lock,
background, protected-data loss, and stop drain operations and clear
secret-bearing UI. Temporary recovery and vault-key buffers are best-effort
wiped.

## 42. Shared Multi-Window Authority

Production composition constructs one import coordinator and presentation
owner plus one pending-import availability authority. Multiple roots share
them. Scene-local claims prevent duplicate sheets without creating duplicate
filesystem or Keychain authorities. Pending-import transitions update that
authority after journal save and clear, and explicit host selection
reconstruction refreshes it after relaunch.
The same production composition constructs exactly one pending-transaction
authority and injects it into both the import coordinator and local-vault
creation gate.

## 43. Lifecycle Cancellation

Unsafe lifecycle events dismiss the import sheet, cancel and drain retained
work, clear prepared in-memory state, and preserve a safe journal and matching
partial resources. Waiting transaction-authority leases are
cancellation-aware, so a stopped waiter is removed without disturbing the
active transaction or blocking drain. No detached cleanup is used.

## 44. Harness Terminal Stop

Harness stop concurrently stops the host, lifecycle forwarder, creation owner,
recovery-export owner, and recovery-import owner. Import work cannot outlive
the process harness.

## 45. Public Search Continuity

Import uses no public network service and does not mutate public-search state.
The locked public shell remains available before restore and while no vault is
selected.

## 46. Source Export Journey

The end-to-end test creates a source vault through Phase 2D-60, explicitly
unlocks by local key, configures Phase 2D-61 recovery/export, and writes the
verified encrypted document.

## 47. Clean-Device Restore Journey

A fresh Keychain and empty Application Support root reach no-vault, perform
public search, explicitly select the export, explicitly enter and confirm the
recovery key, install store/key/selection, clear the journal, and open the
existing unlock panel.

## 48. Recovery-Only Later Launch

After the imported device-local key is deleted, a new process retains store
and selection. Dynamic capabilities expose recovery only. Wrong input fails;
correct input reaches the existing unlocked transition; Keychain remains
without a local vault key.

## 49. Wrong-Key Behavior

Wrong recovery input returns fixed unlock/import failure, echoes no secret,
and creates or updates no import journal, store, key, or selection.

## 50. No Private Rendering

Hydrated values are verification-only and are never published. Production root
continues to expose only the public shell, explicit unlock UI, recovery/export
setup, and recovery import.

## 51. No Authoring

This phase adds no saved-search, saved-job, tracker, note, snippet, draft, or
generated-document authoring.

## 52. No Cloud Or Migration

Import is local and uses no network provider. There is no cloud sync,
passphrase support, legacy plaintext migration, LocalAuthentication, or
biometric behavior.

## 53. Merge-Stable Test Policy

Phase tests assert behavior and permanent source boundaries. They contain no
`origin/master...HEAD` comparison, hard-coded Git SHA, current-branch blob
pin, or historical phase-end tree requirement.

## 54. TDD Evidence

The red checkpoint was committed before production implementation. It proved
the import coordinator, journal, create-only writes, recovery provider,
vault-aware derivation, dynamic host capabilities, import presentation, and
end-to-end restore journey were absent. Subsequent deterministic regressions
cover review and self-review findings.

## 55. Test Coverage

Focused suites cover strict file input, canonical identity, duplicate record
rejection, recovery verification, journal privacy, transaction ordering,
durability pause and explicit directory re-synchronization, partial resume,
completion pending, reset, create-only writes, pending selection and creation
gating, non-reentrant creation/import transaction serialization,
cancellation-aware waiter removal, dynamic capabilities, stale host
resolution, vault-aware unlock, explicit confirmation forwarding, canonical
test-file roots, view lifecycle, root/harness integration, and full
source-to-restore recovery-only relaunch.
The final review-fix regression runs passed 236 Python tests and 1,209 Swift
tests.

## 56. iOS Build And Smoke Evidence

The `AtlasApple` and `AtlasIOSHost` schemes both passed
`build-for-testing` against the generic iOS Simulator destination after the
final review-fix changes. No compatible simulator was booted, so no tap-level
file-import smoke result is claimed; the deterministic in-process
end-to-end suite remains authoritative.

## 57. Go/No-Go

- encrypted export import: implemented;
- clean-install restore: implemented;
- create-only local key: implemented;
- create-only selection: implemented;
- interrupted restore resume: implemented;
- explicit incomplete-restore reset: implemented;
- dynamic recovery capability: implemented;
- production recovery unlock: implemented;
- recovery unlock session-only: implemented;
- local-key unlock preserved;
- private rendering: not implemented;
- private authoring: not implemented;
- passphrase: not implemented;
- multi-vault replacement: not implemented;
- cloud sync: not implemented;
- migration: not implemented;
- production readiness: not claimed.

## 58. Deferred Work

Vault replacement, merging, multiple-vault selection, passphrase unlock,
private-state presentation and authoring, cloud sync, legacy migration,
generated-document rendering, and biometric policy remain outside this phase.

## 59. Next Product Gate

Phase 2D-63 must implement the first private-state presentation and encrypted
saved-search create/list/delete journey, with immediate lock-time private-state
removal and no cloud sync, legacy migration, or generated-document rendering.
