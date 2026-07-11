# Phase 2D-32 AtlasVault In-Memory Private-State Store Design

## 1. Purpose

Phase 2D-32 designs the in-memory owner for private AtlasVault state produced by
record hydration. It defines how Phase 2D-33 can install, read, and clear that
state through the runtime-neutral activation controller before any UI exposure.

## 2. Explicit Design-Only Scope

This phase adds documentation only. It adds no state-store implementation,
activation-controller change, app-launch call site, SwiftUI binding,
`SearchViewModel` or `AtlasLocalCache` integration, persistence write, migration,
cloud sync, onboarding, recovery flow, key rotation, or production-readiness
claim.

## 3. Existing Boundary

`AtlasVaultRecordHydrator` returns `AtlasVaultHydratedState`, containing saved
searches, saved jobs, application notes, profile snippets, draft metadata, and
tombstones. Phase 2D-31 currently retains that value inside its private
activated-session object. The activation controller separately owns the
wipeable `AtlasVaultSession`, bound persistence scope, and non-sensitive public
activation state.

The public snapshot remains a separate public-job cache. It is not an input to
or output from the private-state store.

## 4. Proposed Store Boundary

Phase 2D-33 should introduce an actor-isolated
`AtlasVaultPrivateStateStore` behind a narrow
`AtlasVaultPrivateStateStoring` protocol. Better names may be used if the
implementation reveals an established local convention.

The store owns hydrated private models only. It must not own or retrieve a vault
key, unlock a vault, resolve a path, read or write a local store, encrypt or
decrypt records, call Keychain, or mutate activation state. The activation
controller remains the sole lifecycle coordinator.

## 5. Internal State Machine

The first implementation should model three internal states:

- `empty`: no hydrated private value;
- `staged(generation, state)`: a complete hydration result awaiting activation
  commit and unavailable to readers;
- `active(generation, state)`: the committed in-memory private value for the
  unlocked activation.

The generation is an opaque, non-semantic attempt token. It must not contain a
vault ID, path, record ID, user value, or timestamp with user meaning. Generation
mismatches fail with stable non-sensitive errors.

## 6. Staging And Commit

The controller should retain the hydration result provisionally, ask the store
to stage it for the current activation generation, recheck cancellation, and
then commit that same generation. A staged value must never be returned by a
snapshot/read API.

After the store commit succeeds, the controller may install key ownership and
the bound scope and publish `unlocked`. Future consumers must access private
state through a controller-gated capability or projection that is issued only
after `unlocked`; they must not observe the store actor directly. This prevents
the unavoidable cross-actor commit interval from becoming a UI-visible state.

If cancellation or failure wins at any point, the controller clears that
generation before publishing its terminal state. A stale attempt may clear only
its own staged generation and must never clear a newer active generation.

## 7. State Lifetime

The store begins empty. A committed private state lives only for the matching
unlocked controller session. It survives ordinary in-memory reads and future
private edits while that session remains unlocked.

It does not survive process termination, controller teardown, explicit lock,
vault switch, cancellation, failed activation, or replacement by a later
generation. No background restoration is implied.

## 8. Clearing And Teardown

Provide generation-scoped discard for provisional work and an unconditional
`clearAll()` lifecycle operation for lock/controller teardown. Clearing must
replace all arrays, maps, tombstones, and retained projections with empty values
before the controller publishes `locked` or a post-install failure.

Repeated clear operations are idempotent. Lock during staging clears the staged
value; lock after commit clears the active value. A failed stale attempt cannot
clear a different generation.

Swift value semantics, copy-on-write storage, optimizer behavior, and prior
temporary copies prevent a complete zeroization guarantee. Clearing is
best-effort lifetime control, not a claim that every historical byte is erased.

## 9. Actor Isolation And Concurrency

All mutable store state belongs to one actor. Stage, commit, snapshot, and clear
methods should perform no external await while private state is installed or
being transitioned, avoiding actor reentrancy inside invariants.

The activation controller may await store operations only at explicit attempt
checkpoints. Concurrent snapshots serialize through the store. Concurrent
activation, cancellation, and lock remain serialized by the activation
controller and use generation checks when crossing into the store actor.

No global mutable singleton or task-local private state is allowed.

## 10. Read And Snapshot Semantics

The initial read seam should be internal to the AtlasUI module and return an
immutable value copy only for the active generation. Empty, staged, stale, or
locked access returns no value or a stable non-sensitive error according to the
Phase 2D-33 test contract.

Do not expose the full `AtlasVaultHydratedState` as an unrestricted public UI
model. It contains record IDs, revisions, saved membership, notes, snippets,
draft references, and other private values. Tombstones remain available to
future save/merge code but are not part of active UI collections.

## 11. Mutation Boundary

Phase 2D-33 should implement lifecycle installation and clearing, not general UI
editing. Later private mutations should be actor-isolated, preserve record
metadata and tombstones, and produce `AtlasVaultMutationSet` values for the
existing encrypted record saver.

Mutation must not directly write files, update the public snapshot, or bypass
the persistence coordinator. Save-after-edit and conflict behavior remain
separate reviewed work.

## 12. Redaction And Diagnostics

Store, protocol adapter, generation, state, error, and debug descriptions must
be constant or category-only. They must not include:

- vault IDs, keys, paths, record IDs, revisions, or key IDs;
- private counts or record-type membership;
- saved-search names, text, or filters;
- job keys, saved membership, statuses, or notes;
- profile snippets, draft metadata, or generated-document references;
- payload JSON or hydrated values.

The store must not log. Tests and failure messages may name fake sentinel
categories but must never interpolate a hydrated state or payload value.

## 13. Public Snapshot Boundary

The store has no dependency on `AtlasPublicLocalSnapshot`, `AtlasLocalCache`,
or public search/detail cache types. Installing, reading, mutating, or clearing
private state must leave the encoded public snapshot byte-for-byte unchanged.

No private count, saved-only job key, record ID, revision, note, snippet, draft,
or document reference may be copied into public cache metadata. Public cache
warmup must not infer saved membership.

## 14. Future UI Exposure

A later reviewed phase may add a `@MainActor` bridge that requests narrow,
immutable, purpose-specific projections only while activation is unlocked. The
bridge should expose locked/loading/error placeholders independently of private
payload values and drop every projection immediately on lock or session change.

SwiftUI views, `SearchViewModel`, and `AtlasLocalCache` must not receive the
store actor, vault session, record metadata, tombstones, or full hydrated state.
No UI integration belongs in Phase 2D-33.

## 15. Error Policy

Recommended store errors are category-only: invalid generation, no staged
state, stale generation, and unavailable/locked state. Errors must be
`Sendable`, equatable where useful for tests, and redacted in string/debug
output.

Store errors do not wrap decoder, crypto, filesystem, Keychain, or payload
errors. Those belong to the activation and persistence boundaries that produced
the complete hydration result.

## 16. Phase 2D-33 Test Plan

- construction is empty and side-effect-free;
- staged state is inaccessible until commit;
- matching generation commits a complete hydrated state;
- stale generation cannot commit or clear a newer generation;
- activation success installs state before publishing usable unlocked access;
- wrong key, corrupt store, missing store, unsupported version, and path failure
  install no state;
- cancellation during hydration/staging leaves the store empty;
- lock clears active state and is idempotent;
- controller teardown releases private state;
- reactivation cannot expose a prior vault generation;
- actor-concurrent reads and clears preserve state-machine invariants;
- store/errors/descriptions contain no fake private sentinel;
- no public snapshot mutation occurs;
- no file, Keychain, network, app-launch, SwiftUI, view-model, or cache call site
  is added.

Tests use fake hydrated values and mocks only. Runtime-adapter tests may use a
canonical temporary root but must create no committed `.atlasvault` artifact.

## 17. Deferred

- private-state store implementation and controller integration (Phase 2D-33)
- public/private view-model or SwiftUI exposure
- runtime private editing and save orchestration
- app-launch activation and unlock UI
- LocalAuthentication and biometrics
- migration execution and old-plaintext cleanup
- cloud sync, conflict UI, onboarding, recovery UX, and key rotation
- production security and memory-hardening review

## 18. Recommended Next Phase

After exact-head review and merge of this design, Phase 2D-33 should implement
the actor-isolated private-state store and integrate it with the activation
controller under mocks and tests only. It must remain disconnected from app
launch, SwiftUI, `SearchViewModel`, and `AtlasLocalCache`.
