# AtlasVault Payload Test Vectors

These vectors are fake, test-only, pre-encryption payload fixtures for
Swift/Python compatibility tests. They are not real user data, not serialized
AtlasVault records, not local vault store files, and not `.atlasvault` exports.

The vectors intentionally contain fake sentinel strings so tests can prove two
separate properties:

- Swift Codable payload models produce the same plaintext-before-encryption JSON
  shape as Python `vaultsync` expects.
- Python encrypted record envelopes do not expose record types or private
  payload values after encryption.

Production payloads must be encrypted before local storage, export, import, or
sync.

## Canonical Rules

- Record type strings are stable: `saved_search`, `saved_job`,
  `application_note`, `profile_snippet`, and `draft_metadata`.
- Common envelope keys are `type`, `payload_schema`, `payload`,
  `client_created_at`, and `client_updated_at`.
- Cross-platform payload keys use snake_case.
- Timestamp fields are ISO-8601 UTC strings without fractional seconds and
  ending in `Z`.
- Date-only filter fields such as `closing_date_to` remain `YYYY-MM-DD`.
- Absent optional fields are omitted, not encoded as explicit `null`.
- Array ordering is meaningful for test vectors.
- Object key ordering is not semantically meaningful; tests may sort keys for
  deterministic comparisons.

## Consumers

Python tests load `atlasvault_payload_vectors_v1.json`, convert each payload
envelope into a `PlaintextRecord`, encrypt and decrypt it with `vaultsync`, and
verify serialized encrypted records contain only the encrypted-record metadata
allowlist.

Swift tests load the same vector file, decode each envelope into the
corresponding `AtlasVaultPayloads.swift` Codable type, re-encode it, and compare
JSON objects semantically.

Dart tests consume all four existing vector files directly from the repository.
They validate strict envelope models, canonical ASCII JSON, encrypted-record
crypto, historical passphrase wrapping, recovery wrapping, and encrypted
export bytes. The vector JSON remains unchanged, fake, and test-only. Where a
vector defines exact bytes, Dart matches the Python and Swift result.
PointyCastle is used only to reproduce historical Argon2id v1 behavior; Dart
AES-GCM, HKDF-SHA256, and SHA-256 use `cryptography`. Runtime key storage,
Flutter app integration, plaintext-cache migration, and production
import/export file I/O remain deferred. Future vector changes require Python,
Swift, and Dart agreement.

## Crypto Vectors

`atlasvault_crypto_vectors_v1.json` contains fake, test-only deterministic
encrypted-record fixtures derived from the payload vectors. They are not real
user data, not production vault files, and not `.atlasvault` exports.

The crypto vectors document the Python reference behavior that future Swift
work must preserve:

- record keys are derived with HKDF-SHA256 from the 32-byte vault key;
- HKDF salt is `atlas-vault:v1:<vault_id>`;
- HKDF info is `record:<record_id>`;
- record encryption uses AES-256-GCM with a 12-byte nonce;
- AAD is UTF-8 JSON with sorted keys and compact separators;
- `plaintext_json_b64` is the exact stable JSON plaintext byte sequence sealed
  by Python and Swift;
- encrypted record `ciphertext` is base64 for `ciphertext || 16-byte GCM tag`;
- the nonce is base64 in the separate encrypted-record `nonce` field.

The raw `test_only_vault_key_b64` values in these vectors are deliberately fake
and exist only so Python and Swift tests can derive identical deterministic
record keys. Production code must never use these keys, fixed nonces, or vector
records.

Python tests load the crypto vectors, recompute the record key, AAD, and
ciphertext through `vaultsync`, decrypt the record, and assert encrypted-record
JSON does not expose record type strings or fake private sentinels.

Swift tests load the same crypto vector file, derive the same record key with
CryptoKit `HKDF<SHA256>`, split Python's ciphertext/tag layout for
`AES.GCM.SealedBox`, authenticate the same AAD bytes, and decrypt the ciphertext
back to the source payload vector. They also seal `plaintext_json_b64` with the
same key, nonce, and AAD and assert CryptoKit produces the same
`ciphertext || tag` bytes as Python. Swift CryptoKit usage is test-only in this
phase; Keychain, runtime vault file I/O, migration execution, and cloud sync are
deferred.

Phase 2D-5 Swift encrypted-record helpers consume the same vectors through
tests. Those helpers encapsulate record-key derivation, AAD construction, and
AES-GCM seal/open behavior, but they still do not store vault keys, call
Keychain, read or write vault files, run migrations, or perform sync. The fixed
keys, nonces, and encrypted records in this directory remain fake test fixtures
only.

## Key-Wrap Vectors

`atlasvault_key_wrap_vectors_v1.json` contains one fake, deterministic,
test-only AtlasVault v1 passphrase-wrap fixture. It is not real user data, not
a production vault, and not a production key.

Python tests validate and recompute the Argon2id plus AES-256-GCM reference
wrap, verify correct and wrong fake passphrase behavior, and prove serialized
vault metadata excludes the fake passphrase and raw key. Swift tests decode
the same v1 metadata and validate its lengths and algorithms; Swift does not
implement Argon2id in this phase.

The vector also documents a v1 limitation: key-wrap AAD excludes `vault_id`.
The adjacent vault ID is routing context, not authenticated provenance.
Production passphrase and recovery unlock therefore remain unavailable until
a separately reviewed provider and versioned vault-binding or
key-confirmation design exist.

## Recovery-Wrap And Encrypted-Export Vectors

`atlasvault_recovery_export_vectors_v2.json` contains one fake, deterministic,
test-only recovery-key-wrap v2 and `atlasvault-export` v1 fixture. It is not
real user data, not a production vault, not a production recovery key, and not
a backup that can recover any user data.

The vector fixes the cross-language representation for:

- 32 fake recovery-key bytes plus the five-byte domain-separated checksum;
- the exact 60-symbol `AVRK1` Base32 text;
- a 32-byte HKDF-SHA256 salt and 12-byte AES-GCM nonce;
- vault-bound, sorted, compact recovery-wrap v2 AAD;
- the fixed `primary-recovery-v2` wrap object;
- top-level `atlas-vault` version 1 metadata containing the versioned wrap;
- canonical sorted, compact `atlasvault-export` version 1 bytes;
- the SHA-256 digest of those canonical export bytes.

Python recomputes the code, AAD, wrap, unwrap, export bytes, and digest. Swift
recomputes the same code, AAD, wrap, unwrap, metadata, and export bytes.
Mutation tests cover vault binding, authentication failure, strict lengths,
canonical Base64, unknown fields, and duplicate recovery-wrap identity.

The adjacent v1 passphrase vector remains authoritative and unchanged.
Recovery wrap v2 does not reinterpret v1 Argon2id wrapping or its historical
AAD. Production generation must use secure randomness rather than the fixed
vector values. Phase 2D-61 verifies export preparation only; import and
production recovery unlock are deferred.
