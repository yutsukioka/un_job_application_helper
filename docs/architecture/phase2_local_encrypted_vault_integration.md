# Phase 2 Local Encrypted Vault Integration Plan

Status: design only. This plan defines the local AtlasVault integration path
before cloud provider work. It does not implement Swift app behavior, FastAPI
behavior, data migration, Keychain storage, export/import commands, or cloud
sync. It does not claim production readiness.

## Purpose

Phase 2 moves private user state toward AtlasVault records while keeping public
job cache data separate. The immediate goal is to define how the current Apple
and local FastAPI plaintext state will map to encrypted local vault records so
later implementation can proceed without treating plaintext JSON files as sync
sources.

## Current Plaintext Surfaces

Current Apple local cache:

- `AtlasLocalCache` writes `atlas-local-snapshot.json` under Application
  Support.
- `AtlasLocalSnapshot` serializes `savedSearches: [AtlasSavedSearch]`.
- `AtlasLocalSnapshot` serializes `savedJobs: [AtlasApplicationRecord]`.
- The same snapshot also serializes public job cache data such as health,
  search results, sources, and source runs.

Current local FastAPI state:

- `/api/saved-searches` reads and writes `private/jobagg/saved_searches.json`.
- `/api/tracker` reads and writes `private/jobagg/application_tracker.json`.
- These endpoints are local-only plaintext endpoints and must not become sync
  endpoints.

Public job data and private user state must remain separate. Public job records,
search indexes, source runs, taxonomies, and cached job details are not vault
records unless a future feature adds private annotations to them.

## User Data That Should Become Vault Records

The following user-controlled or user-specific data should become encrypted
AtlasVault records:

- saved searches, including search text, filters, names, summaries, and
  timestamps;
- saved jobs and application tracker records, including selected job keys,
  user status, notes, applied dates, and update timestamps;
- future application notes;
- future reusable profile snippets;
- future draft metadata, including generated document references and per-job
  draft workflow state.

The following data should remain outside the private vault:

- public job database rows and search indexes;
- public job detail cache files;
- source summaries, sync run summaries, and public taxonomy data;
- application logs and diagnostics, which must still avoid private payloads;
- generated application documents themselves, unless a future phase explicitly
  defines encrypted document storage.

## Proposed Encrypted Record Types

Record type is part of the encrypted payload. It should not be copied into
plaintext record metadata because even the type can reveal user behavior.

All records keep the AtlasVault v1 encrypted-record envelope:

- plaintext `id`: random record ID only;
- plaintext `schema_version`;
- plaintext `revision`;
- plaintext `parent_revision`;
- plaintext `deleted`;
- plaintext `key_id`;
- plaintext `nonce`;
- encrypted `ciphertext`.

The plaintext before encryption should use this common shape:

```json
{
  "type": "saved_search",
  "payload_schema": 1,
  "payload": {},
  "client_created_at": "ISO-8601 UTC timestamp",
  "client_updated_at": "ISO-8601 UTC timestamp"
}
```

### `saved_search`

Maps current `AtlasSavedSearch` and `SavedSearchModel` state.

Encrypted payload fields:

- `name`;
- `summary` or `description`;
- full `request`, including search text and every filter list/value;
- `created_at`;
- `updated_at`;
- optional migration source metadata, if needed for dry-run reports.

No saved-search name, query text, filter value, or summary should remain in
plaintext record metadata.

### `saved_job`

Maps current `AtlasApplicationRecord` and local tracker `ApplicationRecord`
state.

Encrypted payload fields:

- `job_key`;
- `status`;
- `notes`, for the current tracker model;
- `applied_at`;
- `updated_at`;
- optional encrypted references to related application notes or draft metadata;
- optional migration source metadata, if needed for dry-run reports.

`job_key` must be encrypted. Although the job record itself may be public, the
fact that a user saved or applied to that job is private.

### `application_note`

Supports future note records and later normalization of tracker notes.

Encrypted payload fields:

- note body;
- title or local label;
- encrypted link to a saved job, job key, or draft;
- note kind, such as `general`, `screening`, `interview`, or `follow_up`;
- created and updated timestamps;
- local-only ordering or pinning state.

Phase 2B may either keep legacy `ApplicationRecord.notes` inside `saved_job` for
lossless migration or create separate `application_note` records while retaining
a reversible mapping in the migration report. The first implementation should
favor lossless behavior over premature normalization.

### `profile_snippet`

Supports future reusable personal profile content.

Encrypted payload fields:

- snippet title;
- snippet body;
- target system or field hint;
- tags;
- source or provenance notes;
- created and updated timestamps.

All personal context, work-history phrasing, reusable answers, and field hints
should be encrypted.

### `draft_metadata`

Supports future generated-document and application-draft workflow state without
placing generated documents themselves into the vault.

Encrypted payload fields:

- encrypted link to saved job or job key;
- target system;
- document type;
- generated document references, such as local path aliases, artifact IDs, or
  export names;
- draft status;
- generated, reviewed, submitted, or archived timestamps;
- any personal context used to produce the draft.

Generated document references can reveal application intent and must be
encrypted. The referenced document files remain out of scope for Phase 2 unless
a later phase defines encrypted document storage.

## Plaintext Metadata Allowlist

The local vault store and future sync boundary may keep only the following
record-level fields in plaintext:

- random record ID;
- schema version;
- revision ID;
- parent revision ID;
- tombstone flag;
- key ID;
- nonce;
- updated timestamp or logical clock, only if needed for local merge or future
  sync.

Prefer logical clocks or coarse updated timestamps when exact timestamps are not
required. Do not place job keys, saved-search names, record type, status,
filters, notes, profile text, document names, generated document paths, or any
personal context in plaintext metadata.

## Apple Local Cache Direction

Later Apple implementation should stop serializing private user state into
`atlas-local-snapshot.json`.

Target direction:

- remove `savedSearches` and `savedJobs` from the plaintext snapshot;
- keep public job cache data in the existing local cache path, including health,
  public search results, source summaries, source runs, and public job details;
- load saved searches and saved jobs from the local encrypted vault after the
  vault is unlocked;
- expose decrypted saved searches and saved jobs to SwiftUI as in-memory state
  only;
- avoid using decrypted saved-job keys to persist private linkage in the public
  snapshot;
- continue treating detail cache warmup as public job cache work, and avoid
  using saved-only job keys in ways that leak private saved-job membership
  through public cache filenames, counts, or progress metadata.

Possible future model split:

- `AtlasPublicLocalSnapshot`: public cache fields only;
- `AtlasVaultSession`: unlocked in-memory decrypted private records;
- `AtlasVaultStore`: encrypted record persistence and import/export boundary.

Phase 2C should design this split before Phase 2D wires Swift Keychain and
runtime unlock behavior.

## FastAPI Local State Direction

Later FastAPI implementation should avoid treating plaintext saved-search and
tracker JSON files as the source of sync truth.

Target direction:

- introduce a local encrypted vault store that is separate from the public
  `jobagg` SQLite database;
- keep encrypted vault storage out of tracked source files;
- keep `/api/search`, public job detail, source, taxonomy, and run endpoints
  separate from private vault operations;
- keep existing plaintext endpoints local-only during transition, if still
  needed for compatibility;
- do not expose `/api/saved-searches` or `/api/tracker` as cloud sync endpoints;
- add optional local migration or export commands that read plaintext JSON,
  write encrypted vault records, and leave originals untouched until the user
  explicitly chooses cleanup in a later phase.

Phase 2A should build the local vault file/store and `.atlasvault`
export/import primitives without reading current plaintext JSON. Phase 2B should
add migration helpers from `saved_searches.json` and `application_tracker.json`.

## `.atlasvault` Export And Import Plan

Manual encrypted export/import is the first non-cloud portability path.

Proposed file extension: `.atlasvault`.

Proposed outer envelope:

```json
{
  "format": "atlasvault-export",
  "version": 1,
  "export_id": "random-uuid",
  "created_at": "ISO-8601 UTC timestamp",
  "vault_metadata": {},
  "records": []
}
```

The outer envelope may contain non-sensitive format metadata only. Any metadata
that names jobs, searches, drafts, profile content, local document references,
or migration sources must be inside encrypted records.

`vault_metadata` should be the AtlasVault v1 metadata object containing crypto
suite information and wrapped vault keys. Passphrase and recovery-key wrapping
should use the existing key-wrap model:

- derive wrapping key locally with Argon2id;
- wrap the random vault key with AES-256-GCM;
- serialize only wrapped keys, salts, nonces, and crypto parameters;
- never serialize the passphrase, recovery key, or raw vault key.

`records` should contain encrypted AtlasVault record envelopes, including
tombstones. The export file must not contain plaintext saved searches, tracker
records, notes, profile snippets, generated document references, raw vault keys,
passphrases, recovery keys, or decrypted payloads.

Import flow:

1. Parse and validate the outer envelope and supported versions.
2. Ask the user for a passphrase or recovery key locally.
3. Unwrap the vault key locally.
4. Decrypt records locally.
5. Merge records locally using record ID, revision, parent revision, tombstone,
   and update clock metadata.
6. Write only encrypted vault state to disk by default.

## Migration Safety Notes

Phase 2 migration must be additive and reversible:

- dry runs should report how many plaintext records would map to each encrypted
  record type without writing vault files;
- migration should never delete `saved_searches.json` or
  `application_tracker.json`;
- migration should write a new encrypted vault file or a staged output path;
- cleanup of plaintext originals must be a separate future user-confirmed
  operation;
- migration logs must not print search text, filters, job keys, notes, profile
  snippets, document references, or decrypted payloads;
- migration should use random vault record IDs, not saved-search names or job
  keys as record IDs;
- duplicate handling should be deterministic after decryption, but duplicate
  keys or names should not be exposed in plaintext vault metadata.

## Test Perspectives

Phase 2 implementation should include tests for:

- no plaintext sentinel in the local vault file;
- no plaintext sentinel in `.atlasvault` exports;
- migration dry run does not delete or rewrite plaintext originals;
- decrypt round trip for each proposed record type;
- wrong password or recovery key fails without partial plaintext output;
- corrupt vault metadata or encrypted record fails safely;
- saved search mapping preserves name, summary, search text, filters, and
  timestamps after decrypting locally;
- saved job mapping preserves job key, status, notes, applied date, and updated
  timestamp after decrypting locally;
- application note, profile snippet, and draft metadata payloads stay encrypted
  at rest;
- Apple cache serialization no longer writes saved searches or saved jobs into
  `atlas-local-snapshot.json`;
- Apple public detail cache files, counts, and progress metadata do not reveal
  saved-only job keys or saved-job membership;
- plaintext record type is not leaked in encrypted-record metadata;
- tombstones merge without requiring plaintext payload inspection.

## Implementation Phases

### Phase 2A: Python Local Vault Store And Export/Import

Build local Python storage around `packages/vaultsync`:

- local encrypted vault file read/write;
- `.atlasvault` export/import envelope validation;
- passphrase and recovery-key wrapping using the existing crypto contract;
- round-trip tests and plaintext-sentinel tests;
- no migration from existing plaintext JSON yet.

### Phase 2B: Migration Helpers From Plaintext JSON

Add opt-in migration helpers:

- read `saved_searches.json` and `application_tracker.json`;
- map records into encrypted `saved_search` and `saved_job` payloads;
- support dry-run reports;
- write staged encrypted vault output;
- leave plaintext originals untouched;
- avoid logging private payload values.

Phase 2B dry runs are count-only and non-sensitive. They must use caller-provided
in-memory objects or explicit caller-provided paths; no helper should default to
real files under `private/`. Dry runs may report counts, skipped-entry counts,
and warning codes, but not saved-search names, search text, filters, job keys,
statuses, notes, document references, or decrypted payloads. Staged encrypted
local stores or `.atlasvault` exports are written only to caller-provided output
paths, and overwrite must be explicit. Cleanup or deletion of plaintext originals
is a separate future user-confirmed phase.

### Phase 2C: Apple-Side Model Mapping Design

Design the Apple model transition:

- split public local snapshot from private decrypted vault state;
- define Swift Codable payloads matching encrypted record types;
- define in-memory unlocked state for saved searches and saved jobs;
- define compatibility behavior while plaintext local endpoints still exist;
- define cache tests proving private state is not serialized in plaintext.

See `docs/architecture/phase2c_apple_vault_model_mapping.md` for the detailed
Apple-side public/private state boundary, proposed Swift model split, payload
mapping, locked/unlocked vault behavior, transition plan, and future Swift test
perspectives.

### Phase 2D: Swift Keychain And Vault Integration Later

Implement Apple runtime integration after the local model design is stable:

- Keychain-backed vault unlock state;
- encrypted record read/write from Swift;
- local UI state hydration from decrypted records;
- no cloud sync;
- no production-readiness claim until threat model, recovery, backups, and
  platform storage behavior are reviewed.

## Risks And Open Questions

- Exact timestamp leakage may be unnecessary; Phase 2A should decide whether a
  logical clock is enough.
- Legacy tracker notes can be stored inside `saved_job` initially or normalized
  into `application_note`; the safer first migration is lossless storage inside
  `saved_job`.
- Existing Apple UI expects saved searches and saved jobs during offline cache
  use; Phase 2C must define locked-vault behavior.
- Public detail cache warmup must be revisited because warming details for
  saved-only jobs can reveal private saved-job membership through public cache
  metadata.
- Search filters can include personally revealing location, grade, career, or
  scoring preferences; every filter must stay encrypted.
- Local encrypted storage path conventions must differ between packaged apps,
  local development, and tests.
- Generated document references are sensitive even when the documents are not
  stored in the vault.
- This plan does not yet define cloud account identity, device onboarding, key
  rotation, recovery UX, or secure deletion of legacy plaintext files.
