# Phase 2D-3 Swift CryptoKit Feasibility

Status: Phase 2D-3 feasibility and compatibility-vector work. This phase is
test-only. It does not add runtime vault behavior to the Apple app.

## Purpose

Phase 2D-3 checks whether Swift CryptoKit can reproduce the Python
`vaultsync` encrypted-record behavior before any Keychain, unlock UI, local
vault file I/O, migration execution, or cloud sync work begins.

The compatibility target is AtlasVault v1 record encryption:

- derive a per-record key from the vault key with HKDF-SHA256;
- encrypt the stable plaintext payload JSON with AES-256-GCM;
- authenticate the same stable JSON associated data used by Python;
- keep record type and private payload values inside ciphertext only.

## Scope

Included:

- fake deterministic encrypted-record test vectors;
- Python tests that recompute and decrypt those vectors through `vaultsync`;
- Swift test-target CryptoKit tests that derive the same record keys and
  decrypt the Python-generated ciphertext;
- documentation of the nonce, AAD, HKDF, and ciphertext/tag conventions.

Deferred:

- Keychain storage;
- vault unlock UI;
- runtime Swift vault persistence;
- Swift app state hydration from decrypted records;
- migration execution;
- cleanup of legacy plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Python Reference Behavior

`packages/vaultsync/vaultsync/crypto.py` is the reference implementation.

Record key derivation:

- input key material: the 32-byte vault key;
- KDF: HKDF-SHA256;
- output length: 32 bytes;
- salt: UTF-8 bytes for `atlas-vault:v1:<vault_id>`;
- info: UTF-8 bytes for `record:<record_id>`.

Record encryption:

- AEAD: AES-256-GCM;
- nonce length: 12 bytes;
- plaintext: UTF-8 stable JSON for the `PlaintextRecord` object;
- AAD: UTF-8 stable JSON with sorted keys and compact separators.

Python AAD keys are:

- `vault_format`;
- `vault_version`;
- `vault_id`;
- `record_id`;
- `record_schema_version`;
- `revision`;
- `parent_revision`;
- `deleted`;
- `key_id`.

Python serializes JSON with sorted keys, compact separators, ASCII escaping, and
UTF-8 encoding. The encrypted record stores `nonce` separately. The
`ciphertext` field is base64 for `cryptography` AESGCM output: ciphertext bytes
followed by the 16-byte GCM tag.

## Swift Feasibility

Swift CryptoKit can represent the same primitives in the Apple test target:

- `HKDF<SHA256>.deriveKey` for the record subkey;
- `AES.GCM.Nonce(data:)` for 12-byte nonces;
- `AES.GCM.SealedBox(nonce:ciphertext:tag:)` for Python's separate nonce plus
  combined ciphertext/tag record layout;
- `AES.GCM.open(_:using:authenticating:)` for AAD-authenticated decryption.

The Swift tests intentionally keep this CryptoKit usage inside
`apps/apple/Tests/AtlasUITests`. No production encryption helper, Keychain
access, vault file reader, or app-state hydration is added in this phase.

## Deterministic Vector Strategy

`contracts/sync/test_vectors/atlasvault_crypto_vectors_v1.json` contains fake,
test-only deterministic records generated from the existing pre-encryption
payload vectors.

Each vector records:

- a clearly labeled `test_only_vault_key_b64`;
- a fake vault ID;
- a fake record ID and revision;
- a fake fixed nonce;
- the expected record key;
- the expected stable AAD JSON and AAD bytes;
- the encrypted record envelope.

The raw vault key in this file is not a production key. It exists only so both
Python and Swift can prove deterministic compatibility.

## Metadata Privacy

The encrypted record envelope keeps only the AtlasVault plaintext metadata
allowlist:

- `id`;
- `schema_version`;
- `revision`;
- `parent_revision`;
- `deleted`;
- `key_id`;
- `nonce`;
- `ciphertext`.

The record type, saved-search text, filters, saved-job status, notes, profile
snippets, generated document references, and other personal context remain in
the encrypted payload. Python and Swift tests assert that fake private sentinel
strings and plaintext record type strings are absent from serialized encrypted
records.

## Next Phase

After review, the next implementation candidate is Phase 2D-4: test-only Swift
encrypted-record helper design. That phase should still precede Keychain,
runtime vault file I/O, migration execution, and cloud sync.
