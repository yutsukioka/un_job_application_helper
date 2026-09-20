# Phase 2D-35 AtlasVault Runtime Facade

## Purpose

Phase 2D-35 implements the runtime-neutral facade designed in Phase 2D-34. It
provides one actor-isolated boundary for explicit activation, lock, private
state reads, and encrypted mutation saves while leaving application and UI
integration deferred.

## Scope

This phase adds the facade, a narrowly scoped activation-controller save
capability, and tests. It does not add a SwiftUI observable model, app-launch
call site, `SearchViewModel` or `AtlasLocalCache` integration, migration,
cloud sync, recovery UX, onboarding, or key rotation. It does not claim
production readiness.

## Public Boundary

`AtlasVaultRuntimeFacading` exposes only:

- non-sensitive runtime status;
- explicit activation from a non-semantic vault ID and optional already
  unwrapped key;
- explicit lock;
- mutation application with a non-sensitive save outcome.

Hydrated state is intentionally absent from the public protocol. The
module-internal `AtlasVaultPrivateStateReading` capability is reserved for a
later privacy-reviewed presentation adapter.

## Facade State

`AtlasVaultRuntimeFacade` is an actor. Its stored mutable state is limited to a
redacted status, an operation epoch, and the current operation token. It does
not store a vault key, unlocked session, filesystem URL, persistence
coordinator, public snapshot, or hydrated-state copy.

The public status categories are `locked`, `activating`, `locking`,
`unlocked`, `saving`, and a redacted activation failure. Status never includes
vault IDs, paths, record types, record counts, or private payload values.

## Construction

`production()` constructs the side-effect-free Phase 2D-29 runtime graph and
an activation controller. `runtimeServices(_:)` supports injected graphs for
tests. Construction invokes no root provider, Keychain client, filesystem
reader or writer, record crypto, hydrator, saver, or private-state operation.

## Activation

The facade forwards activation to `AtlasVaultActivationController`. The
controller remains responsible for vault-ID validation, key selection, root
resolution, encrypted-store loading, hydration, generation-scoped state
installation, and cleanup. A supplied key passes through the request once and
is not retained by the facade.

Activation succeeds only after the controller has installed complete private
state. Failure is translated to a stable category. Cancellation invokes
controller cancellation and lock cleanup before the facade returns to
`locked`.

## Private State

Private state is read through the controller's generation-checked snapshot
operation. The facade verifies its operation epoch before returning the
snapshot, so a read started before a winning lock cannot publish private state
after lock. Reads fail while locked or while a mutating operation is active.

The snapshot is module-internal, immutable, non-Codable, and redacted in
diagnostic output. It is not copied into public cache state.

## Save Transaction

The activation scope now contains an internal mutation-save closure. The
runtime composition wires that closure to the active per-vault record saver,
encrypted local-store merger, and atomic persistence coordinator. The
activation controller performs the transaction while borrowing its canonical
key owner synchronously:

1. verify the expected vault ID against the active key owner and scope;
2. encode and encrypt mutations with the record saver;
3. load the current encrypted local-store envelope;
4. merge encrypted record envelopes;
5. atomically persist the merged encrypted store;
6. reload and hydrate the committed encrypted store;
7. replace generation-scoped in-memory private state;
8. return only the atomic commit category.

The facade never receives a key, decrypted payload, encrypted record array,
local-store envelope, or path from this transaction.

## Save Failure Semantics

A locked save fails before invoking the controller save dependency. A vault-ID
mismatch fails before the record saver. Duplicate, stale-revision, encoding,
encryption, merge, read, and write failures are collapsed to a non-sensitive
save category at the facade boundary.

A failure before atomic commit leaves installed private state unchanged. If
commit succeeds but reloading, hydration, or state replacement fails, the
controller wipes the active key owner, clears private state, and locks. The
facade reports that commit occurred without exposing underlying data.

Both `committed` and `committedDurabilityUnconfirmed` are successful encrypted
commit outcomes and remain distinct.

## Operation Ordering

An opaque monotonically increasing token protects every operation across
actor re-entrancy. One activation or save may be active at a time. Overlapping
saves and activation during save fail with `operationInProgress`; subsequent
non-overlapping saves execute in order.

Lock has priority. It invalidates the prior token, asks the controller to
cancel activation when applicable, and waits for controller lock cleanup.
Late activation, save, or private-read continuations cannot republish unlocked
state. Repeated lock is idempotent.

## Privacy And Diagnostics

Facade requests, status, outcomes, errors, snapshots, the facade itself, and
the activation-controller save error use fixed redacted descriptions. They do
not interpolate keys, vault IDs, paths, private payloads, record IDs,
revisions, or underlying errors. The implementation emits no logs.

Record type remains inside the encrypted payload. Persistence and merger
components operate on the encrypted-record envelope allowlist only. No private
state or saved-only metadata is written to `AtlasPublicLocalSnapshot`.

## Verification

Tests cover side-effect-free construction, activation success and failure,
locked reads and saves, lock idempotence, post-activation private state,
successful and failed saves, vault matching, sequential and overlapping
operations, lock during save, cancellation, commit-with-refresh failure,
diagnostic redaction, and source guards.

An injected temporary-root integration test exercises the real saver,
encrypted merger, atomic persistence seam, reload, and hydrator. It verifies
that the serialized local store omits fake private sentinels and plaintext
record type strings while a separate public snapshot remains byte-for-byte
unchanged.

## Deferred

- SwiftUI presentation and observation;
- app-launch and scene lifecycle wiring;
- `SearchViewModel` or `AtlasLocalCache` private-state integration;
- cross-process serialization and file coordination;
- migration and cleanup of old plaintext snapshots;
- cloud sync and conflict UI;
- recovery UX, device onboarding, and key rotation;
- stronger best-effort memory clearing beyond current Swift `Data` limits;
- production-readiness hardening and platform lifecycle tests.

## Recommended Next Phase

Phase 2D-36 should design application lifecycle integration around this facade
without implementing SwiftUI or app entry-point wiring.
