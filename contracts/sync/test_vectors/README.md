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
