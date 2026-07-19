# AtlasVault Phase 2D-52 Locked-Shell Unlock Flow

## Purpose

Phase 2D-52 adds a thin, unwired composition of the dedicated public-only
locked shell and the explicit unlock panel. It proves that public search stays
available while locked and that an explicit fake local-key request can pass
through the existing test host without exposing private state in this flow.

## Phase Scope

The phase adds a pure flow-state projection, a SwiftUI switch over two merged
views, focused tests, and this architecture record. It does not add production
host wiring, navigation, providers, cryptography, persistence, private
rendering, save handling, migration, or cloud behavior.

## Reconstructed 2D-51 Baseline

Phase 2D-51 merged the capability-driven `AtlasExplicitUnlockView`, its pure
view state, and tests. All review threads were resolved, checks passed, and the
merged focused suite passed before Phase 2D-52 work began.

## Existing Phase 2D-46 Host

Tests instantiate `AtlasVaultTestHost` directly. The phase does not create,
copy, or modify another host. A small test-local coordinator adapter delegates
an unlock request to that host and delegates cancellation through the host's
existing lock operation.

## Existing Endpoint Recorder

`AtlasVaultTestEndpointCallRecorder` remains the sole authority for endpoint
categories. Tests distinguish public search, saved-search compatibility,
tracker compatibility, and private-sidebar refresh without recording content.

## Existing Public-Search Fake

`AtlasVaultFakePublicJobSearchService` remains the sole public-search fake. It
uses the existing endpoint recorder and shared test public-state store.

## Thin Composition Boundary

`AtlasLockedShellUnlockFlowView` receives immutable flow state plus the merged
locked-shell and unlock-panel action values. It switches between existing
views and does not own a controller, runtime, host, provider, service, or
secret.

## Pure Flow-State Model

`AtlasLockedShellUnlockFlowState` contains one public locked-shell model, one
non-sensitive mode, and an optional exact Phase 2D-51 view state. It derives
the mode from owner-supplied panel presentation and the exact merged unlock
presentation status.

## Locked-Public Mode

When the owner has not requested the panel, nonterminal unlock status projects
`lockedPublic`. Cancelled status also returns to this mode. The corresponding
view is the merged `AtlasLockedPublicShellView`.

## Unlock-Panel Mode

An explicit owner request projects the panel for locked, ready,
method-unavailable, activating, generic failed, timed-out, and
host-reconciliation-required statuses. The exact Phase 2D-51 view state and
actions are forwarded.

## Unlocked-Transition Mode

Unlocked status projects only a fixed transition. The state carries no
hydrated data, private model, private count, key, path, or encrypted record.
The view renders fixed non-sensitive text while a future host decides what
comes next.

## Production Local-Key-Only Capability

`AtlasVaultUnlockCapabilities.currentProduction` currently exposes only local
key. The flow does not recalculate or broaden this capability snapshot.

## Hidden Passphrase And Recovery Behavior

Passphrase and recovery remain absent under current production capabilities.
Tests may forward fake capability snapshots to verify projection, but this
does not advertise production provider support.

## No Raw Test-Key UI

The module-internal supplied test-key request is not a presentation method and
does not appear in flow state or SwiftUI.

## Explicit Unlock Request

The locked shell exposes only its injected request action. The owner must
explicitly change panel-presentation state. Opening the panel itself performs
no activation.

## No Automatic Unlock

Construction and rendering invoke no selection, submission, cancellation,
host call, or activation. Every attempt begins with an explicit action in the
merged components.

## Public Search While Locked

The locked shell's injected search action may call the existing host public
search boundary. Tests prove that the public category is called and the public
state is read without replacement.

## Zero Private Compatibility Calls

Locked public search and explicit local-key integration record zero
saved-search compatibility, tracker compatibility, and private-sidebar
refresh calls.

## Existing-Host Local-Key Test Path

A test-local, secret-free coordinator adapter delegates the presentation
controller's local-key request to `AtlasVaultTestHost`. The host keeps its
existing coordinator, lifecycle, runtime, and presentation responsibilities.
No host logic is duplicated in production source.

## Cancellation

The merged unlock controller supplies cancelled status. The pure flow
projection returns to the public locked mode. The flow adds no new
cancellation semantics, retry, or activation.

## Host Reconciliation

Host-reconciliation-required remains an unlock-panel status with the merged
non-interactive, non-sensitive view behavior. The flow does not downgrade or
reinterpret it.

## No Secret Ownership In Flow

Flow state and the composition view contain no secret or secret buffer. The
Phase 2D-51 panel remains the sole local owner of ephemeral input.

## No Private-State Ownership In Flow

The state has no private projection. The test host may hydrate fake private
state under its existing integration contract, but this flow neither receives
nor renders it.

## No Provider Integration

No production passphrase or recovery provider is connected. Test-only fake
capabilities are projection inputs only.

## No Cryptography

The phase performs no key derivation, key unwrap, encryption, decryption, or
record-crypto operation.

## No Direct Keychain

Neither new production file accesses Keychain or `SecItem`. Local-key dispatch
remains behind the existing request, host, and runtime boundaries.

## No Filesystem Or Network In New Production Source

The pure state and view perform no file access, directory resolution, network
request, API-client call, or compatibility-endpoint call.

## No Private Rendering

The SwiftUI flow renders no saved search, saved job, tracker state, note,
snippet, draft, generated-document reference, or other decrypted private
model.

## No Save-Outcome Behavior

Save results, save failures, durability warnings, fatal containment, and
mutations are outside this phase.

## No AtlasRootView

The flow uses the dedicated public shell and never reuses the legacy root.

## No refreshSidebarData

The legacy private sidebar refresh path is absent. Locked-state endpoint spies
enforce the boundary.

## No App Entry Or Navigation

No app launch, app entry point, production navigation, scene, or window source
is modified. The new view remains unwired.

## TDD Evidence

The test perspective was written before repository implementation. The first
valid focused compile failed because the Phase 2D-52 state and view types were
absent. That test-only red commit was pushed before the implementation.

## Test Coverage

Focused tests cover all flow modes, exact status forwarding, production and
fake capability projection, redacted state, construction side effects, source
guards, direct host reuse, public search, endpoint counts, explicit local-key
activation, cancellation, artifacts, and scope.

## Deferred Production Integration

Production host composition, real navigation, app entry, private rendering,
save presentation, passphrase and recovery providers, LocalAuthentication,
migration, plaintext cleanup, cloud sync, onboarding, recovery UX, and key
rotation remain deferred.

## Next Product Gate

Phase 2D-52 completes only this thin, unwired integration. The next product
gate is reviewed production-host composition toward a real explicit local-key
user journey. That work must reuse the existing runtime facade, lifecycle,
observable presentation, locked shell, unlock panel, and local-key capability,
and it must begin with design and review before modifying an app entry point.
Passphrase and recovery remain hidden until reviewed production providers
exist. Private rendering, migration, and cloud sync remain separate later
gates. No later phase is created or implemented here.
