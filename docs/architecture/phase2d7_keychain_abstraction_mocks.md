# Phase 2D-7 Key Store Abstraction And Mock Tests

Status: protocol and mock-test boundary only. This phase creates a small Swift
abstraction for future local vault-key storage and unlock-session behavior
without adding platform secure-storage code or runtime vault integration.

## Purpose

Phase 2D-7 follows the Keychain unlock design by defining the protocol boundary
that later platform storage can implement. It lets tests exercise locked,
unlocked, unavailable-key, and invalid-key behavior before any real platform
secret store is introduced.

## Scope

Included:

- `AtlasVaultKeyStore` protocol for loading, saving, and deleting local vault
  key bytes by vault ID.
- `AtlasVaultSession` as the process-local owner of an unlocked test session.
- `AtlasVaultUnlockState` and `AtlasVaultUnlockService` for pure state
  transitions.
- In-memory test doubles in Swift tests only.

Not included:

- `SecItem` calls;
- LocalAuthentication;
- biometric or device-passcode prompts;
- vault unlock UI;
- vault file read/write;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Proposed Protocols

`AtlasVaultKeyStore` is the storage abstraction:

- `loadVaultKey(for:)` returns optional caller-owned vault-key bytes.
- `saveVaultKey(_:for:)` stores caller-provided vault-key bytes.
- `deleteVaultKey(for:)` removes local unlock material.

The protocol deliberately says nothing about platform storage details. A future
platform implementation can conform to it after review. Phase 2D-7 tests use an
in-memory test double only.

`AtlasVaultUnlockService` is not a UI controller. It coordinates pure state
transitions from caller-provided key material or injected key-store output.

## Mock Implementation

Tests define an in-memory key store that:

- keeps fake `TEST_ONLY` keys in process memory only;
- records load/save/delete calls for assertions;
- performs no file writes;
- uses no platform secure storage;
- contains no production key material.

The mock exists only to prove protocol behavior and state transitions.

## Unlock Session State

Phase 2D-7 models:

- `locked`: no active unlocked session.
- `unlocking`: an unlock attempt is in progress.
- `unlocked`: a valid 32-byte vault key produced an in-memory session.
- `keyUnavailable`: the store returned no key or could not provide one.
- `invalidKey`: supplied key bytes were not the required vault-key length.
- `corruptVault`: reserved for later vault metadata or encrypted-record
  validation.

Corrupt-vault detection is deferred because this phase does not read vault
files, parse vault metadata, or decrypt records.

## Memory And Lifetime Rules

The raw vault key is held only by the unlocked session object in this phase.
Locking clears the session reference. The service and session must not log,
serialize, or persist vault keys, passphrases, recovery keys, decrypted payloads,
record plaintext, saved-search text, saved-job keys, notes, snippets, draft
metadata, or generated document references.

The service accepts caller-provided key bytes and validates length before
creating a session. Invalid or unavailable keys must not hydrate partial private
state.

## Future Platform Storage Boundary

A later platform implementation can conform to `AtlasVaultKeyStore`. That later
loop must separately review:

- platform access-control flags;
- user-presence policy;
- device-only versus syncable local secret behavior;
- recovery behavior when local unlock material is missing;
- integration or device tests for operating-system enforcement.

Those choices are intentionally deferred here.

## Tests

Phase 2D-7 tests cover:

- mock store save/load/delete behavior;
- unlock with a valid fake 32-byte key;
- invalid key rejection;
- lock clearing the in-memory session;
- deleted or unavailable keys leaving no session;
- public snapshot JSON remaining public-only after a mock unlock;
- source-level checks that the abstraction does not call platform storage,
  file I/O, UserDefaults, app cache, view-model, or API-client code.

No Python tests are required for this Swift-only abstraction phase unless shared
contract files or Python code change.
