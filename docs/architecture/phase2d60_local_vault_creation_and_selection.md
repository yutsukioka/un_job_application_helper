# Phase 2D-60: Device-Local Vault Creation and Selection

## 1. Purpose

Phase 2D-60 closes the fresh-install gap in the reviewed production route. A
user who explicitly requests unlock and reaches `noVault` can explicitly
create a device-local encrypted vault, continue to the existing unlock panel,
and explicitly unlock that same vault again after relaunch.

## 2. Scope

This phase adds one resumable creation transaction, one private-free creation
presentation owner and view, a narrow host selection retry, production-harness
assembly, and production-root presentation. It does not change app entry,
process ownership, navigation, private rendering, passphrase or recovery-key
support, cloud sync, migration, or biometric policy.

## 3. Reconstructed Phase 2D-59 Baseline

Phase 2D-59 was squash-merged by PR #80. Current master uses one process
delegate and process owner, fail-closed reference-capture routing, lazy
production composition, one shared harness, and
`AtlasVaultProductionRootView` for the normal route. Its full Swift baseline
and the package and app iOS build baselines passed before Phase 2D-60 tests
were added.

## 4. Fresh-Install Gap

Before this phase, an explicit unlock request with no selected vault published
`noVault`, closed ordinary unlock admission, and exposed no reviewed creation
path. The lower-level Keychain, path, atomic persistence, selection, runtime,
and hydration boundaries existed but were not assembled into a user journey.

## 5. Device-Local-Only Security Position

The first vault key is 256 random bits stored through the reviewed
device-only Keychain key-store boundary. The key is not written into the
creation journal, local-store JSON, presentation state, diagnostics, or logs.
This phase makes no recoverability or production-readiness claim.

## 6. Recovery Warning

The creation view states that the vault is protected only by a device-local
Keychain key, that passphrase and recovery-key recovery are unavailable, and
that loss of app data or the Keychain item may make the vault inaccessible.
The create command remains disabled until the user explicitly acknowledges
that warning.

## 7. Creation Transaction

The coordinator orders work as selection read, journal read or creation, key
load or creation, store load or atomic creation, selection write, selection
read-back, and journal clear. No automatic unlock, method selection, public
search, or runtime activation occurs in this transaction.

## 8. Selected-Vault Readiness Boundary

The selected-vault registry is the readiness boundary. It is not written
until the device-local key exists and a valid encrypted store is either
confirmed from a previous attempt or reported committed by the atomic writer.

## 9. Creation Journal

The Keychain journal uses service `com.atlasvault.vault-creation`, account
`pending-v1`, and `afterFirstUnlockThisDeviceOnly` accessibility. Its fixed
format is `atlasvault-local-creation`, version 1.

## 10. Journal Privacy

The journal accepts exactly `format`, `version`, `vault_id`, `store_id`, and
`created_at`. Strict decoding rejects missing or additional fields, invalid
vault identifiers, empty store identifiers, and malformed timestamps. It
contains no key, path, payload, query, job identifier, or error detail.

## 11. Secure Vault-Key Generation

Production generation calls `SecRandomCopyBytes` for exactly
`AtlasVaultRecordCrypto.vaultKeyByteCount`, currently 32 bytes. Generator
failure becomes the fixed `unavailable` creation failure. Tests inject their
own generator and never write a real Keychain item.

## 12. Vault and Store Identifiers

Vault and store identifiers are separately generated lowercase UUID strings.
The vault identifier must pass the existing path validator, the store
identifier must be non-empty, and the two identifiers must differ. Neither is
published by the creation presentation layer.

## 13. Key Lifetime and Best-Effort Wipe

The transaction keeps working key bytes in mutable `Data` only while resolving
and using the encrypted store. A `defer` resets the bytes and releases
capacity where practical. This is best-effort cleanup and is not represented
as universal Swift memory zeroization. Configured-selection verification loads
directly into one mutable local `Data` value so a second local copy-on-write
reference does not defeat that best-effort reset. Production store access
captures no key or unlocked session. Each synchronous load or save constructs
a temporary session from the coordinator-owned buffer, completes the
persistence call, and releases that session before the coordinator's deferred
reset. The coordinator buffer is therefore the final live creation-owned
in-process copy when best-effort wiping begins.

## 14. Canonical Empty Store

First creation writes local-store format `atlasvault-local-store`, supported
version 1, the journal store identifier, matching created and updated
timestamps, canonical AtlasVault metadata, and an empty encrypted-record
array.

## 15. Empty `key_wraps`

Initial metadata declares `atlas-vault` version 1, the opaque vault
identifier, `AES-256-GCM`, `Argon2id`, `HKDF-SHA256`, and
`AES-256-GCM` key-wrap AEAD. `key_wraps` is an empty array because this phase
does not create passphrase or recovery wrapping.

## 16. Atomic Store Creation

The coordinator resolves the reviewed Application Support root and per-vault
services, then calls the existing atomic persistence coordinator with
`overwrite: false`. The reviewed path remains
`<root>/Atlas/Vaults/<vaultID>/atlasvault-local-store.json`.

## 17. Durability-Unconfirmed Policy

`committed` permits selection registration.
`committedDurabilityUnconfirmed` publishes a fixed verification-required
state, retains the journal and key, and does not write selection. An explicit
retry loads and validates the existing store before proceeding.

## 18. Selection Registration

After a confirmed or validated store, the coordinator constructs a validated
`AtlasSelectedVaultID` and persists it through
`AtlasVaultSelectionRegistering`.

## 19. Selection Verification

The coordinator immediately reads through `AtlasVaultIDSelecting` and
requires the exact selected identifier. A failed or mismatched read-back
fails closed and retains the resumable journal, key, and store.

## 20. Journal Completion

The journal is cleared only after selection read-back succeeds. A clear
failure leaves selection intact, returns fixed `completionPending`, and does
not open the unlock panel. Explicit retry verifies the configured vault and
clears the journal. On relaunch, a persisted selection remains masked from
the host while any creation journal remains, so ordinary unlock cannot bypass
this explicit completion step.

## 21. Crash Resumption

An existing journal fixes the same vault identifier, store identifier, and
creation timestamp across retries. Existing valid key and empty store
material are reused. A matching selected vault plus journal is verified
before the journal is cleared. A relaunch with that partial state returns to
the explicit local-vault setup surface; it does not silently enter the
ordinary selected-vault unlock route.

## 22. Cancellation and Pause

Creation runs in one coordinator-owned task. Caller cancellation does not
orphan it. Explicit pause cancels and drains that task at deterministic
cancellation boundaries and leaves every valid persistent artifact in place
for retry.

## 23. No Destructive Rollback

Pause and failure do not delete the vault key, local store, selected-vault
item, or journal. The only normal deletion is the completed journal after
selection verification.

## 24. Existing Selected-Vault Behavior

A selected vault without a journal is treated as configured only after its
Keychain key and encrypted store validate. The existing store may contain
valid encrypted records and reviewed wrapped-key metadata. The host-facing
selection gate exposes that selection only when the creation journal is
absent. The coordinator continues to read the underlying registry directly
so it can verify and complete a matching pending transaction.

## 25. Conflict and Recovery-Required Behavior

Different selected and journal vaults, missing or malformed keys, missing or
mismatched stores, strict-journal failures, and unexpected destinations
produce fixed `recoveryRequired`. No identifier, path, key, serialized data,
OS status, or underlying error reaches presentation.

## 26. Creation Coordinator

`AtlasLocalVaultCreationCoordinator` is an actor implementing
`AtlasLocalVaultCreating`. Its injected environment isolates selection,
journal, key, store, identifier, timestamp, and random-material operations for
deterministic ordering and failure tests.

## 27. Creation Operation Coalescing

Concurrent callers join the same retained operation. Success is idempotent,
and explicit retry starts a new operation only after the prior one has
completed or been paused.

## 28. Creation Presentation Owner

The MainActor `AtlasLocalVaultCreationPresentationOwner` publishes only fixed
hidden, ready, creating, paused, failed, durability-verification,
completion-pending, and recovery-required states. It constructs no task until
an explicit create action.

## 29. Creation View

`AtlasLocalVaultCreationView` renders the warning, acknowledgment toggle,
fixed progress and failure content, and explicit create, pause, retry, and
close commands. It owns no coordinator, Keychain, filesystem, runtime, or
network dependency.

## 30. Explicit Risk Acknowledgement

The acknowledgment resets when creation is presented. The create command is
unavailable until the user opts in. There is no text or secret input and no
automatic action from view construction, `.task`, or appearance callbacks.

## 31. Host No-Vault Retry

`requestUnlockPanel()` retains its ordinary admission rule and adds one narrow
selection retry from exact `noVault`. Retry requires started, active,
protected-data-available, lifecycle-admitted state with no safe check,
selection, submit, reconciliation barrier, stop, termination, or existing
unlock controller. A selected vault with an uncleared or unreadable creation
journal is deliberately projected as `noVault` to this host boundary until an
explicit setup retry completes or reaches fixed recovery-required state.

## 32. No Automatic Unlock

The no-vault retry performs selection only. It does not load a key, activate
the runtime, select an unlock method, submit unlock, hydrate private state, or
publish an unlocked transition.

## 33. Existing Unlock-Panel Continuation

When retry finds a selected vault, the host sets the public shell to
`locked`, installs one existing unlock controller, and opens the existing
unlock panel with no selected method. The user must separately select and
submit local-key unlock.

## 34. Harness Assembly

The production-like composition factory assembles one coordinator from the
existing runtime services, selection registry, atomic persistence, and the
same Keychain client used by key and selection storage. Assembly performs no
I/O. It also assembles a side-effect-free host selection gate over that same
registry and journal store; journal inspection occurs only during an explicit
unlock request for a persisted selected vault.

## 35. Shared Multi-Window Creation Authority

The harness retains one creation presentation owner and action context.
Every production root made by that process harness receives the same context,
so windows do not create competing transactions or owners. Modal visibility
is separate per-root state: only the scene that accepts the create action
presents a sheet, while other scenes observe the shared transaction state
without presenting duplicate sheets. Presentation uses a transferable,
non-sensitive scene claim. A surviving scene can explicitly continue a
non-hidden setup after the initiating window closes; claiming from another
scene atomically transfers presentation instead of creating a second
transaction or simultaneous sheet.

## 36. Harness Terminal Stop

Harness terminal stop asks the creation owner to cancel and drain active
creation while existing host and lifecycle teardown run. No detached cleanup
can outlive the stopped harness.

## 37. Production-Root Integration

The production root stores an optional immutable creation context. When
present, a private nested child observes the injected owner and offers an
explicit sheet from the locked-public `noVault` state. The root constructs no
creation authority or service. Each nested root owns only a local Boolean for
its sheet presentation and a non-sensitive presentation claim; it does not
duplicate the shared coordinator, presentation owner, or actions. Releasing
or transferring a claim does not dismiss, cancel, or reset the shared
transaction.

## 38. Compatibility Initializers

The original three-argument root initializer and existing internal harness
initializer remain valid and create no creation owner or UI. Creation appears
only through the new injected context used by production assembly.

## 39. Public Search Continuity

The locked public shell remains visible when no vault exists, and public
search remains available before, during, and after the explicit creation
journey.

## 40. First-Launch Journey

The deterministic in-process journey starts a fresh production harness with a
temporary Application Support root and in-memory Keychain, performs public
search, explicitly reaches `noVault`, explicitly creates, verifies persistent
artifacts, and continues to the locked unlock panel.

## 41. Relaunch Journey

After explicit stop, a new harness using the same temporary root and
in-memory Keychain finds the selected vault without starting creation. A new
explicit unlock request opens the existing panel, and explicit local-key
selection and submit unlock the same vault. A second relaunch case injects a
journal-clear failure after selection readiness, verifies that ordinary
unlock remains gated, explicitly completes the matching journal, and only
then opens the existing unlock panel.

## 42. Empty-Store Hydration

The real runtime activation path decrypts and hydrates the canonical empty
record set. The tested public transition is `unlockedTransition`; no private
record content is rendered.

## 43. Error Redaction

Coordinator outcomes, failures, owner state, and diagnostics use fixed
strings. Underlying errors, Keychain statuses, identifiers, timestamps, URLs,
paths, keys, and encrypted or decrypted payloads are not exposed.

## 44. No Private Rendering

This phase renders only the existing public search, non-sensitive vault and
unlock states, explicit creation controls, and `unlockedTransition`. It adds
no saved-search, tracker, note, snippet, draft, generated-document, encrypted
record, or decrypted private-state UI.

## 45. No Passphrase or Recovery

No passphrase provider, recovery-key provider, key derivation, wrap creation,
or recovery material is implemented. Local key remains the only enabled
unlock capability.

## 46. No Cloud or Migration

The implementation performs no cloud synchronization, remote backup,
cross-device transfer, migration, biometric authentication, or legacy vault
conversion.

## 47. TDD Evidence

The red checkpoint introduced six test surfaces before production code. Each
failed for missing creation core/view types, no-vault retry, harness/root
context, or the blocked fresh-install journey. Persistent evidence records
the red commit and focused failures.

Exact-head review added deterministic red evidence for two integration
boundaries: scene-local sheet presentation and relaunch with a selected vault
plus pending journal. Both were repaired without changing the file allowlist.
A subsequent exact-head review added deterministic red evidence for reclaiming
a non-hidden shared setup after the presenting scene disappears. The repair
uses a transferable UI claim and changes no persistence or process authority.
Exact-head Copilot review then added deterministic red evidence for resetting
the local-only warning acknowledgement on each presentation and retaining only
one mutable local key buffer during configured-selection verification.
Final exact-head Codex review added deterministic red evidence that production
store-access closures retained an unlocked session and its key copy. Store
access now accepts the coordinator-owned buffer per synchronous operation and
constructs no session until that operation executes.

## 48. Test Coverage

Focused coverage includes strict journal encoding, secure generation,
transaction order, canonical store bytes, durability retry, resumption,
conflicts, cancellation and coalescing, presentation behavior, view source
boundaries, host retry, harness compatibility, root compatibility, and the
fresh-install/relaunch journey. It also covers pending-journal selection
gating, explicit post-selection completion after relaunch, and scene-local
sheet ownership over shared process authority. Presentation-owner tests also
verify that a second scene can take the claim without dismissing the shared
flow and that stale claim release cannot hide it. Source-boundary tests verify
per-presentation acknowledgement reset and the absence of a second local key
buffer before best-effort wiping. They also require keyless store-access
construction and operation-scoped session creation so returned closures retain
neither a session nor vault-key bytes. Existing Phase 2D-59 and historical
merge-stability suites remain required.

## 49. iOS Build and End-to-End Evidence

Both the Swift-package and actual app iOS Simulator schemes must build after
integration. The in-process test uses a temporary root, persistent in-memory
Keychain, real atomic writer, real runtime activation and hydration, fake
lifecycle/public jobs, and no network. Simulator launch evidence is reported
separately and never substituted for the explicit in-process journey.

## 50. Go/No-Go

- Fresh-install device-local vault creation: implemented.
- Random local vault key: implemented.
- Atomic empty encrypted store: implemented.
- Selected-vault registration: implemented.
- Crash-resumable journal: implemented.
- Explicit local-key unlock after creation: implemented.
- Relaunch local-key unlock: implemented.
- Public search continuity: implemented.
- Private rendering: not implemented.
- Passphrase/recovery wrapping: not implemented.
- Encrypted export: not implemented.
- Cloud sync: not implemented.
- Migration: not implemented.
- Production readiness: not claimed.

## 51. Deferred Work

Recovery-ready wrapping, recovery-material confirmation, encrypted export,
restore verification, reset and revocation policy, private-data authoring, and
private rendering remain outside this phase.

## 52. Next Product Gate

Phase 2D-61 must implement recovery-ready vault-key wrapping and encrypted
export before private-data authoring or rendering is enabled. It must include
explicit recovery-key generation or passphrase wrapping, authenticated
wrapped-vault-key metadata, recovery material confirmation, encrypted export,
restore verification, and a safe vault reset and revocation policy. Cloud
sync remains excluded unless separately reviewed.
