# Phase 2D-21 AtlasVault Persistence Coordinator Save Design

## 1. Purpose

Phase 2D-21 designs how encrypted records produced by the record saver will be
merged into an AtlasVault local-store envelope and handed to the persistence
coordinator for a future write. The design keeps the coordinator on encrypted
envelopes only: private record type and private payload values remain inside
ciphertext, while plaintext encrypted-record metadata contains only the reviewed
allowlist needed for storage, authentication, and merge decisions.

## 2. Scope

This phase is design only.

It does not add:

- coordinator save code;
- runtime app integration;
- SwiftUI, `SearchViewModel`, or `AtlasLocalCache` wiring;
- actual local-store writes;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync, device onboarding, or key rotation.

Future implementation should be test-only before any runtime UI or app storage
integration.

## 3. Current Building Blocks

`AtlasVaultRecordSaver` accepts private in-memory mutations and returns
`AtlasVaultEncryptedRecordEnvelope` values. It does not write files, mutate
public snapshots, or expose private payloads in plaintext output.

`AtlasVaultRecordHydrator` decrypts encrypted records into in-memory private
state after unlock. It is the read-side counterpart and remains separate from
save planning.

`AtlasVaultPersistenceCoordinator` currently computes explicit local-store URLs,
prepares parent directories, and loads or saves encrypted
`AtlasVaultLocalStoreEnvelope` values. It does not decrypt records.

`AtlasVaultLocalStoreEnvelope` stores encrypted records plus non-sensitive store
metadata. `AtlasVaultLocalStoreIO` validates and encodes the envelope at
explicit caller-provided URLs with explicit overwrite behavior.

`AtlasVaultPathLocator` computes the local-store URL from an injected root and
vault ID. `AtlasVaultDirectoryPreparer` prepares only the parent directory for
that URL.

The public snapshot and cache split remains outside the vault. Public job cache
data stays public; saved searches, saved jobs, notes, snippets, and draft
metadata stay in encrypted AtlasVault records.

## 4. Save Integration Boundary

Candidate future type names, subject to implementation review:

- `AtlasVaultPersistenceSaveCoordinator`;
- `AtlasVaultLocalStoreMerger`;
- `AtlasVaultSaveTransaction`;
- `AtlasVaultSaveResult`;
- `AtlasVaultLocalStoreMergeError`.

The save integration boundary should:

- accept encrypted record envelopes from the saver;
- load or receive the current encrypted local-store envelope;
- merge incoming encrypted records into a new envelope;
- preserve untouched encrypted records;
- replace records by ID for updates;
- append records with new IDs for creates;
- preserve tombstones and tombstone metadata;
- update only non-sensitive local-store metadata;
- pass the final encrypted envelope to the local-store writer;
- never decode, decrypt, log, or inspect plaintext payloads.

The merger should not obtain keys, call Keychain, choose app paths, prepare
SwiftUI state, mutate public snapshots, execute migration, or perform cloud
sync.

## 5. Store Merge Semantics

The first implementation should validate the existing local-store record list
before converting it to a map by plaintext envelope record ID. Record IDs are
non-semantic and must not contain job keys, search names, notes, snippets,
generated document references, record types, or user context.

Recommended merge rules:

- create inserts an incoming encrypted envelope when its record ID is not
  already present;
- update replaces the existing envelope with the same record ID;
- delete/tombstone replaces the active envelope for the same record ID with the
  deleted envelope;
- unknown existing records are preserved unchanged;
- existing tombstones are preserved unless replaced by a newer envelope for the
  same record ID;
- duplicate record IDs already present in the existing store fail with a
  non-sensitive error before the map is built;
- duplicate record IDs within the incoming batch fail with a non-sensitive
  error.

Failing on duplicate existing or incoming IDs is the recommended first
implementation. It is easier to reason about than a last-writer policy and
avoids silently discarding an encrypted envelope before conflict behavior is
designed.

## 6. Revision And Conflict Behavior

When parent revision is available, the merger should compare it with the current
stored envelope revision for that record ID before replacement.

Recommended first behavior:

- matching parent revision allows update or tombstone replacement;
- missing current record for an update/delete fails as a non-sensitive stale
  revision or missing-record error;
- mismatched parent revision fails closed as a stale revision or conflict error;
- creates with an existing record ID fail as duplicate or conflict;
- conflict resolution UI and sibling-revision storage are deferred.

Conflict detection must use only encrypted-record envelope metadata. The merger
must not decrypt payloads or inspect private record type to decide conflicts.

## 7. Local-Store Metadata Update

The merger may update local-store metadata only with non-sensitive values, such
as:

- store `updated_at` or a logical clock;
- local schema or store version when explicitly reviewed;
- non-sensitive vault format metadata already present in the envelope.

The merger should preserve:

- supported store version;
- vault metadata that contains no private user facts;
- unknown forward-compatible metadata if the local-store contract allows it.

Store metadata must not include saved-only job keys, saved-search names, notes,
snippets, draft document references, private record counts, record type strings,
or public-cache hints derived from saved membership.

## 8. Write And Atomicity Boundary

The current local-store writer has explicit overwrite behavior. A future
coordinator save path should keep overwrite policy explicit and avoid
opportunistic replacement of existing files.

Atomic-write behavior remains a separate design and implementation boundary.
Open questions include:

- whether to introduce a dedicated atomic writer beneath the persistence
  coordinator;
- whether the coordinator should write to a temporary sibling file and replace;
- whether backup files are allowed, and if so how they avoid plaintext and stale
  private data risk;
- whether fsync or platform-specific durability calls are required;
- how to report partial-write failures without exposing private values.

Until atomic behavior is implemented, tests should keep merge planning separate
from actual file writes and should assert that write failures do not mutate the
in-memory original envelope.

## 9. Error Handling

Future errors should be typed and non-sensitive:

- missing store, when save requires an existing envelope;
- corrupt store;
- unsupported store version;
- duplicate existing records;
- duplicate incoming encrypted records;
- missing current record for update/delete;
- stale parent revision;
- directory preparation failure;
- write failure;
- invalid local-store metadata update;
- unsupported encrypted-record version.

Error values and messages must not include private payloads, record type strings
where avoidable, saved-search names, search text, filters, job keys, notes,
profile snippets, draft references, decrypted JSON, vault keys, or file contents.

## 10. Public Snapshot Boundary

Coordinator save integration must not mutate `AtlasPublicLocalSnapshot`,
`AtlasLocalCache`, public detail cache entries, or `SearchViewModel` state.

Public cache data remains independent. The save path must not add:

- saved-only job keys;
- private record counts;
- notes, snippets, draft metadata, or generated document references;
- private record IDs or revisions;
- flags that let public cache consumers infer saved membership.

Any later projection from private state into UI must be reviewed as runtime
integration and is outside this phase.

## 11. Logging And Privacy

Logging, metrics, and diagnostics may include only non-sensitive classes such as
operation outcome, number of incoming encrypted envelopes, and error category.

They must not include:

- plaintext payloads;
- decrypted JSON;
- record type strings if avoidable;
- saved-search names, query text, or filters;
- job keys or saved-job membership;
- notes;
- profile snippets;
- generated document references;
- vault keys, derived record keys, nonces, or ciphertext bodies.

## 12. Tests For Future Implementation

Future test-only implementation should cover:

- merge create adds an encrypted record;
- merge update replaces the same record ID;
- merge tombstone preserves the deleted flag and replaces the active record;
- untouched encrypted records are preserved;
- existing tombstones are preserved;
- duplicate existing record IDs fail before mapping;
- duplicate incoming IDs fail safely;
- stale parent revision fails safely;
- missing current record for update/delete fails safely;
- local-store metadata updates contain no private data;
- serialized local-store envelope contains no fake private sentinels;
- serialized local-store envelope contains no plaintext private record type
  strings;
- public snapshot and public cache state remain untouched;
- write failure leaves the original store untouched once an atomic writer exists.

## 13. Deferred

- local-store merger implementation;
- coordinator save implementation;
- atomic writer;
- SwiftUI integration;
- `SearchViewModel` or `AtlasLocalCache` integration;
- migration execution;
- conflict resolution UI;
- cloud sync;
- device onboarding;
- key rotation;
- cleanup of old plaintext snapshots.

## 14. Recommended Next Loop

Review Phase 2D-21, then implement Phase 2D-22 as test-only local-store merger
and coordinator save tests. That implementation should still precede SwiftUI
runtime integration and should merge encrypted envelopes only.
