# Future Encrypted Sync API Contract

Status: Phase 0 contract only. No encrypted sync API is implemented yet.

This document defines the future API boundary for syncing AtlasVault v1 data.
The API is intentionally opaque: it may route and store encrypted blobs, but it
must not accept, derive, log, or return decrypted user data.

## Boundary Summary

The future encrypted sync API may accept and store only:

- vault IDs;
- encrypted vault metadata;
- encrypted record blobs;
- record IDs;
- revision IDs;
- parent revision IDs;
- tombstone markers;
- key IDs that do not reveal plaintext;
- updated timestamps or logical clocks needed for sync;
- opaque device IDs.

The future encrypted sync API must never accept or store:

- plaintext saved searches;
- plaintext saved jobs;
- plaintext application notes;
- plaintext generated documents;
- personal histories;
- raw vault keys;
- passphrases;
- recovery keys;
- decrypted payloads.

## Not The Current Local Plaintext API

The current local endpoints remain local-only:

- `GET /api/saved-searches`
- `POST /api/saved-searches`
- `GET /api/tracker`
- `POST /api/tracker`
- `POST /api/tracker/jobs/{job_key}`
- `DELETE /api/tracker/{record_id}`

Those endpoints are plaintext, unauthenticated, and intended for the local
FastAPI service on `127.0.0.1:8765`. They are not acceptable as cloud sync
endpoints and must not be exposed as internet-facing sync APIs without separate
authentication and a ciphertext-only redesign.

## Authentication Is Separate From Encryption

Account identity answers "which account may store or retrieve these opaque
blobs." Encryption answers "who can read the user data."

Future authentication may use a custom account service, OAuth, passkeys, or a
cloud provider identity. None of those systems may receive:

- raw vault keys;
- passphrases;
- recovery keys;
- decrypted record payloads.

Authentication tokens are not encryption keys. Account IDs are not vault keys.
Device IDs must be opaque sync identifiers and must not embed personal data.

## Draft API Shape

Endpoint names are illustrative. They describe the allowed data flow, not an
implemented service.

### Put Vault Metadata

`PUT /api/encrypted-sync/vaults/{vault_id}/metadata`

Allowed request fields:

- `vault_id`;
- serialized AtlasVault metadata;
- opaque account or device context from authentication;
- optional logical clock or updated timestamp.

The metadata may contain wrapped vault keys and crypto parameters. It must not
contain raw vault keys, passphrases, recovery keys, or plaintext user records.

### Get Vault Metadata

`GET /api/encrypted-sync/vaults/{vault_id}/metadata`

Returns only the encrypted vault metadata object associated with the authenticated
account and vault ID.

### Put Encrypted Records

`PUT /api/encrypted-sync/vaults/{vault_id}/records`

Allowed request fields per record:

- `id`;
- `schema_version`;
- `revision`;
- `parent_revision`;
- `deleted`;
- `key_id`;
- `nonce`;
- `ciphertext`;
- optional server received timestamp or logical clock.

The server may validate shape, size, version allowlists, and authenticated user
permissions. It must not decrypt or inspect `ciphertext`.

### Get Record Changes

`GET /api/encrypted-sync/vaults/{vault_id}/records?since=<cursor>`

Returns encrypted records and tombstones needed by the client to converge.
Conflict sets may contain multiple encrypted revisions with the same record ID.
Clients resolve conflicts locally after decryption.

### Delete Or Tombstone Records

Deletes should be represented as tombstones using the encrypted record metadata:

- same record `id`;
- new `revision`;
- previous `parent_revision`;
- `deleted: true`;
- encrypted payload policy defined by the client.

Hard deletes are a retention policy concern and must not replace tombstones
until all active clients have had a chance to observe the delete.

## Provider-Neutral Storage

The API may be backed by a custom database, object storage, CloudKit, Drive-like
file storage, or another provider. Provider choice must not change the vault
format. Providers store:

- encrypted vault metadata;
- encrypted record JSON blobs;
- opaque sync cursors and clocks.

Providers must not store plaintext user-saved text or raw secrets.

## Logging And Observability

Logs, traces, metrics, and error reports must not include:

- plaintext record payloads;
- decrypted vault contents;
- passphrases;
- recovery keys;
- raw vault keys;
- AES-GCM nonces paired with plaintext;
- full encrypted blobs unless explicitly needed in a local debug fixture using
  fake test data.

Production logs may include opaque IDs, counts, version numbers, and high-level
failure classes.

## Manual Export And Import

Manual encrypted export/import uses the same blob boundary as the future sync
API. Export bundles may contain encrypted vault metadata, encrypted record blobs,
record IDs, revisions, parent revisions, tombstones, and opaque export metadata.

They must not contain plaintext saved searches, tracker records, application
notes, generated documents, raw vault keys, passphrases, or recovery keys.

## Rejected API Designs

The following are explicitly out of contract:

- adding cloud exposure to `/api/saved-searches` or `/api/tracker`;
- storing plaintext saved searches in a cloud table;
- storing plaintext application notes in a backend;
- sending a passphrase to a server for key derivation;
- sending raw vault keys to a server for device onboarding;
- relying on cloud provider encryption as a substitute for end-to-end
  encryption;
- logging decrypted payloads for troubleshooting.

## CI Guard

`tests/security/test_A4_encrypted_sync_wire_guard.py` uses the static guard in
`packages/vaultsync/vaultsync/service_contract_guard.py` to scan Python service
route handlers and Pydantic request models. The test fails if an endpoint under
`services/` accepts fields such as `passphrase`, `recovery_key`, `vault_key`,
`raw_vault_key_b64`, or `unwrapped_vault_key_b64`.
