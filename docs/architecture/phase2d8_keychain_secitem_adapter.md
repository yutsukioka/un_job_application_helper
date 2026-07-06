# Phase 2D-8 Keychain SecItem Adapter

Status: adapter implementation behind protocol only. This phase adds a real
SecItem-backed `AtlasVaultKeyStore` implementation and fake-client unit tests,
but does not wire Keychain unlock into app runtime behavior.

## Purpose

Phase 2D-8 turns the Phase 2D-7 key-store protocol boundary into a concrete
Apple Keychain adapter. The adapter lets future unlock work store and retrieve a
random 32-byte AtlasVault vault key by vault ID without exposing SecItem calls to
the unlock/session state model.

## Scope

Included:

- SecItem-backed implementation behind `AtlasVaultKeyStore`;
- a small `AtlasKeychainClient` wrapper around SecItem calls;
- fake SecItem-client tests for save, load, update, and delete behavior;
- validation for vault-key length and vault ID;
- no runtime call site outside tests.

Excluded:

- LocalAuthentication;
- biometric or device-passcode prompts;
- vault unlock UI;
- automatic unlock at launch;
- runtime vault file I/O;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Adapter Boundary

`AtlasVaultKeyStore` remains the public storage protocol used by unlock/session
code. `AtlasKeychainVaultKeyStore` is one implementation of that protocol.

`AtlasKeychainClient` wraps the four SecItem operations the adapter needs:

- add;
- copy matching;
- update;
- delete.

Production construction can use `SecItemAtlasKeychainClient`. Unit tests inject
a fake client and never call the real Keychain. The adapter is not referenced
from app runtime code, view models, cache code, or file I/O.

## Keychain Item Shape

The real client maps vault-key operations to a generic password item:

- class: `kSecClassGenericPassword`;
- service: `com.atlasvault.vault-key` by default;
- account: caller-provided random `vaultID`;
- value data: the 32-byte AtlasVault vault key;
- accessibility: adapter-selected accessibility policy.

Keychain attributes must not include saved-search names, search text, saved-job
keys, application notes, profile snippets, draft metadata, generated document
references, record type strings, or private payload values.

## Default Accessibility Policy

The first adapter default is `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`.
This keeps the local unlock material device-only and avoids iCloud Keychain
sync. It does not add user-presence gating, LocalAuthentication, biometrics, or
access-control prompts.

User-presence policy remains a later design and implementation decision. Face ID,
Touch ID, or the device passcode may release a Keychain item in a future phase,
but biometric data must never be treated as an encryption key.

## Save Load Delete Behavior

Save:

- rejects empty or whitespace-only vault IDs;
- rejects keys that are not exactly 32 bytes;
- adds a new generic-password item;
- updates the existing item if SecItem reports a duplicate.

Load:

- rejects empty or whitespace-only vault IDs;
- returns `nil` when SecItem reports item not found;
- validates loaded key data is exactly 32 bytes;
- maps other SecItem statuses to non-sensitive errors.

Delete:

- rejects empty or whitespace-only vault IDs;
- deletes the matching item;
- treats item-not-found as a successful no-op;
- maps other SecItem statuses to non-sensitive errors.

## Error And Logging Policy

The adapter must not log or serialize:

- vault keys;
- passphrases;
- recovery keys;
- derived keys;
- decrypted payloads;
- private record values.

Errors expose only validation classes or OSStatus values. They must not include
raw key bytes, base64 key material, passphrases, recovery keys, decrypted
payloads, saved-search text, job keys, notes, snippets, or generated document
references.

## Tests

Phase 2D-8 tests use a fake keychain client and cover:

- adding a new generic-password item;
- loading an existing key;
- returning nil for item not found;
- updating on duplicate item;
- deleting an existing item;
- treating deletion of a missing item as a no-op;
- rejecting invalid vault-key length;
- rejecting invalid vault IDs;
- rejecting loaded key data with the wrong length;
- surfacing non-sensitive SecItem errors;
- item attributes containing only service, vault ID, accessibility, and value
  data;
- source-level absence of LocalAuthentication, access-control prompts,
  UserDefaults, file I/O, cache/view-model wiring, and API-client wiring.

## Deferred

- access-control and user-presence policy;
- LocalAuthentication and biometric prompts;
- account recovery UX;
- Keychain migration or key rotation;
- runtime vault unlock UI;
- runtime Swift vault file I/O;
- automatic unlock at launch;
- cleanup of old plaintext snapshots;
- cloud sync and device onboarding.
