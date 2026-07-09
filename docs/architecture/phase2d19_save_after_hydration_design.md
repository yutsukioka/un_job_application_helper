# Phase 2D-19 AtlasVault Save-After-Hydration Design

## 1. Purpose

Phase 2D-19 designs how edited hydrated private state will be saved back to
encrypted AtlasVault records after unlock. The goal is to define the boundary
before implementation so future code can encode private models, encrypt record
envelopes, and update the local-store envelope without ever writing plaintext
private payloads to disk or public cache state.

## 2. Scope

This phase is design only.

It does not add:

- save-after-hydration code;
- runtime app integration;
- SwiftUI, `SearchViewModel`, or `AtlasLocalCache` wiring;
- local store writes;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync, device onboarding, or key rotation.

Any implementation should first be test-only and vector-driven before runtime UI
or app persistence integration.

## 3. Current Building Blocks

`AtlasVaultHydratedState` from Phase 2D-18 groups decrypted private records in
memory by type and retains record metadata for future save and merge work.

`AtlasVaultPayloadEnvelope` and the five payload models provide Codable payload
shapes for `saved_search`, `saved_job`, `application_note`, `profile_snippet`,
and `draft_metadata`.

`AtlasVaultRecordCrypto` derives per-record keys from the unlocked vault key,
vault ID, and record ID. It seals plaintext payload envelope bytes into
`AtlasVaultEncryptedRecordEnvelope` values and authenticates plaintext metadata
with AAD.

`AtlasVaultLocalStoreEnvelope` stores only encrypted record envelopes and
non-sensitive store metadata. `AtlasVaultLocalStoreIO` validates, encodes, and
writes the local-store JSON at explicit caller-provided URLs.

`AtlasVaultPersistenceCoordinator` coordinates path, directory, and encrypted
local-store IO seams. It currently avoids record hydration and should remain the
layer that persists encrypted local-store envelopes after save planning.

`AtlasPublicLocalSnapshot` is the public cache split. It must not receive
private records, private counts, saved-only job keys, notes, snippets, or draft
metadata.

## 4. Save Boundary

Future types, names subject to implementation review:

- `AtlasVaultRecordSaver`;
- `AtlasVaultSavePlanner`;
- `AtlasVaultMutationSet`;
- `AtlasVaultSaveResult`;
- `AtlasVaultSaveError`.

The save boundary should:

- accept modified in-memory private state or explicit private mutations;
- encode typed payloads into canonical `AtlasVaultPayloadEnvelope` bytes;
- generate or preserve record metadata;
- encrypt payload envelopes into `AtlasVaultEncryptedRecordEnvelope` values;
- merge encrypted records into a new `AtlasVaultLocalStoreEnvelope`;
- return only encrypted store state to the persistence coordinator;
- never write plaintext bytes, decoded payloads, or hydrated private state to
  disk;
- never mutate `AtlasPublicLocalSnapshot`, public detail-cache metadata, or
  app cache state.

The saver should not choose filesystem paths, prepare directories, write files,
obtain keys, call Keychain, or project state into SwiftUI.

## 5. Mutation Model

The first save API should prefer an explicit mutation set over diffing arbitrary
UI state. A mutation set can represent:

- create: new private record payload and generated metadata;
- update: existing record ID with a new payload and parent revision;
- delete: tombstone record for an existing record ID;
- no-op: unchanged records kept as-is;
- replace-all, only for controlled tests or reviewed migration flows.

Each mutation should carry the minimum metadata needed for deterministic save:

- record ID, if updating or deleting;
- current revision, used as `parent_revision` for updates;
- key ID to use for the encrypted record;
- client-created and client-updated timestamps;
- operation timestamp or logical clock for the new revision;
- typed payload for create/update operations.

The planner should avoid inferring mutations from public cache state. Saved-only
membership, search labels, notes, and draft references remain private.

## 6. Record Identity And Revisions

New records should receive random, non-semantic record IDs. Record IDs must not
include job keys, saved-search names, record types, notes, snippets, generated
document references, organization names, or user context.

Updates should retain the existing record ID. The new encrypted envelope should
receive a fresh revision and set `parent_revision` to the record revision that
was edited. If the parent revision is missing, the first implementation should
fail or mark the save as conflict-prone rather than silently overwriting.

Deletes should create tombstones with the same record ID, a fresh revision, the
previous revision as `parent_revision`, `deleted: true`, and no active payload in
hydrated state. Whether a tombstone ciphertext contains an empty envelope or a
minimal authenticated placeholder should be decided in the implementation phase,
but it must not contain user-private plaintext after encryption.

Revision IDs should be random or otherwise non-semantic. They must not encode
record type, job key, search name, or user text. Conflict siblings remain future
work and should not be collapsed without a reviewed merge policy.

## 7. Payload Encoding

Save code should encode the same common plaintext envelope for every active
record:

- `type`;
- `payload_schema`;
- `payload`;
- `client_created_at`;
- `client_updated_at`.

For `saved_search`, encode `AtlasSavedSearchVaultPayload`, including name,
summary, description, full search request, filters, and timestamps.

For `saved_job`, encode `AtlasSavedJobVaultPayload`, including saved-only job
key, status, notes, applied timestamp, updated timestamp, and any private legacy
tracker ID.

For `application_note`, encode `AtlasApplicationNoteVaultPayload`, including
title, body, note kind, linked job key or record ID, pin state, ordering, and
timestamps.

For `profile_snippet`, encode `AtlasProfileSnippetVaultPayload`, including
title, body, target system, field hint, tags, provenance notes, and timestamps.

For `draft_metadata`, encode `AtlasDraftMetadataVaultPayload`, including linked
job key or saved-job record ID, target system, document type, generated document
reference, draft status, workflow timestamps, personal context reference, and
context summary.

Canonical encoding should be deterministic for tests and compatible with the
existing shared payload-vector expectations. Optional absent fields should follow
the established payload convention.

## 8. Encryption

Save code must use an already unlocked session. It should not obtain
passphrases, unwrap keys, or call Keychain.

Encryption requirements:

- use the unlocked vault key and vault ID from the session;
- derive the per-record key from vault key, vault ID, and record ID;
- generate a fresh 96-bit nonce for every encrypted record save;
- bind record ID, schema version, revision, parent revision, deleted flag, key
  ID, vault format, vault version, and vault ID with AAD;
- never reuse a nonce with the same derived record key;
- never log plaintext, nonce-generation inputs, vault keys, record keys, or
  decrypted payload bytes.

Tests may use deterministic fake nonces only in test vectors. Production save
code must use secure randomness.

## 9. Local Store Update

The save planner should produce a new in-memory `AtlasVaultLocalStoreEnvelope`
or encrypted record collection for the persistence coordinator to write later.

Update rules:

- replace the prior encrypted envelope for the same record ID with the new
  encrypted envelope when saving a normal update;
- append or retain tombstone envelopes according to the conflict policy;
- preserve untouched encrypted records byte-for-byte where possible;
- preserve existing tombstones unless a later compaction policy is reviewed;
- update local-store metadata such as `updated_at` only with non-sensitive
  values;
- keep `vault_metadata` free of job keys, record types, saved-search names,
  notes, snippets, document references, and user context.

The local store writer's overwrite behavior remains explicit. Atomic-write,
backup, recovery, and partial-write behavior belong to the persistence
coordinator or a later atomic-write policy and are not implemented here.

## 10. Error Handling

Future save errors should be typed and non-sensitive:

- `invalidSession`: missing or invalid unlocked session;
- `encodingFailed`: payload envelope could not be encoded;
- `encryptionFailed`: record sealing failed;
- `unsupportedPayloadSchema`: caller attempted to save an unsupported schema;
- `unsupportedRecordVersion`: record metadata targets an unsupported encrypted
  record version;
- `missingParentRevision`: update/delete lacks required parent metadata;
- `staleRevision`: parent revision no longer matches the current local record;
- `conflictDetected`: concurrent sibling revisions require later conflict
  handling;
- `localStoreMergeFailed`: encrypted envelope merge failed;
- `persistenceFailed`: local-store write failure surfaced by the coordinator.

Error values and messages must not include saved-search names, search text,
filters, job keys, notes, snippets, generated document references, plaintext
JSON, record payloads, vault keys, or passphrases.

Partial save failures should leave the previous encrypted local-store envelope
unchanged in memory unless the caller explicitly accepts a complete replacement.
File write failures are handled by the persistence coordinator in a later phase.

## 11. Public Snapshot Boundary

Save-after-hydration must not mutate `AtlasPublicLocalSnapshot` or write through
`AtlasLocalCache`.

The public snapshot must not contain:

- private record payloads;
- private record counts;
- saved-search names or search requests;
- saved-only job keys or saved-job membership;
- notes, snippets, draft metadata, or generated document references;
- encrypted vault paths that reveal private metadata.

Public cache refresh and public detail-cache warmup remain independent. Future
runtime work must not use saved-only membership to drive public cache filenames,
counts, or progress metadata without separate review.

## 12. Logging And Privacy

Save code, diagnostics, and user-visible errors must not log:

- saved-search names, search text, filters, or source IDs;
- job keys, statuses, applied timestamps, or notes;
- profile snippets, provenance notes, or personal context;
- generated document references or draft workflow metadata;
- plaintext payload JSON;
- vault keys, record keys, passphrases, recovery keys, or derived key material.

If logs are needed, use non-sensitive error classes and coarse operation labels
only. Avoid private record counts in locked contexts because counts may reveal
saved membership.

## 13. Tests For Future Implementation

Future test-only implementation should cover:

- saving a new `saved_search` creates an encrypted record with no sentinel
  leakage in plaintext metadata or local-store JSON;
- updating a `saved_job` preserves record ID and sets the previous revision as
  `parent_revision`;
- deleting a record creates a tombstone and excludes the record from active
  hydrated state;
- every save uses a fresh nonce for the same record ID;
- wrong or missing session fails without plaintext output;
- unsupported payload schema and unsupported record version fail safely;
- stale parent revision reports a non-sensitive conflict error;
- local-store merge preserves untouched encrypted records and tombstones;
- public snapshot serialization is unchanged by save planning;
- error string/debug output contains no private sentinels;
- output local-store envelopes contain encrypted records only.

## 14. Deferred

- record saver implementation;
- atomic write code;
- runtime app integration;
- SwiftUI state mutation and view-model wiring;
- migration execution;
- conflict resolution UI;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.

## 15. Recommended Next Loop

Run a Phase 2D-19 review pass, then implement Phase 2D-20 as a test-only record
saver protocol plus vector tests. Keep it below SwiftUI runtime integration and
below migration/cloud behavior.
