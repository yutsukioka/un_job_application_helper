# Phase 2D-16 AtlasVault Persistence Coordinator Mocks

Status: protocol, coordinator seam, and temp-path tests only. This phase adds a
testable persistence coordinator boundary for unlocked vault sessions before any
runtime app wiring.

## Purpose

Phase 2D-16 implements the smallest coordinator seam after the Phase 2D-15
design. It proves that an unlocked vault session can be passed to a persistence
coordinator that uses injected filesystem seams to load and save encrypted local
store envelopes.

This phase remains below app runtime integration. It does not hydrate SwiftUI
state, connect to `SearchViewModel`, call `AtlasLocalCache`, or choose app
storage roots.

## Scope

Included:

- dependency-injected persistence coordinator;
- explicit temp roots and mocks in tests;
- encrypted local-store load/save coordination;
- overwrite behavior tests;
- source guards for runtime boundary violations.

Excluded:

- SwiftUI, `SearchViewModel`, or `AtlasLocalCache` integration;
- automatic Application Support lookup;
- passphrase prompts or key unwrapping;
- Keychain unlock flow wiring;
- record hydration or plaintext payload decryption;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync.

## Proposed Types

Phase 2D-16 introduces:

- `AtlasVaultUnlockedSession`: testable unlocked-session input with random
  `vaultID` and 32-byte vault key, redacted in string/debug output.
- `AtlasVaultPersistenceCoordinating`: protocol for loading and saving encrypted
  local-store envelopes for an unlocked session.
- `AtlasVaultPersistenceCoordinator`: default coordinator implementation.
- `AtlasVaultPersistenceEnvironment`: dependency container for path locator,
  directory preparer, and local-store IO.
- `AtlasVaultPersistenceError`: non-sensitive error enum.

The local-store IO dependency is explicit so tests can use real temp files or
fake behavior without wiring runtime app state.

## Responsibilities

The coordinator may:

- compute the encrypted local-store file URL from the session vault ID;
- load an encrypted local-store envelope when the file exists;
- return `nil` when no store exists yet;
- prepare the parent directory before saving;
- save encrypted local-store JSON with explicit overwrite behavior;
- surface non-sensitive errors.

The coordinator must:

- keep decrypted payloads in memory only;
- avoid record decryption in this phase;
- avoid mutating public snapshots;
- avoid writing private state outside the encrypted local-store envelope;
- preserve existing local-store validation and overwrite contracts.

## Explicit Non-Responsibilities

The coordinator does not:

- obtain passphrases or recovery keys;
- unwrap vault keys;
- call Keychain or implement unlock UX;
- choose Application Support or app container roots;
- hydrate SwiftUI state;
- read or write public cache snapshots;
- execute migration;
- upload or sync cloud state.

## Tests

Tests cover:

- invalid session key length;
- missing encrypted store;
- encrypted store save/load using temporary roots;
- parent directory creation through the preparer;
- overwrite refusal and explicit overwrite success;
- corrupt store failure;
- structurally valid but undecryptable records passing through unchanged;
- absence of private sentinels and plaintext record type strings;
- source guard against Keychain, UserDefaults, Application Support lookup,
  runtime view models, app cache, and networking;
- no `.atlasvault` artifacts or `private/` writes;
- redacted session string/debug output.

## Deferred

- runtime app wiring;
- atomic-write coordinator policy;
- record hydration and private model mapping;
- Keychain user-presence policy;
- UI hydration and locked-state presentation;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.
