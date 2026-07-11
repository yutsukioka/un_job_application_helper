# Phase 2D-33 AtlasVault In-Memory Private-State Store

## Purpose

Phase 2D-33 implements the actor-isolated private-state store designed in Phase
2D-32 and integrates its lifecycle with the runtime-neutral activation
controller under tests.

## Scope

This phase adds an in-memory store, activation-controller lifecycle calls, and
tests. It adds no app-launch call site, SwiftUI binding, `SearchViewModel` or
`AtlasLocalCache` integration, private-state editing, persistence write,
migration, cloud sync, onboarding, recovery flow, key rotation, or
production-readiness claim.

## Store Boundary

`AtlasVaultPrivateStateStore` is an internal actor behind the
`AtlasVaultPrivateStateStoring` protocol. It owns only complete
`AtlasVaultHydratedState` values. It has no dependency on vault keys, paths,
Keychain, filesystem services, crypto, public snapshots, networking, or UI
types.

`AtlasVaultPrivateStateGeneration` wraps a random, non-semantic token. Its
description is redacted and it contains no vault ID, path, record metadata, or
private payload value.

## State Machine

The store starts `empty`. `stage` installs one complete value for one generation
without making it readable. `commit` transitions that matching staged value to
`active`; only a matching active generation may request an immutable snapshot.

A duplicate stage, a commit without staged state, and a stale generation fail
with stable category-only errors. A generation-scoped clear cannot remove a
different generation. `clearAll` is idempotent.

## Activation Integration

Each activation attempt creates a fresh private-state generation. After record
hydration, the controller stages the complete value, checks cancellation,
commits it, and rechecks the attempt and generation after the cross-actor commit
await. It installs key ownership and the bound persistence scope and publishes
`unlocked` only after that final check, with no intervening await.

The activated session retains the key owner, bound scope, and private-state
generation. It no longer embeds a second hydrated-state copy. The module-internal
snapshot seam is available only for the generation of an unlocked session.

## Cancellation And Lock Races

Cancellation and lock invalidate the attempt before awaiting store cleanup. If
either operation runs while stage or commit is suspended, the activation path
observes the invalid attempt, clears its generation again, and returns
`cancelled` without publishing `unlocked`.

Failure cleanup keeps its attempt identity across the store-clear await so a
concurrent cancellation or lock can win. The stale failure path cannot overwrite
the resulting `locked` state.

## Lifetime And Clearing

Activation failures clear only their attempt generation. Explicit lock clears
all stored private state before publishing `locked`, wipes the installed key
owner, and is idempotent. Controller teardown schedules `clearAll` on the
injected store; controller-owned stores are also released with the controller.

Clearing drops the store's retained value. Swift value semantics, copy-on-write
storage, optimizer behavior, and prior temporary copies still prevent a claim
of complete historical byte zeroization.

## Privacy And Diagnostics

The store does not log and does not serialize hydrated state. Store, generation,
and error descriptions are constant or category-only. They expose no private
counts, record types, record IDs, revisions, saved-search values, job keys,
notes, snippets, draft references, keys, paths, or payload JSON.

The store has no public-snapshot or cache dependency. Activation, snapshot,
clear, lock, and teardown leave public job-cache state independent.

## Verification

Focused tests cover empty construction, staging visibility, matching commit,
duplicate and stale generations, generation-scoped clear, idempotent clear-all,
concurrent snapshots and clear, and redacted diagnostics.

Controller tests cover successful installation, lock, controller teardown,
reactivation isolation, cancellation during commit, lock during commit, no
late `unlocked` publication, public-snapshot immutability, and the existing
failure and runtime-neutral source guards.

## Deferred

- app-launch and SwiftUI integration
- `SearchViewModel` and `AtlasLocalCache` private-state integration
- purpose-specific UI projections
- private-state mutation and encrypted save orchestration
- unlock prompts, LocalAuthentication, and biometrics
- migration execution and old-plaintext cleanup
- cloud sync, conflict UI, onboarding, recovery UX, and key rotation
- production security and memory-hardening review

## Recommended Next Phase

Review and merge this implementation before designing any UI-facing projection.
A Phase 2D-34 placeholder may be created after the mega-loop, but this phase
does not define or implement Phase 2D-34 behavior.
