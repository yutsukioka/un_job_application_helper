# Phase 2D-17 AtlasVault Record Hydration Design

Phase 2D-17 defines the future record-hydration boundary before any Swift
implementation. Hydration means decrypting encrypted AtlasVault record envelopes
after unlock, decoding plaintext payload envelopes, and producing in-memory
private state without writing decrypted payloads to disk, public cache snapshots,
or logs.

## 1. Purpose

This design describes how future Swift code should turn encrypted local-store
records into unlocked private models after a valid vault key is available. It
builds on the existing payload Codable types, record crypto helper, local-store
IO, and persistence coordinator while keeping runtime UI integration deferred.

## 2. Scope

This phase is documentation only.

It does not add:

- record hydration code;
- SwiftUI, `SearchViewModel`, or app runtime wiring;
- local store writes;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync, device onboarding, or key rotation.

The first implementation should be test-only and vector-driven before any
runtime state hydration.

## 3. Current Building Blocks

`AtlasVaultEncryptedRecordEnvelope` is the persisted encrypted record envelope.
Its plaintext metadata contains random record IDs, revisions, parent revisions,
delete markers, key IDs, nonces, and ciphertext only. Record type, job keys,
saved-search names, notes, snippets, and document references remain inside the
ciphertext.

`AtlasVaultPayloadEnvelope` is the common plaintext wrapper. It carries
`type`, `payload_schema`, `payload`, `client_created_at`, and
`client_updated_at`. Swift payload structs already exist for all Phase 2 private
record types.

`AtlasVaultRecordCrypto` derives per-record keys and opens encrypted records
with the unlocked vault key, vault ID, and authenticated metadata. It returns
plaintext bytes only to the caller.

`AtlasVaultPersistenceCoordinator` currently loads and saves encrypted local
store envelopes only. It deliberately does not decrypt records or hydrate private
models.

`AtlasPublicLocalSnapshot` and `AtlasLocalCache` represent the public cache
split. Hydrated private state must not be serialized into that snapshot.

## 4. Hydration Boundary

Future types, names subject to implementation review:

- `AtlasVaultRecordHydrator`
- `AtlasVaultPayloadDecoder`
- `AtlasVaultHydratedState`
- `AtlasVaultHydratedRecord`
- `AtlasVaultHydrationError`

The hydrator should:

- accept encrypted records plus an unlocked vault session or vault key;
- decrypt each record with `AtlasVaultRecordCrypto`;
- decode the common payload envelope first;
- dispatch by `AtlasVaultPayloadType`;
- decode the typed payload only after the type is known;
- preserve record ID, revision, parent revision, deleted flag, and key ID in
  in-memory metadata needed for later save or merge;
- produce in-memory private models only;
- never write decrypted payload bytes, decoded payloads, or derived private
  state to disk;
- never mutate `AtlasPublicLocalSnapshot` or public detail-cache metadata.

Hydration should sit after local-store read and before any future unlocked UI
state projection. It should not obtain vault keys, choose paths, create
directories, call Keychain, or save encrypted records.

## 5. Supported Record Types

### `saved_search`

Source payload: `AtlasSavedSearchVaultRecordPayload`.

Target state: a private saved-search record containing encrypted-record metadata
plus `AtlasSavedSearchVaultPayload`. A future UI projection may map the payload
to `AtlasSavedSearch` only while unlocked.

Sensitive fields: saved-search name, summary, description, search text, filters,
source IDs, location filters, capability tags, and timestamps tied to user
behavior.

### `saved_job`

Source payload: `AtlasSavedJobVaultRecordPayload`.

Target state: a private saved-job record containing encrypted-record metadata
plus `AtlasSavedJobVaultPayload`. A future UI projection may map the payload to
`AtlasApplicationRecord` only while unlocked.

Sensitive fields: saved-only job key, status, notes, applied timestamp, updated
timestamp, and any legacy tracker ID.

### `application_note`

Source payload: `AtlasApplicationNoteVaultRecordPayload`.

Target state: a private note record containing encrypted-record metadata plus
`AtlasApplicationNoteVaultPayload`.

Sensitive fields: title, body, note kind, linked job key, linked saved-job record
ID, pin state, sort order, and timestamps.

### `profile_snippet`

Source payload: `AtlasProfileSnippetVaultRecordPayload`.

Target state: a private reusable-snippet record containing encrypted-record
metadata plus `AtlasProfileSnippetVaultPayload`.

Sensitive fields: title, body, target system, field hint, tags, provenance notes,
and timestamps.

### `draft_metadata`

Source payload: `AtlasDraftMetadataVaultRecordPayload`.

Target state: a private draft-metadata record containing encrypted-record
metadata plus `AtlasDraftMetadataVaultPayload`.

Sensitive fields: linked job key, linked saved-job record ID, target system,
document type, generated document reference, draft status, generated/reviewed/
submitted/archive timestamps, personal context reference, and context summary.

## 6. In-Memory State Model

`AtlasVaultHydratedState` should separate active records from tombstones while
preserving enough metadata for later save and merge work.

Recommended shape:

- saved searches keyed by record ID, with optional secondary lookup by name only
  inside unlocked memory;
- saved jobs keyed by record ID, with optional secondary lookup by job key only
  inside unlocked memory;
- application notes keyed by record ID;
- profile snippets keyed by record ID;
- draft metadata keyed by record ID;
- tombstones keyed by record ID;
- per-record revision and parent-revision metadata;
- non-sensitive aggregate hydration status.

Record IDs and revisions may remain in memory because they are needed for later
save, tombstone handling, and conflict review. Public UI and public cache code
must not infer saved-job membership, saved-search labels, private counts, or
workflow progress from that state while locked.

## 7. Tombstone And Conflict Behavior

Deleted encrypted records should be retained as tombstones in hydrated private
state but excluded from active UI-facing collections. Tombstones should keep
record ID, revision, parent revision, key ID, and deleted flag; their encrypted
payload should not be decrypted unless a future merge policy requires it and has
been reviewed.

Conflict siblings are future work. The first hydrator should keep enough
metadata to detect multiple active records with related parent revisions, but it
should not invent conflict resolution UI or merge plaintext automatically.

Tombstone merge should not require plaintext inspection for the common case:
record ID, revision, parent revision, and deleted flag are authenticated
metadata.

## 8. Error Handling

Hydration errors should be non-sensitive:

- `authenticationFailed`: wrong key or AEAD authentication failure;
- `corruptCiphertext`: malformed nonce, base64, tag, or ciphertext envelope;
- `malformedPlaintextJSON`: decrypted bytes are not valid JSON;
- `unsupportedPayloadSchema`: payload schema is not supported;
- `unknownPayloadType`: decrypted envelope type is not recognized;
- `invalidPayload`: typed payload does not match its schema;
- `partialHydrationFailed`: non-fatal per-record decode errors were collected;
- `fatalHydrationFailed`: private state must be cleared.

Recommended first implementation policy:

- fail closed for wrong key, corrupt ciphertext, unsupported record schema, or
  malformed store-level input;
- clear any partially hydrated private state after a fatal error;
- optionally collect per-record non-sensitive errors only when the remaining
  records can be safely hydrated;
- never expose partial plaintext after authentication failure or corrupt-store
  conditions;
- avoid embedding record type, job key, search text, note body, or document
  reference in thrown errors.

## 9. Logging And Privacy

Hydration logs, diagnostics, and user-visible errors must not include:

- vault keys, record keys, passphrases, recovery keys, or derived key material;
- decrypted JSON or plaintext payload bytes;
- saved-search names, search text, filters, or source IDs;
- job keys, saved-job status, notes, or applied timestamps;
- profile snippets, provenance notes, generated document references, personal
  context references, or context summaries;
- private record IDs if they become user-correlatable in a later design.

If logging is needed, log only non-sensitive error classes and coarse counts
after review. Avoid private record counts if they reveal saved-job or
saved-search membership in locked contexts.

## 10. Public Snapshot Interaction

Hydrated state must never be serialized into `AtlasPublicLocalSnapshot` or
written through `AtlasLocalCache`.

The public snapshot may continue to hold public health, public search results,
public facets, source summaries, source runs, and public detail-cache metadata.
It must not receive saved searches, saved jobs, saved-only job keys, notes,
profile snippets, draft metadata, private record counts, or generated document
references.

Public detail cache warmup must not be driven from saved-only job membership
unless a later reviewed design proves it leaves no filenames, counts, progress
messages, or cache metadata that reveal private membership.

Locked state must clear hydrated private state while leaving public cache state
available.

## 11. Save-After-Hydration Boundary

Future save flow after hydration:

1. Unlocked private state changes in memory.
2. The modified private model is encoded into the common payload envelope.
3. The payload envelope is serialized to canonical plaintext JSON.
4. `AtlasVaultRecordCrypto` encrypts the payload into an encrypted record
   envelope.
5. The local store envelope is updated with encrypted records only.
6. The persistence coordinator writes encrypted local-store JSON.

No plaintext payload should be written to disk during save. Atomic write policy,
revision generation, conflict detection, and tombstone compaction remain future
design items.

## 12. Tests For Future Implementation

Future test-only hydrator implementation should cover:

- decrypting a `saved_search` vector into in-memory saved-search state;
- decrypting a `saved_job` vector into in-memory saved-job state;
- decrypting all five supported record types from shared vectors;
- preserving record ID, revision, parent revision, deleted flag, and key ID in
  hydrated metadata;
- excluding tombstones from active state while retaining tombstone metadata;
- unknown record type policy: fail safely or ignore with non-sensitive error,
  depending on review decision;
- unsupported payload schema failure;
- wrong-key/authentication failure with no partial plaintext exposure;
- malformed plaintext JSON failure;
- corrupt ciphertext failure;
- no private data in errors or logs;
- public snapshot serialization still excludes private state;
- locked state clears hydrated private state.

Tests should use fake shared vectors only, never real user data.

## 13. Deferred

- record hydration implementation;
- SwiftUI hydration;
- `SearchViewModel` or `AtlasLocalCache` integration;
- migration execution;
- conflict resolution UI;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.

## 14. Recommended Next Loop

After Phase 2D-17 review, implement Phase 2D-18 as a test-only record hydrator
protocol plus vector tests. Keep it disconnected from SwiftUI runtime
integration, migration execution, and public cache writes.
