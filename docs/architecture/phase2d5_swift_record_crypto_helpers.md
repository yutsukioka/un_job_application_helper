# Phase 2D-5 Swift Record Crypto Helpers

Status: Phase 2D-5 helper implementation. This phase adds reusable Swift
CryptoKit helpers but does not wire them into runtime app storage, UI, or vault
unlock behavior.

## Purpose

Phase 2D-3 and Phase 2D-4 proved Swift CryptoKit can match Python `vaultsync`
for AtlasVault record key derivation, stable AAD, AES-256-GCM open, and
AES-256-GCM seal. Phase 2D-5 moves that proven behavior into a small Swift
helper boundary so later runtime work can depend on one reviewed implementation.

## Scope

Included:

- Swift encrypted-record envelope Codable type;
- HKDF-SHA256 record-key derivation;
- stable JSON AAD construction;
- AES-256-GCM open and seal helpers;
- tests against the shared deterministic Swift/Python vectors.

Not included:

- Keychain;
- passphrase prompts;
- vault unlock UI;
- local vault file read/write;
- migration execution;
- cloud sync;
- device onboarding;
- key rotation.

## Inputs And Outputs

The helpers accept caller-provided inputs only:

- 32-byte vault key bytes;
- vault ID;
- encrypted-record metadata;
- 12-byte nonce from the record envelope;
- plaintext bytes when sealing.

They output either decrypted plaintext bytes or an encrypted record envelope with
base64 `ciphertext || 16-byte GCM tag`. The helpers do not store vault keys,
read files, write files, call Keychain, use UserDefaults, or hydrate SwiftUI
state.

## Privacy Boundary

Record type and private payload values remain inside encrypted plaintext bytes.
The encoded encrypted-record envelope keeps only AtlasVault v1 plaintext
metadata:

- `id`;
- `schema_version`;
- `revision`;
- `parent_revision`;
- `deleted`;
- `key_id`;
- `nonce`;
- `ciphertext`.

## Error Handling

The helper API fails closed for:

- authentication failure from wrong AAD, nonce, ciphertext, tag, key, or vault
  ID;
- malformed base64 in nonce or ciphertext;
- invalid vault-key length;
- invalid nonce length;
- unsupported encrypted-record schema version;
- malformed encrypted-record envelope layout.

Errors do not expose vault keys, derived keys, plaintext bytes, or private
payload data.

## Test Strategy

Swift tests consume `contracts/sync/test_vectors/atlasvault_crypto_vectors_v1.json`
and verify:

- record-key derivation matches Python;
- AAD bytes match Python stable JSON;
- Swift opens Python encrypted records;
- Swift seals the canonical plaintext bytes to the same Python ciphertext/tag;
- wrong AAD, wrong nonce, and tampered ciphertext fail authentication;
- serialized encrypted records omit fake private sentinels and record type
  strings;
- the helper source does not reference Keychain, UserDefaults, or file I/O APIs.

The fixed keys, nonces, IDs, and payloads in these vectors are fake and
test-only.
