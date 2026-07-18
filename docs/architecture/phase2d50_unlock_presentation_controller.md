# Phase 2D-50 AtlasVault Unlock Presentation Controller

## Purpose

Phase 2D-50 adds a runtime-neutral controller between future unlock
presentation and the existing single-use unlock request coordinator. It
projects immutable method capabilities, validates selection and submission,
owns one controller-local request at a time, and publishes only sanitized
state.

## Scope

This phase adds:

- `AtlasVaultUnlockPresentationControlling`;
- `AtlasVaultUnlockPresentationController`;
- non-sensitive presentation status and state;
- local-key, passphrase, and recovery submission values;
- fake, coordinator-driven tests.

It adds no SwiftUI, observable state, runtime facade dependency, direct
activation, key unwrapping, cryptography, Keychain access, filesystem access,
networking, public-cache access, private endpoint access, persistence, save
handling, app-host wiring, migration, or cloud behavior.

## Existing Boundaries

`AtlasVaultUnlockCapabilities` is immutable and non-sensitive. Its production
snapshot currently advertises only the reviewed local-key path. Passphrase and
recovery may be marked available in tests through explicit fake provider
presence, but the controller neither retains nor calls those providers.

`AtlasVaultUnlockRequestCoordinator` remains responsible for request
single-use enforcement, secret consumption, injected derivation, and
activation dispatch. The controller constructs only the coordinator's public
local-key, passphrase-buffer, and recovery-buffer inputs.

## Presentation State

`AtlasVaultUnlockPresentationState` contains only:

- the exact immutable capability snapshot;
- an available selected method, if any;
- a fixed presentation status.

Statuses are locked, ready, method unavailable, activating, unlocked, generic
failure, cancelled, timed out, and host reconciliation required. State and
submission types are `Sendable`, are not `Codable`, and have fixed redacted
descriptions. They contain no vault ID, input text, secret buffer, request,
wrapped-key metadata, key, path, private state, or failure detail.

## Selection And Submission

Unavailable methods cannot be selected. Submission requires both current
availability and an exact selected-method match. Rejected, mismatched, or
concurrent duplicate secret submissions are cleared without taking their
bytes and never reach the coordinator.

An accepted submission creates one `AtlasVaultUnlockRequest` with the
controller's non-semantic vault ID. The controller retains that request only
while it is needed for cancellation. It does not construct or expose a raw-key
request.

## Secret Ownership

The submitted one-shot buffer passes into the existing request. The
coordinator owns consumption and activation transfer. The controller performs
an idempotent clear after dispatch terminates and releases its request
reference before publishing terminal state.

Swift and `Data` do not provide a universal memory-zeroization guarantee.
This boundary minimizes ownership and tests observable clearing and release;
it makes no stronger platform-memory claim.

## Controller-Local Serialization

The actor admits one active submission at a time. A second submission is
rejected and any supplied secret is cleared before coordinator dispatch.

This is deliberately not process-global or multi-window admission. A future
app host must separately coordinate multiple presentation owners before
production integration.

## Authorization And Late Completion

Every accepted attempt receives a controller-local authorization generation.
Cancel, method change, disappearance, timeout, or host-lock notification
invalidates that generation. A completion from an invalidated generation
cannot publish unlocked state.

Cancellation delegates to `AtlasVaultUnlockRequestCoordinating.cancel`. Its
Boolean result is not a terminal activation outcome: `true` does not guarantee
that an activation dependency honored cancellation, while `false` can mean
that a failed or expired dispatch already left the coordinator's active set.
The controller therefore blocks new selection and submission until the
invalidated dispatch itself finishes. A failed dispatch retains the requested
cancelled, ready, or locked presentation state. A successful dispatch publishes
`hostReconciliationRequired` instead of unlocked state. A future host must
reconcile authoritative runtime state and perform any required lock; this
controller does not call the runtime facade.

`hostDidLock()` is a notification that the host lock boundary has been
applied. It clears selection, publishes locked state, and attempts request
cancellation. The invalidated attempt remains tracked until dispatch
termination because activation could still commit after the host lock. A late
failure retains locked state; a late success requires another host
reconciliation and cannot publish unlocked presentation.

## Failure Behavior

The coordinator exposes only fixed request errors. The controller maps:

- cancellation to cancelled;
- expiry to timed out;
- `invalidRequest`, `alreadyUsed`, and `unlockFailed` to one generic failed
  status;
- unexpected non-`AtlasVaultUnlockRequestError` throws to that same generic
  failed status.

It does not distinguish wrong secret, missing local key, corrupt vault, path
failure, or provider failure. This avoids a presentation oracle and reflects
the information available from the existing coordinator.

## Capability Limitations

Production passphrase and recovery remain unavailable. Phase 2D-49 provides
wrapped-key models and a context-aware protocol, not a reviewed production
provider or authenticated v1 vault binding. This phase does not connect that
provider boundary to the older coordinator derivation closures.

The supplied fake key path remains internal to existing tests and is absent
from the presentation method and submission models.

## Tests

Fake tests cover:

- side-effect-free construction and exact capability projection;
- unavailable and mismatched submission rejection before secret consumption;
- local-key and explicitly fake passphrase/recovery dispatch;
- raw-key absence and redacted, non-Codable state;
- one controller-local in-flight request;
- success and generic failure;
- timeout and explicit cancellation;
- cancellation losing to committed success;
- method-change, disappearance, and host-lock invalidation;
- secret clearing and request ownership release;
- no forbidden dependency, public snapshot mutation, or `.atlasvault`
  artifact.

## Deferred

- Production passphrase and recovery providers.
- Authenticated vault binding or key confirmation.
- Process-global and multi-window unlock admission.
- Runtime-state reconciliation and host lock commands.
- SwiftUI and observable presentation.
- Save failures, durability warnings, mutations, and private rendering.
- Production app entry and navigation integration.
- LocalAuthentication, migration, plaintext cleanup, cloud sync, recovery UX,
  onboarding, key rotation, and production-readiness review.
