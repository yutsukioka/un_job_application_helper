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

## iOS-Flutter Encrypted Interoperability Vector

`atlasvault_ios_flutter_interop_vectors_v1.json` contains two fake, test-only
encrypted interoperability cases. `flutter_to_ios` is generated by the Dart
recovery-export path and imported by the existing Apple recovery-import
transaction. `ios_to_flutter` is generated by the existing Apple
recovery-export transaction and imported by the Dart/Android clean-install
transaction.

Both cases contain only fake keys and fake encrypted records. They fix the
ordered encrypted records, recovery-wrap v2 metadata, canonical
`atlasvault-export` version 1 bytes, and SHA-256 digest for each direction.
Python, Swift, and Dart continue to own the established wire rules; no existing
vector JSON was modified.

Direct cross-language tests additionally exchange encrypted `.atlasvault`
artifacts outside the repository. Recovery keys remain separate test inputs,
and no plaintext sidecar or production user data is written.

## Windows Encrypted Interoperability Vector

`atlasvault_windows_interop_vectors_v1.json` contains three fake, test-only
encrypted interoperability cases:

- `apple_to_windows` is produced through the Apple production recovery-export
  coordinator and installed through the Windows production import coordinator.
- `android_to_windows` is produced through the Android/Flutter production
  recovery-export coordinator and installed through the Windows production
  import coordinator.
- `windows_to_apple_android` is produced through the Windows production
  recovery-export coordinator and installed through the Apple and Android
  production import coordinators.

Each case fixes fake recovery-key text, a fake raw vault key used only by the
deterministic tests, canonical `atlasvault-export` version 1 bytes, SHA-256,
ordered encrypted records, supported-record counts, unsupported private-record
counts, and tombstone counts. The cases prove that every producer and consumer
agrees on canonical bytes while preserving encrypted record order, unsupported
records, and tombstones. Recovery-wrap v2 remains the portable recovery
boundary, and valid passphrase-wrap v1 entries may coexist where specified.

All values are fake. Recovery keys are supplied separately by the test vector;
they are not embedded in `.atlasvault` documents and are never written as
sidecars. Direct encrypted artifacts and their SHA-256 files are generated in
the external persistent checkpoint, not in the repository. No production user
data or plaintext intermediary is present.

The pre-existing payload, crypto, key-wrap, recovery-export, and iOS-Flutter
vector JSON files remain unchanged. The Windows vector adds another consumer
of the established wire format; it does not change any wire field or version.

## Device Identity And Pairing Vector

`atlasvault_device_identity_pairing_vectors_v1.json` is a fake,
deterministic, test-only identity and pairing fixture. Python is the reference
generator. It fixes two fake Ed25519 seeds, two fake X25519 private keys,
derived public keys and opaque device IDs, strict descriptors, signed pairing
objects, a length-delimited transcript hash, the X25519 shared secret,
HKDF-SHA256 session key, directional HMAC-SHA256 proofs, and invalid cases.
None of these values belong to a production user or installation.

The vector contains one fixed valid Python-generated Ed25519 signature for
each signed object. Python and deterministic Dart signing reproduce those
bytes. Swift strictly decodes and canonicalizes each fixed signed envelope and
verifies its signature with CryptoKit. Fresh CryptoKit signatures are required
to be valid but are not compared for equality or inequality with the fixed
signature because CryptoKit intentionally randomizes signature generation.
Canonical unsigned payloads remain byte-identical across all runtimes.

The fixed transcript is derived from the fixed signed envelopes. A runtime
transcript is derived from the exact runtime envelopes actually exchanged, so
another valid signature can yield another valid transcript hash. Phase 2F-1
tests write a public-only fresh Swift signed transcript to the external
checkpoint directory identified by
`ATLAS_DEVICE_IDENTITY_RUNTIME_VECTOR_DIR`; Python and Dart verify those fresh
signatures and reproduce the transcript and confirmation proofs. That external
artifact contains no private key, shared secret, session key, recovery key, or
vault key and is never committed.

The vector defines only cryptographic possession and transcript behavior. It
does not establish device trust, transport a vault key, persist replay state,
implement QR pairing, register devices with a backend, synchronize records,
revoke devices, or rotate keys. All pre-existing vector JSON files remain
unchanged.
