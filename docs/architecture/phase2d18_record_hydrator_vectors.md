# Phase 2D-18 AtlasVault Record Hydrator Vectors

## Purpose

Phase 2D-18 adds a test-only record hydrator seam after the Phase 2D-17
hydration design. It proves that fake encrypted AtlasVault records can be
decrypted and decoded into in-memory private Swift state before any runtime app
wiring.

## Scope

- vector-driven Swift tests;
- in-memory hydrated state only;
- no SwiftUI, `SearchViewModel`, or `AtlasLocalCache` integration;
- no local store writes;
- no migration or cloud sync.

The hydrator accepts an already unlocked session. It does not obtain keys,
choose paths, read files, write files, or update app state.

## Hydrator Behavior

The hydrator:

1. accepts encrypted record envelopes and an unlocked session;
2. authenticates/decrypts records with `AtlasVaultRecordCrypto`;
3. decodes the common payload envelope;
4. dispatches by `AtlasVaultPayloadType`;
5. decodes the typed payload;
6. returns private in-memory state grouped by record type;
7. retains tombstone metadata separately and excludes tombstones from active
   state.

The first tests use existing fake plaintext payload vectors and seal them inside
Swift tests. This keeps shared vectors unchanged while still exercising the same
crypto and payload contracts.

## Privacy

Hydrated private payloads exist only in returned in-memory models. The hydrator:

- does not log private payloads;
- does not mutate public snapshots;
- does not read or write local store files;
- does not call Keychain, UserDefaults, app view models, or networking;
- returns non-sensitive error cases only.

## Error Behavior

The first implementation fails closed for:

- wrong key or authentication failure;
- malformed plaintext JSON;
- unknown record type;
- unsupported payload schema;
- unsupported encrypted record schema version;
- corrupt encrypted record envelopes.

Tombstone records are authenticated with the vault key, then retained only as
metadata. Their payloads are not decoded into active private state.

## Tests

Phase 2D-18 tests cover:

- hydration for all five record types;
- tombstones excluded from active state and retained separately;
- private sentinels appearing only after hydration into returned in-memory
  private models;
- wrong key, malformed payload, unknown type, unsupported schema, and unsupported
  record version errors;
- non-sensitive error string/debug output;
- source guards proving no runtime, public-cache, file-write, Keychain,
  UserDefaults, or networking dependencies.

## Deferred

- SwiftUI integration;
- public/private view-model wiring;
- save-after-hydration persistence;
- migration execution;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.
