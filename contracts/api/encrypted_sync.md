# AtlasVault Zero-Knowledge Sync API Contract

Status: C13 account authentication and signed device-registry handlers are
implemented. Ciphertext storage operations are versioned here but remain
unimplemented until C14.

This document defines the API boundary for syncing AtlasVault data.
The API is intentionally opaque: it may route and store encrypted blobs, but it
must not accept, derive, log, or return decrypted user data.

The machine-readable source is
`contracts/api/atlasvault_sync_openapi.json` (OpenAPI 3.1, contract version
1.0.0). Its object schemas reject unknown fields. The Python service models are
also strict, and `tests/security/test_A4_encrypted_sync_wire_guard.py` checks
them against `BANNED_WIRE_FIELD_NAMES` in
`packages/vaultsync/vaultsync/service_contract_guard.py`.

## Boundary Summary

Vault-data endpoints may accept and store only:

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

No endpoint may accept or store:

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

## Account And Device Authentication

Account identity answers "which account may store or retrieve these opaque
blobs." Encryption answers "who can read the user data."

The C13 account service uses the existing AtlasVault Ed25519 device identity:

1. A self-signed add transition bootstraps an opaque account ID and its first
   signed public-device descriptor.
2. An active device requests a one-time, short-lived challenge.
3. The device signs a domain-separated proof binding the account ID, device ID,
   challenge ID, and challenge bytes.
4. The server consumes the challenge and returns a short-lived opaque bearer
   token. Only the token's SHA-256 digest is retained server-side.

Sessions authorize an account and active device. They do not authorize vault
decryption and carry no vault-key material. Authentication endpoints are:

- `POST /v1/accounts/{account_id}/devices/bootstrap`;
- `POST /v1/accounts/{account_id}/auth/challenges`;
- `POST /v1/accounts/{account_id}/sessions`.

None of those endpoints may receive:

- raw vault keys;
- passphrases;
- recovery keys;
- decrypted record payloads.

Authentication tokens are not encryption keys. Account IDs are not vault keys.
Device IDs must be opaque sync identifiers and must not embed personal data.

The current C13 implementation uses injected ephemeral account, challenge,
session, and registry state. It requires no deployment credential or hosting
configuration. Durable backend state and deployment are later P4 chunks.

## Signed Server Device Registry

The server registry stores only opaque account IDs, signed device descriptors,
public signing and agreement keys, and a current opaque revision. A transition
has an exact schema and is signed over canonical JSON with the domain
`atlasvault-device-registry-transition-v1:`.

Bootstrap is a self-signed `add` transition. Later additions require both an
account-scoped session for the signer device and a transition signature from
that same active device. The parent revision must equal the current registry
revision, target descriptors must pass their existing self-signature and
device-ID checks, and tampered or stale transitions fail closed.

The C13 endpoints are:

- `GET /v1/accounts/{account_id}/devices`;
- `POST /v1/accounts/{account_id}/devices`.

C13 intentionally supports add-only transitions. Device revocation, rotation,
and convergent registry synchronization are not implied by this contract.

## Versioned Ciphertext Storage Shape

The OpenAPI contract reserves the following authenticated storage paths for
C14. C13 does not register handlers for them:

- `/v1/vaults/{vault_id}/metadata`;
- `/v1/vaults/{vault_id}/objects/{object_id}`;
- `/v1/vaults/{vault_id}/patches`;
- `/v1/vaults/{vault_id}/snapshots`.

Every request and response body is an exact encrypted-metadata or opaque-
ciphertext envelope. Plaintext, passphrases, recovery keys, raw vault keys, and
unwrapped vault keys are forbidden. Conditional revisions, cursors,
idempotency, storage, and retry behavior are implemented in C14 and later P4
chunks rather than in C13.

### Put Vault Metadata

`PUT /v1/vaults/{vault_id}/metadata`

Allowed request fields:

- `format` and `version`;
- `vault_id`;
- `revision` and `key_epoch`;
- `nonce_b64`, `ciphertext_b64`, and `aad_b64`;
- `signature_b64` and `content_sha256`.

The ciphertext may decrypt client-side to wrapped vault keys and crypto
parameters. The server envelope must not contain raw vault keys, passphrases,
recovery keys, or plaintext user records.

### Get Vault Metadata

`GET /v1/vaults/{vault_id}/metadata`

Returns only the encrypted vault metadata object associated with the authenticated
account and vault ID.

### Put Encrypted Records

`PUT /v1/vaults/{vault_id}/objects/{object_id}`

Allowed request fields per record:

- `format` and `version`;
- `object_id`;
- `revision`;
- `parent_revision`;
- `key_epoch`;
- `nonce_b64`, `ciphertext_b64`, and `aad_b64`;
- `signature_b64` and `content_sha256`;
- `tombstone`.

The server may validate shape, size, version allowlists, and authenticated user
permissions. It must not decrypt or inspect `ciphertext_b64`.

### Get Opaque Changes

`GET /v1/vaults/{vault_id}/patches`

Returns encrypted records and tombstones needed by the client to converge.
Conflict sets may contain multiple encrypted revisions with the same record ID.
Clients resolve conflicts locally after decryption.

### Delete Or Tombstone Records

Deletes should be represented as tombstones using the encrypted object metadata:

- same `object_id`;
- new `revision`;
- previous `parent_revision`;
- `tombstone: true`;
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
