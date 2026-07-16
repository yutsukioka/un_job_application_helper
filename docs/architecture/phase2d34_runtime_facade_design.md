# Phase 2D-34 AtlasVault Runtime Facade Design

## 1. Purpose

Phase 2D-34 defines a narrow runtime-neutral facade over the internal
AtlasVault composition, activation, in-memory private-state, record-save, and
atomic-persistence services. The facade is the future application-facing vault
boundary, but it is not a UI model.

## 2. Design-Only Scope

This phase adds documentation only. It adds no facade implementation, service
invocation, Keychain or filesystem access, vault activation, record mutation,
SwiftUI or app-launch call site, migration, cloud sync, recovery flow,
onboarding, or key rotation. It does not claim production readiness.

## 3. Existing Internal Service Graph

`AtlasVaultRuntimeFactory` constructs the inactive root provider, key store,
record saver and hydrator, local-store merger, atomic writer, and per-vault
persistence factory without invoking them. `AtlasVaultActivationController`
owns activation serialization and the canonical wipeable key owner. Its
generation-scoped `AtlasVaultPrivateStateStore` installs complete hydrated
state only after successful activation and clears that state on failure, lock,
and teardown.

The persistence coordinator can load encrypted stores and atomically merge and
save encrypted records. `AtlasPublicLocalSnapshot` and public job cache remain
outside this graph.

## 4. Why A Facade Is Needed

A future app host should not coordinate the activation controller, private
store, saver, persistence coordinator, operation generations, and error
redaction independently. A facade gives that host one small API while keeping
key ownership, bound per-vault services, filesystem URLs, encrypted envelopes,
and hydrated-state generations internal.

The facade is not a service locator. It owns one coherent runtime scope and
enforces operation ordering and lifecycle invariants around that scope.

## 5. Proposed Facade Protocol

The first implementation should use an asynchronous `Sendable` protocol:

```swift
public protocol AtlasVaultRuntimeFacading: Sendable {
    func status() async -> AtlasVaultRuntimeStatus
    func activate(_ request: AtlasVaultRuntimeActivationRequest) async throws
    func lock() async
    func privateState() async throws -> AtlasVaultPrivateStateSnapshot
    func apply(_ request: AtlasVaultRuntimeMutationRequest) async throws
        -> AtlasVaultSaveOutcome
}
```

Names may be refined in Phase 2D-35. `privateState()` is a restricted private-
data capability, not a public-status projection and not a UI-safe observable
property.

## 6. Proposed Facade Implementation Type

Use an `AtlasVaultRuntimeFacade` actor. Actor isolation protects facade state,
but explicit operation tokens are still required across `await` points because
actors are re-entrant. The actor must not be global or a singleton.

The implementation should have a dependency-injected initializer for tests and
a side-effect-free factory that accepts the Phase 2D-29 runtime graph. No
dependency operation runs from either initializer.

## 7. Public Non-Sensitive Status Model

`AtlasVaultRuntimeStatus` should expose only stable categories such as:

- `locked`;
- `activating`;
- `locking`;
- `unlocked`;
- `saving`;
- `failed(AtlasVaultRuntimeFailure)`.

It must not contain associated vault IDs, keys, paths, record metadata,
payload types, private values, private record counts, underlying errors, or
operation details.

## 8. Private Session Boundary

`AtlasVaultActivationController` remains the sole owner of the canonical
wipeable session and installed private-state generation. The facade must not
retain an `AtlasVaultSession`, `AtlasVaultUnlockedSession`, raw key `Data`,
activated scope, or second hydrated-state copy.

Phase 2D-35 should add the smallest module-internal controller capability that
performs a synchronous session-bound operation or a complete controller-owned
save transaction. A short-lived `AtlasVaultUnlockedSession` may exist only
inside that capability. It must not escape an `await`, actor boundary, closure,
result, or stored property.

## 9. Explicit Activation Operation

`activate` accepts a validated non-semantic vault ID and either no key or an
explicitly supplied already-unwrapped 32-byte key. The facade delegates the
operation to `AtlasVaultActivationController`; it does not retrieve a key or
resolve a root itself.

The request must not conform to diagnostic protocols that reflect supplied key
bytes. A supplied key is passed through once and not retained by the facade.

## 10. Explicit Lock Operation

`lock` is always explicit, idempotent, and safe from every facade state. It
invalidates the current facade operation generation, delegates cancellation or
lock to the activation controller, waits for private-state clearing, and
publishes `locked` only after teardown completes.

## 11. Read-Private-State Operation

`privateState()` succeeds only for the currently installed unlocked
generation. It delegates to the activation controller's generation-checked
snapshot boundary and rechecks the facade operation generation after every
cross-actor await. A read begun before lock must not return private state after
lock wins.

`AtlasVaultPrivateStateSnapshot` may wrap the existing immutable hydrated state
for module-internal use. It must not be `Codable`, persisted, logged, embedded
in public status, or cached by the facade.

## 12. Mutation And Save Operation

`apply` accepts an `AtlasVaultRuntimeMutationRequest` containing a caller's
expected non-semantic vault ID and an `AtlasVaultMutationSet`. The expected ID
is used only for active-session matching and is never published in status or
diagnostics.

The controller-owned save transaction should:

1. verify the request matches the active session;
2. borrow a short-lived unlocked session;
3. use `AtlasVaultRecordSaving` to produce encrypted record envelopes;
4. atomically merge and persist those envelopes through the bound persistence
   coordinator;
5. derive the complete post-save private state without exposing plaintext to
   persistence services;
6. replace the installed private state only after encrypted persistence
   reports a committed result.

The transaction must not install optimistic private state before commit. A
pre-commit failure leaves the previous installed state unchanged. If disk
commit succeeds but post-commit private-state reconstruction fails, fail closed
by clearing and locking rather than representing stale memory as current.

## 13. Clear-Private-State Operation

There is no independent public clear that leaves a key/session unlocked.
Clearing private state is part of `lock`, activation failure cleanup,
cancellation, teardown, and fail-closed post-commit recovery. An internal test
seam may verify clearing but must not create an unlocked-without-state mode.

## 14. Runtime Composition Ownership

A production facade factory owns one side-effect-free
`AtlasVaultRuntimeServices` graph and constructs one activation environment and
controller. Shared stateless saver, hydrator, merger, and writer dependencies
come from that graph. Per-vault bound services are created only during explicit
activation.

## 15. Activation Controller Ownership

The facade owns or exclusively receives one activation controller. It delegates
activation, cancellation, lock, public activation state, private-state access,
and session-bound save capability. The facade must not duplicate the
controller's lifecycle state machine.

## 16. Private-State Store Ownership

The activation controller continues to own the private-state store and its
generation. The facade receives snapshots and save outcomes, never the store
itself. State replacement after save must be generation-scoped and atomic from
the perspective of facade callers.

## 17. Persistence Coordinator Ownership

The bound per-vault activation scope owns the applicable persistence
coordinator capability. The facade must not hold paths or construct a second
coordinator. Persistence receives only encrypted local-store and record
envelopes, never hydrated models or plaintext payloads.

## 18. Per-Vault Serialization

One facade represents at most one active vault. Activation and every save use a
single operation gate. Saves are serialized so a later mutation observes the
committed revision state of the earlier mutation. Multi-vault concurrency
requires separate facade instances and later policy review.

This is in-process serialization only. Cross-process compare-and-swap and file
locking remain production-readiness work.

## 19. Actor Isolation

The facade actor owns only non-sensitive status, an operation token, and
in-flight policy flags. The activation controller actor owns session lifetime
and private-state generation. No actor should retain a duplicate key or private
snapshot solely for convenience.

Every value crossing an actor boundary must satisfy strict concurrency and
`Sendable` requirements. Closures that borrow session capability remain
synchronous so key-bearing views cannot escape.

## 20. Cancellation

Cancellation is checked before activation or save side effects, between
asynchronous stages, and before installing state. Cancelling activation invokes
controller cancellation and leaves the facade locked. Cancelling a save before
the atomic commit starts leaves installed state unchanged.

Once a synchronous atomic commit has started, cancellation cannot claim the
write did not occur. The facade must wait for the writer's committed result,
then either install consistent state or lock and clear; it must not translate a
committed write into an ordinary cancellation failure.

## 21. Re-Entrant Operations

Actor re-entrancy must not permit overlapping activation, save, lock, or state
installation. Each operation captures a monotonically changing opaque token
and verifies it after every await. Stale continuations may clean up their own
provisional state but must not publish status or private data.

## 22. Operation Ordering

The initial policy is one mutating operation at a time:

- concurrent saves are rejected with a non-sensitive busy category rather
  than queued against stale revisions;
- repeated lock calls coalesce and remain idempotent;
- reads may proceed only while no lock transition is pending;
- activation and save never overlap.

The facade publishes `saving` before invoking saver or persistence and returns
to `unlocked` only after persistence and private-state replacement both
succeed.

## 23. Save While Locked

Saving while `locked`, `locking`, or `failed` fails before saver, persistence,
filesystem, Keychain, or private-store calls. The error is a stable `locked`
category with no request details.

## 24. Save During Activation

Saving while `activating` fails immediately with `operationInProgress`. It does
not cancel or otherwise change the activation attempt.

## 25. Lock During Save

Lock has priority. It invalidates the save token and requests teardown. If the
save has not begun commit, no encrypted write occurs. If commit is already
non-interruptible, the write result is allowed to settle, but no post-save
private state may be installed after the lock request. The controller clears
state and key ownership before lock returns.

The implementation and tests must distinguish pre-commit failure,
`committed`, and `committedDurabilityUnconfirmed` rather than inferring outcome
from task cancellation alone.

## 26. Activation During Save

Activation during save is rejected with `operationInProgress`; it does not
replace the active vault or cancel the save. A caller must explicitly lock and
wait for teardown before activating another vault.

## 27. Session And Vault-ID Matching

Activation validates the vault ID before key retrieval. Save compares the
request's expected vault ID with the controller-owned active vault inside the
redacted session boundary. A mismatch fails before saver or persistence calls
and exposes neither ID.

No path, status, error, debug description, or result may encode a vault ID,
saved job key, search name, or other semantic identifier.

## 28. Error Translation

`AtlasVaultRuntimeFacadeError` should contain stable categories such as
`locked`, `operationInProgress`, `invalidRequest`, `activationFailed`,
`sessionMismatch`, `conflict`, `saveFailed`, `privateStateUnavailable`, and
`cancelled`.

Activation's existing non-sensitive failure categories may be preserved as a
nested category. Saver, merger, persistence, and private-store errors must be
translated without retaining or reflecting the underlying error.

## 29. Non-Sensitive Public Errors

Descriptions and debug descriptions return category names only. They do not
include key bytes, vault IDs, paths, record IDs, revisions, payload types,
ciphertext, private values, counts, OS errors, or Keychain status codes.

`committed` and `committedDurabilityUnconfirmed` are successful
`AtlasVaultSaveOutcome` values, not errors. The outcome exposes no record count,
record type, ID, revision, or path.

## 30. Redaction And Debug Policy

Facade, request, snapshot wrapper, save outcome, and dependency-container
descriptions must use fixed redacted text or stable category names. Avoid
automatic reflection of stored closures and request values. The facade does not
log.

## 31. No Private Values In Public Status

Public status is lifecycle-only. It never includes saved-search names, search
text or filters, saved-job membership or keys, statuses, notes, snippets, draft
or generated-document references, record metadata, ciphertext, or key data.

## 32. No Private Record Counts In Public Status

Status and save outcomes do not expose counts by record type or total private
record count. A count can reveal private behavior even when payload values stay
encrypted. Any future aggregate metric requires a separate privacy review.

## 33. No Public Snapshot Mutation

The facade graph has no `AtlasPublicLocalSnapshot`, `AtlasLocalCache`, or public
cache dependency. Activation, private-state reads, saves, failures, lock, and
teardown cannot mutate public job data or infer saved membership into the
public cache.

## 34. No Filesystem Or Keychain Operation During Facade Construction

Constructing the facade may instantiate inactive adapters and closures only. It
must not call the root provider, load or save a key, create a per-vault path,
inspect or prepare a directory, read or write a store, hydrate a record, or
perform crypto. Construction tests must spy on every side-effect seam.

## 35. Construction Versus Activation

Construction establishes dependencies and initial `locked` status. Only an
explicit `activate` may validate a vault ID, retrieve a stored key, resolve the
root, load a store, decrypt records, and install private state. Only an
explicit `apply` may encrypt and atomically persist mutations. `status` and
`lock` do not activate a vault.

## 36. No SwiftUI Or App-Launch Integration

The facade is runtime-neutral. Phase 2D-35 must not add SwiftUI,
`ObservableObject`, property wrappers, environment objects, scene/app delegate
hooks, app entry points, `SearchViewModel`, `AtlasLocalCache`, UserDefaults,
networking, or platform lifecycle subscriptions.

## 37. Test Strategy

Phase 2D-35 should cover:

- side-effect-free construction and initial status;
- activation success, failure, cancellation, and already-active behavior;
- private-state availability only for the active generation;
- idempotent lock and read-versus-lock races;
- save rejection while locked or activating;
- expected-vault mismatch before saver or persistence calls;
- successful encrypted save and post-commit private-state consistency;
- pre-commit failure leaving installed state unchanged;
- committed-but-post-processing failure locking and clearing;
- serialized concurrent saves and activation/save rejection;
- lock during each save stage, including non-interruptible commit;
- committed durability-unconfirmed propagation;
- fixed redaction for status, requests, errors, outcomes, and facade;
- no key, private sentinel, path, record metadata, or private count leakage;
- no public snapshot mutation or `.atlasvault` artifact;
- source guards for UI, app-entry, cache, Keychain, filesystem, network, and
  global-singleton coupling.

Tests should use injected mocks and temporary roots only. The full Swift suite
must pass under strict concurrency.

## 38. Deferred Work

The facade does not implement migration, old-plaintext cleanup, cloud sync,
recovery UX, device onboarding, key rotation, cross-process coordination,
secure memory guarantees, app lifecycle policy, UI unlock prompts,
LocalAuthentication, app launch, or SwiftUI presentation. Those boundaries
require later design and review.

## 39. Recommended Phase 2D-35 Implementation

After this design is reviewed, Phase 2D-35 should implement the actor facade,
non-sensitive status/error/outcome types, dependency-injected activation and
save seams, and focused concurrency tests. Extend the activation controller or
its bound scope only where required to borrow the canonical session and commit
a generation-scoped private-state replacement; do not create a second key
owner or runtime call site.
