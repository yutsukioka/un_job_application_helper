# Phase 2D-31 AtlasVault Runtime Activation Controller

## Purpose

Phase 2D-31 implements the runtime-neutral Swift activation controller designed
in Phase 2D-30. It coordinates injected services under tests without adding an
app-launch, SwiftUI, view-model, cache, migration, or cloud call site.

## Scope

This phase adds an inactive controller seam, an injected activation environment,
a bound per-vault activation scope, and tests. It may explicitly load a fake or
temporary-root encrypted store and hydrate fake records when a test calls
`activate`. Merely constructing the environment or controller has no side
effect.

It does not add unlock UI, passphrase or recovery unwrapping, LocalAuthentication,
new-vault creation, runtime saves, migration, cloud sync, onboarding, recovery
UX, key rotation, or production-readiness claims.

## Controller Boundary

`AtlasVaultActivationController` is a Swift actor. Runtime-neutral means that it
is independent of application entry points, SwiftUI, `SearchViewModel`, and
`AtlasLocalCache`; it does not mean language-neutral. Actor isolation is the
required serialization mechanism for activation, cancellation, lock, and
state installation.

The public state projection contains only `locked`, `activating`, `unlocked`,
or a stable non-sensitive failure category. Duplicate and re-entrant activation
errors are per-call outcomes and do not replace the state of the active attempt
or installed session.

## Injected Environment

`AtlasVaultActivationEnvironment` injects stored-key loading, root resolution,
and per-vault scope construction. `runtimeServices(_:)` adapts the Phase 2D-29
composition graph without invoking any dependency during adapter construction.

`AtlasVaultActivationScope` binds encrypted-store loading and record hydration
to one validated vault ID. It does not expose a path, key, encrypted record, or
hydrated value through its description.

## Activation Sequence

1. Reject duplicate or re-entrant requests without mutating public state.
2. Publish `activating` and validate the non-semantic vault ID.
3. Use an explicitly supplied, already-unwrapped raw 32-byte vault key, or load
   the stored key only when explicit input is absent.
4. Create one canonical wipeable key owner.
5. Resolve the root and build a bound per-vault scope.
6. Load an existing encrypted local-store envelope.
7. Hydrate all encrypted records into provisional in-memory private state.
8. Install key ownership, scope, and hydrated state atomically, then publish
   `unlocked`.

No fallback to the stored key occurs after invalid or authentication-failing
explicit input. Missing store does not create a file or directory.

## Failure And Teardown

Invalid ID fails before key loading. Missing stored key, key-store failure, and
invalid key remain distinct. Persistence and hydration errors map to stable
authentication, corruption, unsupported-version, or vault-unavailable
categories without underlying details.

Every terminal post-key failure wipes/releases the provisional key owner and
installs no private state before publishing `failed`. Cancellation and explicit
lock publish `locked`. Attempt tokens reject late results. Synchronous load and
hydrate operations are not interruptible; cancellation is checked before their
result can be installed.

`AtlasVaultSession` remains the canonical long-lived key owner. The existing
`AtlasVaultUnlockedSession` is created only as a short-lived synchronous view
for persistence and crypto helpers. Swift `Data` value semantics still prevent
a complete historical zeroization guarantee.

## Privacy Boundaries

The controller does not log. Public state, errors, descriptions, and debug
descriptions contain no vault ID, key, path, record metadata, private count, or
payload value. Hydrated state remains private to the actor and is never written,
serialized into `AtlasPublicLocalSnapshot`, or passed to public cache code.

## Verification

Tests cover side-effect-free construction, key-source priority, failure
taxonomy, ordered activation, missing store, atomic success, lock, cancellation,
late-result rejection, concurrency, re-entry, exact key-release behavior,
redaction, public-snapshot immutability, temporary-root artifacts, and source
guards. The runtime-services adapter receives an integration test under a
temporary root.

## Deferred

- a dedicated in-memory private-state store (Phase 2D-32 design and 2D-33 implementation)
- app-launch and SwiftUI integration
- unlock prompts, LocalAuthentication, and biometrics
- passphrase/recovery unwrapping and key persistence policy
- runtime private-state mutation and saves
- migration and old-plaintext cleanup
- cloud sync, conflict UI, onboarding, recovery UX, and key rotation
- production security review
