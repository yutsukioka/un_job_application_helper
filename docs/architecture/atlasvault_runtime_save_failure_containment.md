# AtlasVault Runtime Save-Failure Containment

## Purpose

This follow-up defines and implements the runtime boundary for save outcomes
that are safe to retry, committed with uncertain durability, or unsafe to keep
unlocked. It closes the fail-open gap identified by the Phase 2D-43 readiness
audit without adding UI or app-host wiring.

## Scope

The change is limited to the runtime facade, its stateless presentation
projection, and fake regression tests. It adds no direct filesystem, Keychain,
public-cache, SwiftUI, app-entry, migration, or cloud dependency.

## Existing Commit Proof

`AtlasVaultAtomicStoreWriter` throws only before destination replacement. Once
replacement succeeds, it returns either `committed` or
`committedDurabilityUnconfirmed`; directory synchronization failure is a
result, not a thrown pre-commit error.

Within the production runtime graph, `AtlasVaultActivationController` maps an
error thrown before an atomic result to its typed `saveFailed` operation error.
After a committed result, it reloads and hydrates the encrypted store before
installing refreshed private state. A failed post-commit refresh already clears
the session and reports `committedStateUnavailable`.

## Outcome Classification

### Recoverable Pre-Commit

The facade treats only the controller's explicit `saveUnavailable` or
`saveFailed` operation errors as recoverable. Those errors occur before an
atomic commit result exists. The active session remains unlocked, the existing
in-memory private state remains unchanged, and the public error is the fixed
non-sensitive `saveFailed` category.

Session mismatch and cancellation retain their existing pre-commit behavior
and are not reclassified as committed or fatal.

### Committed Durability Unconfirmed

`committedDurabilityUnconfirmed` remains a successful outcome. The activation
controller reloads the committed encrypted store and updates in-memory state;
no rollback is attempted. The stateless presentation adapter can project the
fixed `saveDurabilityUnconfirmed` warning while retaining only the refreshed
current-generation private projection.

### Fatal Or Integrity Unknown

An explicit `AtlasVaultRuntimeSaveFailure.integrityUnknown`, or any error that
escapes without an existing trusted classification, fails closed. The facade
publishes `locking` before awaiting environment teardown, then publishes
`locked`. The environment lock invalidates the active session and clears its
private-state store. The thrown facade error is the fixed non-sensitive
`saveIntegrityUnknown` category.

No subsequent private read or save is permitted until explicit reactivation.
Presentation receives locked status and cannot retain a private projection.

## Concurrency Boundary

The fail-closed transition uses the facade's operation epoch. A failure from a
save that has already been superseded is reduced to cancellation rather than
overwriting a newer operation's status. While authoritative cleanup is in
progress, status is `locking`, so private reads and new activation or save work
are unavailable.

Explicit lock remains idempotent. Existing cancellation and lock-priority tests
continue to protect pre-commit cancellation and stale committed completion.

## Error And Logging Privacy

Save classifications contain no payload, record identifier, vault identifier,
key, path, ciphertext, count, or underlying error text. Runtime, save-failure,
presentation, request, and snapshot descriptions remain fixed or redacted. No
logging or analytics call is added.

## Verification

Focused tests cover:

- successful and recoverable save behavior;
- committed durability warning with updated state;
- explicit and unclassified integrity-unknown failure;
- private-state removal while cleanup runs;
- locked private reads and saves after fatal failure;
- explicit reactivation after containment;
- repeated lock;
- public-snapshot immutability;
- error and debug redaction;
- source guards for UI, cache, Keychain, filesystem, network, and app-entry
  coupling.

Existing runtime facade and presentation adapter suites also run because these
are shared boundaries.

## Deferred

- Phase 2D-44 observable presentation ownership
- App-host warning and command coordination
- SwiftUI save controls or private-state rendering
- Production app-entry integration
- Migration and plaintext cleanup
- Cloud sync, recovery, onboarding, and key rotation
- Production threat-model and readiness review
