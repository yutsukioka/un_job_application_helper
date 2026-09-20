# Phase 2D-9 Swift Local Vault Store Helpers

Status: helper implementation only. This phase adds Swift Codable and explicit
URL read/write helpers for AtlasVault local encrypted store envelopes. It does
not wire the helper into app runtime behavior.

## Purpose

Phase 2D-9 follows the encrypted-record helper and Keychain adapter phases by
adding a small Swift boundary for serialized local encrypted vault stores. The
helper preserves encrypted record envelopes and vault metadata JSON so later
runtime work can choose storage paths and unlock behavior separately.

## Scope

Included:

- `atlasvault-local-store` envelope Codable types;
- generic JSON metadata preservation;
- explicit caller-provided URL read/write helpers;
- overwrite protection by default;
- temporary-directory tests only.

Excluded:

- default app storage paths;
- Application Support path selection;
- Keychain usage;
- unlock/session wiring;
- SwiftUI state hydration;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## Envelope Format

The local store envelope matches the Python `vaultsync` shape:

```json
{
  "format": "atlasvault-local-store",
  "version": 1,
  "store_id": "random-store-id",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z",
  "vault_metadata": {},
  "records": []
}
```

`records` contains `AtlasVaultEncryptedRecordEnvelope` values. Record payloads
remain encrypted. The helper validates envelope shape and encrypted-record
metadata, but it does not decrypt records.

Export-envelope helpers are deferred until a separate import/export phase needs
them on Apple.

## Privacy Boundary

Plaintext local-store metadata may contain only non-private vault/store metadata
and encrypted-record envelope metadata. The store JSON must not contain
saved-search names, search text, private filters, saved-job keys, statuses,
notes, profile snippets, draft metadata, generated document references, record
type strings, decrypted payloads, passphrases, recovery keys, or raw vault keys.

The helper rejects legacy plaintext snapshot fields such as `savedSearches` and
`savedJobs` at the top level so old public-cache shapes are not accepted as
vault stores.

## Error Handling

The helper fails closed for:

- malformed JSON;
- unsupported store version;
- invalid store format;
- missing required envelope fields;
- invalid encrypted-record schema or base64 metadata;
- overwrite attempts when `overwrite` is not explicit;
- read/write failures from caller-provided URLs.

Errors are non-sensitive and do not include payload data, key material, or
private sentinels.

## Tests

Swift tests cover:

- envelope encode/decode round trip;
- write/read using temporary paths;
- overwrite refusal by default;
- explicit overwrite success;
- unsupported version and invalid format failures;
- malformed JSON failure;
- encrypted vector record round trip;
- serialized store JSON omitting private vector sentinels and record type
  strings;
- tampered-but-well-formed ciphertext preservation without decryption;
- rejection of legacy private snapshot fields;
- source guards for no Keychain, UserDefaults, default app paths, runtime app
  wiring, or networking.

## Deferred

- app storage path selection;
- Keychain unlock wiring;
- vault unlock UI;
- SwiftUI private-state hydration;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.
