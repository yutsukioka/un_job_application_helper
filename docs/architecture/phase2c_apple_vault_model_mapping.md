# Phase 2C Apple Vault Model Mapping Design

Status: design only. This document defines the future Apple-side model split
for AtlasVault integration. It does not implement Swift runtime behavior,
Keychain storage, passphrase prompts, export/import UI, migration execution,
cloud sync, device onboarding, key rotation, or cleanup of plaintext originals.

## 1. Current Apple State Map

The current SwiftUI app keeps public job cache state and private user state in
the same local snapshot.

### `AtlasLocalCache`

File inspected: `apps/apple/Sources/AtlasUI/AtlasLocalCache.swift`.

`AtlasLocalCache` owns the Application Support cache directory:

- snapshot file: `Atlas/atlas-local-snapshot.json`;
- detail cache directory: `Atlas/JobDetails`;
- detail staging directory: `Atlas/JobDetails.staging`;
- detail backup directory: `Atlas/JobDetails.previous`.

`loadSnapshot()` reads and decodes `AtlasLocalSnapshot`.
`saveSnapshot(_:)` encodes the full snapshot as sorted-key JSON and writes it
atomically. `commitSnapshot(_:replacingDetailsWith:)` updates the snapshot and,
when provided, swaps staged public job details into the detail cache. `saveDetail`
persists individual `AtlasJobDetail` JSON files keyed by sanitized public
`jobKey`.

### `AtlasLocalSnapshot`

File inspected: `apps/apple/Sources/AtlasUI/AtlasLocalCache.swift`.

Current fields:

- `savedAt`: cache timestamp.
- `baseURL`: local API base URL.
- `health`: `AtlasHealthSummary`.
- `searchResponse`: `AtlasSearchResponse`.
- `savedSearches`: `[AtlasSavedSearch]`.
- `savedJobs`: `[AtlasApplicationRecord]`.
- `sources`: `[AtlasSourceSummary]`.
- `recentRuns`: `[AtlasSourceRun]`.

Public cache state in this structure:

- `savedAt`;
- `baseURL`;
- `health`;
- `searchResponse`;
- `sources`;
- `recentRuns`.

Private user state in this structure:

- `savedSearches`;
- `savedJobs`.

The private fields currently serialize into `atlas-local-snapshot.json` in
plaintext.

### `AtlasSavedSearch`

File inspected: `apps/apple/Sources/AtlasUI/AtlasAPIClient.swift`.

Current fields:

- `name`;
- `description`;
- `request: AtlasSearchRequest`;
- `createdAt`;
- `updatedAt`.

`AtlasSearchRequest` includes user-selected search text and filters such as
organizations, source IDs, cities, countries, grade codes, CCOG families,
capability tags, contract groups, seniority groups, work modalities, volunteer
filters, date bounds, result limit, offset, and sort.

All `AtlasSavedSearch` fields are private once they represent a user-saved
search. The name, description, request text, and filters must move into encrypted
vault payloads.

### `AtlasApplicationRecord`

File inspected: `apps/apple/Sources/AtlasUI/AtlasAPIClient.swift`.

Current fields:

- `id`;
- `jobKey`;
- `status`;
- `notes`;
- `appliedAt`;
- `updatedAt`.

All fields are private user state. A public job key becomes private when it
identifies a user-saved, tracked, drafted, or applied job.

### `AtlasSearchViewModel`

File inspected: `apps/apple/Sources/AtlasUI/SearchViewModel.swift`.

Relevant current published state:

- live search UI state: `query`, `filters`, `sortOrder`, and
  `displayedResultLimit`;
- public job/search cache state: `results`, `total`, `facets`,
  `facetLabels`, `filterAvailabilityFacets`, `filterAvailabilityLabels`,
  `unclassifiedCount`, `sources`, `recentRuns`, `serverState`,
  `lastUpdated`, `cacheSavedAt`, `cachedJobCount`, `cachedDetailCount`,
  `detailCacheTotal`, detail-cache progress fields, and
  `refreshIntervalHours`;
- private user state: `savedSearches` and `savedJobs`;
- mixed/private-risk state: `cachedSnapshot` because it currently contains
  `savedSearches` and `savedJobs`;
- public cache state: `cachedAllJobs`, because it derives from
  `snapshot.searchResponse.results`.

Saved searches are loaded in two ways:

- `refreshSidebarData(forceServer:)` calls `client.savedSearches()` when
  refreshing from the local server.
- `applySidebarData(_:)` assigns `savedSearches = snapshot.savedSearches` when
  using a cached snapshot.

Saved jobs are loaded in two ways:

- `refreshSidebarData(forceServer:)` calls `client.trackerRecords()`.
- `applySidebarData(_:)` assigns sorted `snapshot.savedJobs`.

Snapshots are fetched and saved in:

- `fetchSnapshot(health:)`, which concurrently calls `/api/search`,
  `/api/saved-searches`, `/api/tracker`, `/api/sources`, and `/api/updates`,
  then returns an `AtlasLocalSnapshot` containing all five result groups;
- `refresh()`, which commits the fetched snapshot with
  `AtlasLocalCache.commitSnapshot`;
- `snapshotWithCurrentSavedAt(_:)`, which copies all current snapshot fields,
  including private saved-search and saved-job arrays.

Cached job details are saved by:

- `AtlasLocalCache.saveDetail` from `fetchAndCacheDetail(jobKey:)`;
- `AtlasLocalCache.saveDetail(_:jobKey:to:)` during detail-cache staging;
- `AtlasLocalCache.commitSnapshot` when a staged detail directory replaces the
  active detail cache.

`detailJobKeys(for:)` currently combines public search-result job keys with
private saved-job keys. In the future, public detail warmup can continue to use
search-result job keys while private saved-job detail warmup must be driven from
unlocked in-memory vault state rather than the public snapshot. It must also
avoid creating a public-cache membership signal for saved-only jobs.

### `AtlasAPIClient`

File inspected: `apps/apple/Sources/AtlasUI/AtlasAPIClient.swift`.

Public job/cache endpoints:

- `search(_:)` -> `/api/search`;
- `jobDetail(_:)` -> `/api/job-detail`;
- `updates()` -> `/api/updates`;
- `sources()` -> `/api/sources`;
- `health()` -> `/api/health`.

Private local compatibility endpoints:

- `savedSearches()` -> `/api/saved-searches`;
- `saveSearch(name:request:summary:)` -> `/api/saved-searches`;
- `deleteSavedSearch(name:)` -> `/api/saved-searches/{name}`;
- `trackerRecords()` -> `/api/tracker`;
- `saveJob(_:)` -> `/api/tracker/jobs/{job_key}`;
- `deleteTrackerRecord(id:)` -> `/api/tracker/{id}`.

The private endpoints are local-only compatibility surfaces. They must not
become cloud sync endpoints.

### `SearchScreen` and Saved UI

File inspected: `apps/apple/Sources/AtlasUI/SearchScreen.swift`.

`AtlasSidebarView` reads `viewModel.savedSearches` to render saved-search
navigation rows and context-menu delete actions. `SavedPanel` reads both
`viewModel.savedJobs` and `viewModel.savedSearches`. `SavedJobRecordRow` renders
`record.jobKey`, `record.status`, and `record.updatedAt`, and constructs a
placeholder `JobSearchResult` from the saved record.

Those UI values should only be available after the vault is unlocked in future
implementations.

## 2. Public vs Private State Boundary

### Remains Public Local-Cache State

The following may remain in an Apple public local cache:

- API health summary;
- public search results;
- public facets and facet labels;
- public filter-availability facets and labels derived from public results;
- source summaries;
- source run/update history;
- public job detail cache files;
- public taxonomies/facets if added later;
- cache refresh timestamps that do not reveal private saved state;
- local API base URL, if it does not encode private user data.

Public job detail cache files can remain outside the private vault because they
represent public vacancy data. The cache must not encode private saved-job
membership.

Live unsaved search inputs and filters are not public persisted cache state.
They may remain ordinary in-memory UI state while the user is searching, but
once saved as a search they become private vault payload.

### Moves To Encrypted Vault State

The following must move to AtlasVault records:

- saved searches;
- saved-search names and descriptions;
- saved-search text and filters;
- saved jobs;
- application tracker status;
- tracker notes;
- applied timestamps tied to user workflow;
- saved-job linkage;
- private application workflow state;
- future application notes;
- future profile snippets;
- future draft metadata;
- generated document references.

Even when a job record is public, `jobKey` becomes private when it identifies a
job the user saved, tracked, drafted, applied to, or annotated.

## 3. Proposed Swift Model Split

This section proposes Swift type boundaries only. It does not implement them.

### `AtlasPublicLocalSnapshot`

Purpose: replacement for `AtlasLocalSnapshot` as the plaintext public cache
container.

Suggested fields:

- `savedAt: Date`;
- `baseURL: URL`;
- `health: AtlasHealthSummary`;
- `searchResponse: AtlasSearchResponse`;
- `sources: [AtlasSourceSummary]`;
- `recentRuns: [AtlasSourceRun]`.

Plaintext/encrypted boundary: plaintext public cache only. It must not contain
`savedSearches`, `savedJobs`, private job-key linkage, notes, profile snippets,
draft metadata, or generated document references.

Lifetime: persisted between launches.

Storage location: current Application Support `Atlas/atlas-local-snapshot.json`
path or a compatibility-renamed successor path.

Mapping: current `AtlasLocalSnapshot` minus `savedSearches` and `savedJobs`.

### `AtlasVaultSession`

Purpose: unlocked in-memory view of private vault records for SwiftUI.

Suggested fields:

- `state: locked | unlocked | missing | corrupt | unavailable`;
- `savedSearches: [AtlasSavedSearch]`;
- `savedJobs: [AtlasApplicationRecord]`;
- future `applicationNotes`;
- future `profileSnippets`;
- future `draftMetadata`;
- non-sensitive unlock failure class for UI.

Plaintext/encrypted boundary: decrypted private payloads are held in memory only
while unlocked. They must never be encoded into `AtlasPublicLocalSnapshot`.

Lifetime: process memory. Clears on lock, failed unlock reset, account switch,
or app lifecycle event chosen by a later security design.

Storage location: none for decrypted values. The encrypted vault file is handled
by `AtlasVaultStore`.

Mapping: hydrates current `savedSearches` and `savedJobs` published properties.

### `AtlasVaultStore`

Purpose: platform-side owner of encrypted local vault file IO.

Responsibilities:

- read encrypted vault metadata and encrypted record envelopes;
- write encrypted vault metadata and encrypted record envelopes;
- import/export `.atlasvault` envelopes in future UI loops;
- provide encrypted records to a crypto layer after unlock.

Plaintext/encrypted boundary: persists encrypted vault state only. It may expose
decrypted payloads only to `AtlasVaultSession` after local decrypt.

Lifetime: persisted encrypted file plus short-lived read/write operations.

Storage location: Application Support under a separate private vault path, not
inside the public job cache snapshot.

Mapping: Swift equivalent of Phase 2A local store/export envelopes.

### `AtlasVaultRecord`

Purpose: Swift representation of the AtlasVault encrypted record envelope.

Plaintext fields:

- random `id`;
- `schemaVersion`;
- `revision`;
- `parentRevision`;
- `deleted`;
- `keyID`;
- `nonce`;
- `ciphertext`.

Plaintext/encrypted boundary: does not expose record type, job key, saved-search
name, status, notes, profile text, or generated document references.

Lifetime: persisted encrypted record and in-memory encrypted record list.

Mapping: mirrors Python `EncryptedRecord`.

### `AtlasVaultPayload`

Purpose: internal decrypted wrapper around typed private payloads.

Suggested fields:

- `type`;
- `payloadSchema`;
- `payload`;
- `clientCreatedAt`;
- `clientUpdatedAt`.

Plaintext/encrypted boundary: exists only after decrypt in memory. Never stored
in public cache or logs.

Lifetime: in-memory only.

Mapping: mirrors Python `PlaintextRecord` payload shape.

### `AtlasSavedSearchVaultPayload`

Purpose: decrypted payload for `saved_search`.

Fields:

- `name`;
- `summary` or `description`;
- `request: AtlasSearchRequest`;
- `createdAt`;
- `updatedAt`.

Mapping: converts to/from `AtlasSavedSearch`.

### `AtlasSavedJobVaultPayload`

Purpose: decrypted payload for `saved_job`.

Fields:

- `jobKey`;
- `status`;
- `notes`;
- `appliedAt`;
- `updatedAt`;
- optional legacy tracker `id`.

Mapping: converts to/from `AtlasApplicationRecord`.

### `AtlasApplicationNoteVaultPayload`

Purpose: future decrypted application note payload.

Fields:

- note body;
- note kind;
- encrypted link to saved job or job key;
- created and updated timestamps;
- local ordering or pinning metadata.

Mapping: no current Swift model; future feature state.

### `AtlasProfileSnippetVaultPayload`

Purpose: future reusable personal profile content.

Fields:

- title;
- body;
- target system or field hint;
- tags;
- provenance notes;
- created and updated timestamps.

Mapping: no current Swift model; future feature state.

### `AtlasDraftMetadataVaultPayload`

Purpose: future private draft workflow metadata.

Fields:

- encrypted link to saved job or job key;
- target system;
- document type;
- generated document references;
- draft status;
- generated/reviewed/submitted/archived timestamps;
- personal context used to produce the draft.

Mapping: no current Swift model; future document workflow state.

## 4. Codable Payload Mapping

These examples are plaintext before encryption only. They must never serialize
into `AtlasPublicLocalSnapshot`.

### `saved_search`

```json
{
  "type": "saved_search",
  "payload_schema": 1,
  "payload": {
    "name": "saved-search-name",
    "summary": "Open programme roles",
    "description": "Open programme roles",
    "request": {
      "text": "programme",
      "status": ["open"],
      "organizations": ["UNICEF"],
      "source_ids": ["unicef_pageup"],
      "cities": [],
      "countries_iso3": [],
      "grade_codes": ["P-3"],
      "ccog_families": [],
      "capability_tags": [],
      "contract_groups": [],
      "seniority_groups": [],
      "work_modalities": [],
      "volunteer_kinds": [],
      "unv_categories": [],
      "unv_volunteer_types": [],
      "closing_date_to": null,
      "include_low_confidence": false,
      "include_facets": true,
      "limit": 50,
      "offset": 0,
      "sort": "closing_date_asc"
    }
  },
  "client_created_at": "2026-01-01T00:00:00Z",
  "client_updated_at": "2026-01-01T00:00:00Z"
}
```

### `saved_job`

```json
{
  "type": "saved_job",
  "payload_schema": 1,
  "payload": {
    "id": "legacy-local-tracker-id",
    "job_key": "source:public-job-id",
    "status": "drafting",
    "notes": "private note text",
    "applied_at": null,
    "updated_at": "2026-01-01T00:00:00Z"
  },
  "client_created_at": "2026-01-01T00:00:00Z",
  "client_updated_at": "2026-01-01T00:00:00Z"
}
```

### `application_note`

```json
{
  "type": "application_note",
  "payload_schema": 1,
  "payload": {
    "job_key": "source:public-job-id",
    "kind": "general",
    "title": "Interview prep",
    "body": "private note text",
    "pinned": false
  },
  "client_created_at": "2026-01-01T00:00:00Z",
  "client_updated_at": "2026-01-01T00:00:00Z"
}
```

### `profile_snippet`

```json
{
  "type": "profile_snippet",
  "payload_schema": 1,
  "payload": {
    "title": "Monitoring summary",
    "body": "private reusable profile text",
    "target_system": "INSPIRA",
    "field_hint": "summary",
    "tags": ["monitoring", "reporting"]
  },
  "client_created_at": "2026-01-01T00:00:00Z",
  "client_updated_at": "2026-01-01T00:00:00Z"
}
```

### `draft_metadata`

```json
{
  "type": "draft_metadata",
  "payload_schema": 1,
  "payload": {
    "job_key": "source:public-job-id",
    "target_system": "UNICEF",
    "document_type": "cover_letter",
    "document_ref": "private-local-artifact-alias",
    "status": "reviewing",
    "generated_at": "2026-01-01T00:00:00Z"
  },
  "client_created_at": "2026-01-01T00:00:00Z",
  "client_updated_at": "2026-01-01T00:00:00Z"
}
```

## 5. Locked and Unlocked Vault UX Model

This is model behavior, not UI implementation.

- App launched while vault is locked: public search cache may load from
  `AtlasPublicLocalSnapshot`; saved searches, saved jobs, notes, profile
  snippets, and draft metadata stay empty or locked-placeholder state.
- App launched with no vault yet: public cache works normally; private panes show
  no private data and may offer future create/import actions.
- User has public cache but vault is locked: search results, public job details,
  source summaries, and updates remain usable. Saved-search names and saved-job
  rows should not appear until unlock.
- User unlocks vault successfully: `AtlasVaultSession` decrypts records locally
  and hydrates `savedSearches`, `savedJobs`, and future private state in memory.
- User enters wrong passphrase: unlock fails with a non-sensitive error class.
  No partial saved search, job key, note, profile text, or draft reference is
  exposed.
- Vault file missing: public cache remains available; private vault state becomes
  missing/unavailable until create or import.
- Vault file corrupt: public cache remains available; private state is not
  partially hydrated.
- Vault key unavailable: public cache remains available; private state remains
  locked/unavailable.
- User imports `.atlasvault`: import should parse encrypted envelope, ask for
  local unlock material, decrypt and merge locally, and never route plaintext
  through the local FastAPI service or a cloud service.
- User exports `.atlasvault`: export should write encrypted vault metadata and
  encrypted records only.
- User continues using public job search while private vault is locked: allowed.
  Saving a search or job should either require unlock first or queue only a
  non-persisted action that cannot leak into the public snapshot.

## 6. Transition Plan for `AtlasLocalCache`

Future implementation should transition from `AtlasLocalSnapshot` to
`AtlasPublicLocalSnapshot`.

Required behavior:

- remove `savedSearches` and `savedJobs` from the plaintext snapshot in a later
  implementation phase;
- keep public job cache fields: `savedAt`, `baseURL`, `health`,
  `searchResponse`, `sources`, and `recentRuns`;
- keep public job detail cache under `Atlas/JobDetails`;
- stop using private saved-job keys in public snapshot serialization;
- avoid warming a public detail cache from saved-only job keys in a way that
  leaks private saved-job membership through filenames, counts, or progress
  messages;
- drive any private saved-job detail workflow from unlocked in-memory vault
  state, not from public snapshot data;
- stage compatibility migration and test it before changing runtime behavior;
- do not delete old plaintext snapshots automatically in the first
  implementation;
- require explicit user confirmation for any future cleanup or deletion of old
  plaintext originals.

Compatibility option:

1. Add `AtlasPublicLocalSnapshot` alongside `AtlasLocalSnapshot`.
2. Add tests that encode `AtlasPublicLocalSnapshot` and assert private sentinel
   values are absent.
3. Add a read compatibility path that can load old snapshots for public fields
   while ignoring private fields unless an explicit future migration flow is
   running.
4. Write only public snapshots after the compatibility path is proven.
5. Move private saved state to `AtlasVaultSession` and encrypted
   `AtlasVaultStore`.

## 7. Compatibility With Current Local API

The current local API remains useful during transition, but its private
endpoints must be treated as local compatibility surfaces.

Public cache endpoints can remain public-cache sources:

- `/api/search`;
- `/api/job-detail`;
- `/api/sources`;
- `/api/updates`;
- `/api/health`;
- public facets/taxonomies if exposed.

Private compatibility endpoints:

- `/api/saved-searches`;
- `/api/tracker`.

Rules:

- public job endpoints can continue feeding `AtlasPublicLocalSnapshot`;
- plaintext saved-search and tracker endpoints must remain local-only;
- plaintext saved-search and tracker endpoints must not become cloud sync
  endpoints;
- Apple vault state should eventually become the source of truth for saved
  searches and saved jobs;
- any bridge from local plaintext endpoints into the vault must be opt-in,
  additive, and dry-run first;
- no bridge may silently upload plaintext or silently copy plaintext into cloud
  storage;
- saving or deleting private state should eventually write encrypted vault
  records rather than calling plaintext endpoints as the durable source of truth.

During compatibility, the app may keep reading `/api/saved-searches` and
`/api/tracker` for local-only behavior until the vault path is ready. Once vault
state is authoritative, those endpoints should be removed from normal Apple
state hydration or limited to explicit migration tools.

## 8. Test Perspectives for Future Swift Implementation

Future Swift tests should cover:

- public snapshot serialization does not contain saved searches;
- public snapshot serialization does not contain saved jobs;
- public snapshot serialization does not contain job keys from saved jobs;
- public snapshot serialization does not contain notes;
- public snapshot serialization does not contain profile snippets;
- public snapshot serialization does not contain generated document references;
- public snapshot compatibility loading ignores old private fields unless an
  explicit migration flow is active;
- locked vault still allows public search cache use;
- locked vault does not show saved-search names;
- locked vault does not show saved-job keys;
- unlocked vault hydrates saved searches in memory;
- unlocked vault hydrates saved jobs in memory;
- wrong passphrase does not expose partial plaintext;
- corrupt vault does not expose partial plaintext;
- missing vault does not prevent public search cache use;
- import/export does not write plaintext payloads;
- migration from old snapshot is dry-run/additive first;
- old plaintext snapshot cleanup requires explicit user confirmation;
- public detail cache warmup does not persist private saved-job linkage in the
  public snapshot;
- public detail cache files, counts, and progress messages do not reveal
  saved-only job keys or saved-job membership;
- Swift encrypted payload JSON matches Python reference test vectors before
  encryption.

## 9. Risks and Open Questions

- Locked-vault offline UX needs product review: the app can show public cached
  vacancies, but saved-search and saved-job panes need a clear locked/empty
  distinction without revealing private counts or labels unintentionally.
- Saved-search names should appear only after unlock; the UX should avoid
  showing counts or recent timestamps if those are considered private.
- Exact timestamps may not be needed for public cache or vault metadata; future
  implementation should prefer coarse timestamps or logical clocks where
  possible.
- Apple can either parse Python-compatible JSON directly or implement a
  Swift-native version of the same AtlasVault contract. Swift-native code is
  likely required for production Apple UX, but Python vectors should remain the
  compatibility oracle.
- CryptoKit/Swift Crypto compatibility with Python AES-GCM, HKDF, Argon2id, and
  stable JSON AAD needs explicit test vectors before runtime integration.
- Avoid duplicating vault semantics across Apple, Android, and Windows by
  treating `contracts/sync/encrypted_vault.md` as the canonical wire/storage
  contract and `packages/vaultsync` as a reference implementation, not an
  Apple-only design.
- Decide how much `packages/vaultsync` is reference spec versus production
  service code before adding platform-specific behavior.
- Generated documents remain out of vault scope for now, but generated document
  references are private and should be encrypted as `draft_metadata`.
- Detail cache warmup currently benefits from saved-job keys. Moving saved-job
  keys out of the public snapshot requires a new unlocked-session warmup path
  that does not leave saved-only membership evidence in public cache metadata.
- Old plaintext snapshots may remain on disk after compatibility migration until
  a future explicit cleanup flow exists.

## 10. Recommended Next Implementation Loop

Recommended next smallest loop: Phase 2C-review, manually review this Apple
mapping design against the current SwiftUI app and Phase 2 Python vault
contracts.

After review, good implementation candidates are:

- Phase 2D-0: add Swift Codable payload structs and tests only, with no runtime
  storage change;
- Phase 2D-1: split `AtlasLocalSnapshot` into a public-only snapshot in a
  compatibility-safe way;
- Phase 2D-2: add Swift test vectors matching the Python encrypted payload
  format.

Do not start cloud sync yet.
