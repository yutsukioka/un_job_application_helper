# Phase 2D-20 AtlasVault Record Saver Vectors

## Purpose

Phase 2D-20 adds a test-only record saver seam after the Phase 2D-19
save-after-hydration design. It proves that fake private in-memory changes can
be encoded into AtlasVault payload envelopes and encrypted into record envelopes
before any runtime app wiring.

## Scope

- vector-driven Swift tests;
- in-memory mutation and encrypted-record output only;
- no SwiftUI, `SearchViewModel`, or `AtlasLocalCache` integration;
- no local store writes;
- no migration or cloud sync.

The saver accepts an already unlocked session. It does not obtain keys, choose
paths, read files, write files, update public snapshots, or update app state.

## Saver Behavior

The saver:

1. accepts an explicit mutation set;
2. encodes canonical payload envelopes for create and update mutations;
3. encrypts payload envelope bytes with `AtlasVaultRecordCrypto`;
4. creates random non-semantic IDs and revisions for new records;
5. preserves record IDs and uses parent revisions for updates;
6. creates tombstone envelopes for deletes;
7. returns encrypted record envelopes only.

Tests may inject deterministic IDs, revisions, and nonces for repeatable fake
vectors. Default generation remains non-semantic and fresh per save.

## Privacy

The saver:

- does not log private payloads;
- does not mutate public snapshots;
- does not read or write local store files;
- does not call Keychain, UserDefaults, app view models, or networking;
- returns non-sensitive error cases only.

Record type, saved-search names, job keys, notes, snippets, and draft references
remain inside ciphertext. Plaintext record metadata is limited to the encrypted
record envelope allowlist.

## Error Behavior

The first implementation fails closed for:

- invalid unlocked session input;
- unsupported payload schema;
- payload encoding failure;
- record encryption failure;
- missing record IDs;
- invalid or stale revision metadata.

Errors must not include private payload values, record type strings, job keys,
search text, notes, snippets, generated document references, or decrypted JSON.

## Tests

Phase 2D-20 tests cover:

- create mutations for all five record types;
- update of a saved job preserving record ID and parent revision;
- delete mutation producing a tombstone;
- fresh nonce behavior for separate saves;
- deterministic fake generator injection;
- encrypted output without private sentinels or plaintext record type strings;
- round-trip hydration of saver output;
- non-sensitive error output;
- public snapshot unchanged by save planning;
- source guards proving no runtime, public-cache, file-write, Keychain,
  UserDefaults, or networking dependencies.

Shared fake plaintext payload vectors are used as source data; this phase does
not update Python/vector files.

## Deferred

- SwiftUI integration;
- persistence coordinator write-back;
- local store merge policy;
- atomic write;
- migration execution;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.
