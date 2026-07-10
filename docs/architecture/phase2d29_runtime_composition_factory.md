# Phase 2D-29 AtlasVault Runtime Composition Factory

## Purpose

Phase 2D-29 adds an executable, side-effect-free construction boundary for the
isolated AtlasVault services designed in Phase 2D-28.

## Scope

This phase constructs dependency values and factories only. It adds no vault
activation, key retrieval, root lookup, filesystem operation, encrypted-store
read/write, record hydration, public-cache mutation, app-launch call site, or
SwiftUI integration. Migration, cloud sync, onboarding, recovery, and key
rotation remain deferred. This is not a production-readiness claim.

## Runtime Graph

`AtlasVaultRuntimeFactory.production()` constructs:

- an inactive `AtlasApplicationSupportVaultRootProvider`;
- an inactive `AtlasKeychainVaultKeyStore<SecItemAtlasKeychainClient>`;
- a directory preparer and local-store IO adapter;
- an atomic store writer;
- an encrypted local-store merger;
- a record saver and record hydrator;
- a per-vault service factory.

Constructing these values does not invoke their operational methods. The locked
graph contains no vault ID, vault key, session, store URL, encrypted record, or
hydrated private state.

## Dependency Injection

`AtlasVaultRuntimeFactory.makeServices` accepts every side-effect seam. Tests
can inject recording root, key-store, directory, local-store, atomic-writer,
merger, saver, and hydrator implementations without introducing global mutable
state.

The production overload also accepts root-locator, Keychain-client, and atomic
filesystem-client adapters so tests can prove that wrapping real production
types does not call those clients during construction.

## Per-Vault Factory

`AtlasVaultPerVaultServiceFactory.makeServices(rootURL:vaultID:)` is a separate
explicit pure construction step. It validates a caller-supplied non-semantic
vault ID, validates an explicit root URL, and creates a fresh vault-bound path
locator and persistence coordinator. The bound locator rejects a session for a
different vault before path or filesystem work. Construction does not query the
root provider, retrieve a key, compute a store URL, prepare a directory, read or
write a store, or hydrate records.

Directory preparation, local-store IO, atomic writing, merging, saving, and
hydration dependencies are intentionally shared when they are stateless or
injected service seams. Each per-vault scope receives a fresh locator and
coordinator value so mutable read-modify-write lifecycle state can remain
isolated in a future controller.

## Privacy And Diagnostics

Runtime, factory, and per-vault debug descriptions are fixed and redacted. They
do not reflect dependency descriptions, keys, vault IDs, root paths, private
sentinels, saved-search text, saved-job keys, record types, or payload values.

The graph has no dependency on `AtlasPublicLocalSnapshot`, `AtlasLocalCache`,
`SearchViewModel`, SwiftUI, app entry points, or networking. Public job cache
state remains outside the vault graph.

## Verification

Tests prove zero calls during production and injected construction, no created
files/directories, explicit-root per-vault construction, expected dependency
types and sharing, per-vault path isolation, Sendable conformance, fixed
redaction, unchanged public snapshots, and source-level runtime guards.

## Deferred

- activation and key-source selection;
- key/session lifetime and failure cleanup;
- encrypted-store load and hydration;
- private-state ownership;
- runtime saves;
- app launch and SwiftUI integration;
- migration, cloud sync, recovery, onboarding, and key rotation.
