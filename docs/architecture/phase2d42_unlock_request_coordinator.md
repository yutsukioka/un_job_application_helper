# Phase 2D-42 AtlasVault Unlock Request Coordinator

## Purpose

Phase 2D-42 implements the runtime-neutral request boundary designed in Phase
2D-41. It coordinates fake passphrase, recovery-key, local-key, and supplied
test-key inputs with injected derivation and activation seams. It does not add
an input UI or a production derivation implementation.

## Scope

The coordinator and its tests are isolated from SwiftUI, observable state, app
launch, LocalAuthentication, Keychain writes, filesystems, networking, public
cache mutation, migration, cloud sync, onboarding, and key rotation. Secret
input is held in memory only and is never encoded or persisted.

## Request And Buffer Boundary

`AtlasVaultUnlockRequest` is a non-Codable, single-use handle backed by shared
actor state. Copies therefore cannot dispatch the same input twice. A request
accepts exactly one `AtlasVaultUnlockInputSource`:

- a passphrase buffer;
- a recovery-key buffer;
- a non-secret local-key choice; or
- a supplied fake vault-key buffer for tests.

`AtlasVaultSecretBuffer` exposes only consuming byte transfer and idempotent
clearing. `AtlasVaultInMemorySecretBuffer` makes a byte-array copy on input so
it does not retain caller-owned `Data` storage, then clears that owned storage
on transfer, explicit cleanup, or deinitialization. Clearing is best effort:
Swift copy-on-write behavior, allocator behavior, and dependency-owned copies
prevent a formal memory-zeroization guarantee.

The public input enum exposes passphrase, recovery-key, and local-key choices.
The supplied fake vault-key constructor is module-internal, which also makes it
reachable to this module's `@testable` tests; it is not a public bypass around
derivation.

## Coordinator Behavior

`AtlasVaultUnlockRequestCoordinator` serializes request claims in an actor and
registers a non-secret pending-claim gate before awaiting request storage. This
keeps explicit and caller cancellation authoritative if storage has entered its
dispatching state but the coordinator has not yet installed the active
dispatch. The pending entry becomes the active entry in one actor-isolated
turn. The coordinator commits that state before the first dependency call. It
delegates passphrase and recovery-key processing to injected closures, rejects
an empty passphrase before derivation, validates the resulting fake key length,
and delegates activation through the existing
`AtlasVaultRuntimeActivationRequest` boundary. A local-key request passes no
supplied key and reveals no Keychain implementation detail.

Success, failure, cancellation, and timeout first remove coordinator-owned
secret references and commit their non-sensitive request state. A lock-backed
terminal gate arbitrates cancellation, expiration, and activation reservation
without awaiting an actor hop. The active dispatch retains the claimed buffer
only as a cleanup handle; caller cancellation, explicit cancellation, timeout,
and coordinator teardown detach that handle even when the operation task has
not begun consuming it.

Every caller-supplied buffer cleanup is best effort and runs in an independent
task after trusted state and input detachment. Pending cancellation, active
dispatch cancellation, expiration, success, and failure therefore do not await
a caller-supplied buffer's `clear()` implementation. A slow or non-returning
cleanup cannot delay the public result or keep the request registered as
active. Coordinator-owned byte copies are still wiped synchronously before the
operation returns or throws.
The child operation waits behind an internal start gate. Dispatch installs its
caller-cancellation handler, synchronously handles an already-cancelled caller,
and only then releases the child, so activation cannot outrun cancellation
handler registration.
Non-positive timeouts expire synchronously after the request is claimed and
before any operation task or activation can start.
Positive timeouts cover the whole dispatch. Caller and explicit cancellation,
as well as expiration, cancel an in-flight child after activation is reserved.
An injected activation seam that honors cancellation throws before committing,
so the corresponding `cancelled` or `expired` public result remains
authoritative. If activation ignores cancellation and returns only after it has
committed an unlocked session, that returned success is authoritative: the
coordinator promotes the terminal gate and request storage to completed rather
than reporting a failure while the runtime is unlocked. Once activation
completes, later cancellation or expiration cannot relabel that potentially
irreversible side effect. Dependencies remain responsible for clearing any
copies they own.

Only the coordinator that owns an active dispatch may cancel it. Another
coordinator can cancel the shared request while it is pending, but cannot
mutate dispatching storage without the owning operation and terminal gate.

## Privacy And Error Behavior

Input-source, request, buffer, coordinator, and error descriptions are fixed or
redacted. The public input enum never delegates description or reflection to an
associated caller-supplied secret buffer.
Underlying derivation and activation failures map to `unlockFailed`; invalid
request structure, reuse, cancellation, and expiration use stable,
non-sensitive cases. No error or debug description includes input source,
vault ID, secret length, bytes, dependency details, or private record values.
Injected dependencies cannot surface lifecycle-shaped request errors directly;
those values are normalized to `unlockFailed` unless coordinator-owned state
actually records cancellation or expiration.

The coordinator does not place unlock input in presentation snapshots or
modify `AtlasPublicLocalSnapshot`. It invokes no logging or analytics API.

## Tests

Fake-only tests cover construction, all four input sources, empty-passphrase
rejection, request-copy and concurrent single-use enforcement, cancellation
before and during the storage claim and active dispatch, pre-operation
cancellation and timeout cleanup, cancellation during activation for both
cancellation-honoring and cancellation-ignoring dependencies, pending and
active cancellation completion while caller cleanup is blocked, caller
cancellation, late dependency completion, cancellation before handler
installation, an already cancelled caller, timeout during activation, timeout
after activation return, non-owner cancellation, dependency error
normalization, success and failure cleanup, malicious buffer-description
redaction, non-persistability, actor serialization, no public snapshot
mutation, no filesystem artifacts, and source guards for UI, platform,
persistence, networking, and encoding coupling.

## Deferred

- production passphrase derivation and recovery-key parsing;
- actual secure input controls and prompts;
- observable presentation integration and SwiftUI views;
- app-host and app-launch integration;
- Keychain prompts and LocalAuthentication;
- retry, rate-limit, clipboard, keyboard, and screen-capture UX;
- migration, cloud sync, recovery UX, onboarding, and key rotation.

Phase 2D-43 audits the complete boundary before any SwiftUI integration phase.
