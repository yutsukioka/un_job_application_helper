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
commits the dispatching state before the first dependency call. It delegates
passphrase and recovery-key processing to injected closures, validates the
resulting fake key length, and delegates activation through the existing
`AtlasVaultRuntimeActivationRequest` boundary. A local-key request passes no
supplied key and reveals no Keychain implementation detail.

Success, failure, cancellation, and timeout remove coordinator-owned secret
references. A lock-backed terminal gate makes cancellation, expiration, and
activation reservation mutually exclusive without awaiting an actor hop.
The active dispatch retains the claimed buffer only as a cleanup handle;
caller cancellation, explicit cancellation, timeout, and coordinator teardown
clear that handle even when the operation task has not begun consuming it.
Non-positive timeouts expire synchronously after the request is claimed and
before any operation task or activation can start.
Positive timeouts cover the whole dispatch. Expiration may cancel an in-flight
activation until that activation atomically completes; the injected activation
seam must honor cancellation before committing an unlocked session, as the
runtime facade does. Cancellation that reserves the gate before activation
prevents a late dependency completion from activating the vault. Once
activation completes, later cancellation or expiration cannot relabel that
potentially irreversible side effect. Dependencies remain responsible for
clearing any copies they own.

Only the coordinator that owns an active dispatch may cancel it. Another
coordinator can cancel the shared request while it is pending, but cannot
mutate dispatching storage without the owning operation and terminal gate.

## Privacy And Error Behavior

Request, buffer, coordinator, and error descriptions are fixed or redacted.
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

Fake-only tests cover construction, all four input sources, request-copy and
concurrent single-use enforcement, cancellation before and during dispatch,
pre-operation cancellation and timeout cleanup, caller cancellation, late
dependency completion, timeout during activation, non-owner cancellation,
dependency error normalization, success and failure cleanup, redacted
descriptions, non-persistability, actor serialization, no public snapshot
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
