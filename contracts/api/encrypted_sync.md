# AtlasVault Zero-Knowledge Sync API Contract

Status: C13 account authentication and signed device-registry handlers, C14
opaque storage handlers, and C15 authorization, abuse-control, and secret-free
observability boundaries are implemented. The current store and request limiter
are injected, process-local test infrastructure; durable deployment remains the
C16 P4 gate.

This document defines the API boundary for syncing AtlasVault data.
The API is intentionally opaque: it may route and store encrypted blobs, but it
must not accept, derive, log, or return decrypted user data.

The machine-readable source is
`contracts/api/atlasvault_sync_openapi.json` (OpenAPI 3.1, contract version
1.2.0). Its object schemas reject unknown fields. The Python service models are
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

The current implementation uses injected ephemeral account, challenge,
session, registry, and opaque-storage state. It requires no deployment
credential or hosting configuration. Durable backend state and deployment are
later P4 chunks.

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
Public signing/agreement keys and authentication challenges are canonical
base64 encodings of exactly 32 bytes; descriptor, transition, and session-proof
signatures are canonical base64 encodings of exactly 64 bytes. Their schemas
publish the corresponding length, alphabet, and zero padding-bit constraints,
so a schema-valid value is also a canonical base64 value. Device and
transition signature verification runs outside the shared registry lock, then
authorization, capacity, parent revision, and revision uniqueness are rechecked
under the lock immediately before commit.

The C13 endpoints are:

- `GET /v1/accounts/{account_id}/devices`;
- `POST /v1/accounts/{account_id}/devices`.

C13 intentionally supports add-only transitions. Device revocation, rotation,
and convergent registry synchronization are not implied by this contract.

## Account/Device Authorization And Abuse Controls

Every ciphertext metadata, object, patch, and snapshot operation requires an
active bearer session for a device currently registered to an account. The
authenticated account selects the server-side opaque namespace; a token for a
different account cannot read or mutate it. Authentication runs before request
model parsing or storage mutation.

C15 applies one aggregate storage-request window across all ciphertext paths:

- 40 requests per authenticated account per 60-second window;
- 24 requests per authenticated device per 60-second window;
- 1,024 retained account registries per process;
- 4,096 live authentication challenges per process;
- 8 live authentication challenges per account/device pair;
- 4,096 live session digests per process;
- 8 live session digests per account/device pair;
- 256 retained public devices per account;
- 4,096 retained opaque vault namespaces per process;
- 128 retained opaque vault namespaces per account;
- 16,384 retained object IDs per account;
- 65,536 retained patches per account;
- 131,072 retained ciphertext revisions per account;
- 1 GiB conservative retained ciphertext-revision bytes per process;
- 512 MiB conservative retained ciphertext-revision bytes per account;
- 64 KiB maximum encoded HTTP request body on account-control routes;
- 192 MiB maximum encoded HTTP request body on storage routes.

Limits are keyed by authenticated account and device IDs rather than bearer
tokens. Rotating a session, changing ciphertext endpoints, or adding another
device therefore cannot reset the applicable device or account window. A
non-finite or regressing monotonic clock fails closed for rate windows and all
account challenge/session credentials. Expired process-local windows are
removed through bounded heap-backed cleanup rather than a full counter scan on
each request.

Account bootstrap fails with 429 before insertion when the fixed process-local
registry ceiling is reached. This bounds unauthenticated self-signed account
creation without retaining request-derived dimensions in telemetry.
Challenge and session issuance enforce both process-wide and per-account/device
ceilings, then fail with 429 before retaining state. Signed device addition
likewise fails with 429 before mutation when its per-account ceiling is reached.
Live challenge capacity uses an expiry heap plus per-device counters, so neither
expiry cleanup nor per-device accounting scans the live challenge registry.
Live session capacity uses an expiry heap plus per-device counters; an invalid
challenge is rejected before any capacity maintenance, and no request scans the
session registry.

The in-process opaque store enforces its vault, object, patch, revision, and
byte budgets before mutation. Revision-byte accounting is deliberately
conservative and cumulative: replacing a current envelope does not refund its
budget because revision fingerprints and short-lived idempotency evidence remain
retained. This fail-closed bound keeps the default embedded backend finite; a
durable production backend must provide equivalent or stricter quotas.

The route-specific body ceiling is checked before request parsing from a valid
declared length and while streaming request chunks. Oversized requests return a
fixed 413 response and do not reach the account or storage handler. Rate
exhaustion returns a fixed 429 response. Session proof verification runs outside
the backend registry lock, followed by a locked capacity and expiry recheck at
commit. C16 must choose a shared limiter before any multi-instance deployment;
process-local limits are sufficient only for this in-process P4 contract and
its tests.

Every documented account and storage operation publishes the 413 middleware
boundary together with each handler-specific 400, 401, 404, 409, or 429 result,
so the served and checked-in OpenAPI response sets remain identical.
Bearer-protected account and storage routes include
`WWW-Authenticate: Bearer` on 401 responses. Challenge and session-proof
failures are authentication-flow failures and do not emit that bearer challenge.
Each reusable canonical error response also publishes its JSON body schema:
fixed failures return an object with a string `detail`, while validation
failures return the exact generic `{"detail":"Invalid request."}` shape.
The served `/openapi.json` also publishes `x-atlasvault-c15-controls` directly
from the active `AbuseControlPolicy`, including custom deployment limits rather
than copying only canonical defaults.

## Versioned Ciphertext Storage Shape

C14 registers the following account-session-authenticated storage paths:

- `/v1/vaults/{vault_id}/metadata`;
- `/v1/vaults/{vault_id}/objects/{object_id}`;
- `/v1/vaults/{vault_id}/patches`;
- `/v1/vaults/{vault_id}/snapshots`.

Every request and response body is an exact encrypted-metadata or opaque-
ciphertext envelope. Plaintext, passphrases, recovery keys, raw vault keys, and
unwrapped vault keys are forbidden. The service validates only envelope shape,
opaque path consistency, and concurrency metadata. It does not decode
`ciphertext_b64`, recompute its digest, inspect encrypted content, or use any
content-derived value as a storage key.

The in-process store namespaces state by authenticated account ID, opaque vault
ID, operation kind, and opaque object ID. These identifiers are supplied by the
protocol and must not encode vault contents. The store emits random opaque
cursors; it never derives cursor or idempotency state from ciphertext.

### Conditional Writes And Safe Retries

Every write requires:

- `If-Match: *` for creation of an absent resource, or the exact current opaque
  revision for replacement or append;
- an opaque `Idempotency-Key` scoped to that storage operation.

Both header values are limited to 1-128 visible ASCII bytes (`!` through `~`),
excluding spaces, control characters, Unicode, and HTTP `obs-text`. The same
constraint appears in the canonical and served OpenAPI schemas and is enforced
again at the storage boundary.

Object, patch, and snapshot envelopes must also name the expected current
revision in `parent_revision`. A stale parent, changed replay under the same
idempotency key, or reuse of one revision for different bytes returns a
conflict without mutation. Replaying the same operation returns its original
response. Repeating the identical revision under a new idempotency key is also
suppressed, so delivery is exactly-once in effect.

Idempotency receipts are retained for a 600-second retry window and then
reclaimed. A retry outside that window is evaluated against current revision
state rather than retaining obsolete ciphertext envelopes indefinitely.
Receipt expiries are tracked globally in a min-heap and at most 64 due entries
are reclaimed per write, so a write never scans every retained vault while
holding the store lock. Replay also checks the selected receipt's own expiry,
so a due receipt beyond that bounded batch cannot return a stale response.

Patch appends form a compare-and-set sequence at this backend storage layer.
The server does not decrypt patches or decide record conflicts. Client patch
semantics, authenticated snapshot contents, and convergence remain P5 work.

`GET /v1/vaults/{vault_id}/patches` accepts `page_size` on the first request and
returns an opaque `next_cursor`. Cursor state captures the append boundary and
page size. Retrying a cursor returns the same page and next cursor, while later
appends appear only in a fresh listing. Cursor records expire after 300 seconds;
an expired cursor fails closed and the client starts a fresh listing. Cursor
expiry uses a min-heap, so listings do not scan the global live-cursor registry.
The OpenAPI parameter intentionally has no unconditional default: omitting it
on the first page selects 100 server-side, while later requests inherit the
size retained in their cursor and may omit the parameter.

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
Metadata and opaque object `key_epoch` values use the shared positive signed
64-bit range (`1...9223372036854775807`) enforced by all AtlasVault clients.

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

The server validates shape, path consistency, conditional revisions, and the
account/device session. It must not decrypt or inspect `ciphertext_b64`. C15
enforces account/device throttles before this handler runs.

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
- opaque encrypted record envelopes;
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
- full encrypted blobs, including fake fixture payloads;
- bearer tokens, idempotency keys, account/device/vault/object IDs, or request
  paths;
- user-supplied validation values or PII.

C15 emits only a fixed request category (`account`, `storage`, or `other`), a
fixed outcome class, an HTTP status number, and aggregate counts. Event history
is bounded. Metrics have exactly `category`, `outcome`, and `count` dimensions;
no request-derived label is permitted. Validation, authorization, storage,
size, and throttling failures use fixed response text and do not echo input.

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
