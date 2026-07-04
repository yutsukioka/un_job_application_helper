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
back to the source payload vector. Swift CryptoKit usage is test-only in this
phase; Keychain, runtime vault file I/O, migration execution, and cloud sync are
deferred.
