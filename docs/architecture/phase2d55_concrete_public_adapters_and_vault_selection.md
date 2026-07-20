# Phase 2D-55 Concrete Public Adapters and Vault Selection

## Purpose

Phase 2D-55 implements the three concrete dependencies required by the
runtime-neutral Phase 2D-54 host contracts: a narrow public-job API adapter, a
restore-only public-snapshot adapter, and a device-local single-vault selection
registry. The phase preserves the public/private boundary and adds no
production host or application wiring.

## Phase Scope

The implementation is limited to:

- adapting the public subset of `AtlasAPIClient`;
- restoring the reviewed public snapshot file;
- persisting one non-semantic selected vault ID through `AtlasKeychainClient`;
- fixed, redacted adapter errors;
- contract and security tests.

The phase does not implement a host, unlock admission, host reconciliation,
presentation ownership, platform lifecycle observation, views, app entry,
navigation, private rendering, save commands, or later product behavior.

## Reconstructed Phase 2D-54 Baseline

Phase 2D-54 merged the runtime-neutral protocols and safe public values used
here:

- `AtlasPublicJobSearching`;
- `AtlasPublicSnapshotRestoring`;
- `AtlasVaultIDSelecting`;
- `AtlasVaultProductionHostDependencies`;
- the injected production-host factory boundary.

The exact merged Phase 2D-54 head was review-clean before this phase began.
Relevant API, public-snapshot, Keychain, root-provider, and host-contract tests
also passed before implementation.

## Concrete Dependency Objective

Phase 2D-55 makes the first three dependency slots constructible without
making the host constructible. Each adapter has an injected test seam and an
inert initializer. Side effects occur only through an explicit public
operation.

## Narrow Public API Client Seam

`AtlasPublicJobAPIClient` contains only:

- health;
- public search;
- public job detail;
- public sources;
- public updates.

`AtlasAPIClient` conforms in the adapter file. Tests inject a narrow fake
instead. The seam has no arbitrary endpoint method and no private compatibility
operation.

## Broad-Client Containment

The concrete adapter receives a full `AtlasAPIClient` only through its public
initializer, immediately stores it behind the narrow seam, and cannot call
operations outside that seam. Private saved-state and tracker operations are
therefore unrepresentable in adapter code.

## Public Request Mapping

`AtlasPublicJobSearchRequest` maps to an `AtlasSearchRequest` with:

- the exact public query;
- the exact validated limit and offset;
- open-job status only;
- low-confidence inclusion disabled;
- facet loading disabled;
- deterministic closing-date ordering;
- all private and unrelated filters empty.

No query or request field appears in descriptions or errors.

## Public Result Projection

Search rows project only:

- public job ID;
- title;
- safe organization display;
- public duty station;
- stable closing-date text.

Score, score reasons, match evidence, membership, application status, notes,
private classifications, URLs, and persistence details are discarded.
Each response row must retain the exact reviewed `open` status requested by the
adapter. Non-open rows fail before projection or provenance authorization.
Decoder-synthesized placeholders for a missing title, organization, or duty
station are not reviewed public values and also fail before provenance
authorization. This deliberately rejects even a literal value equal to one of
those fallback labels rather than accepting a row whose raw field presence can
no longer be proven across the existing client seam. Duplicate IDs, empty
required values, and inconsistent pagination also fail closed.

## Stable Public Date Representation

Closing dates use one static, immutable `Date.ISO8601FormatStyle` configured
once for UTC internet date and time with fractional seconds disabled. Per-job
projection only applies that reusable value; it does not construct or configure
a formatter for each row. The value-style primitive has no shared mutable
formatter state and is safe to use concurrently under Swift's strict
concurrency model.

Output remains `YYYY-MM-DDTHH:MM:SSZ`, independent of device locale, user
calendar, and time-zone display settings. Subsecond input is represented
without fractional seconds, preserving the reviewed contract. Live API search
and public-snapshot restore share the same projection.

## Public Health Mapping

Reviewed healthy values `ok`, `ok_empty`, `healthy`, and `available` map to
`available`. Reviewed unavailable values `missing_db`, `warning`, `degraded`,
`issue`, `unavailable`, `down`, `error`, and `disabled`, plus missing optional
source status, map to `unavailable`. Unknown values fail as `invalidResponse`.

Only public availability, open-job count, enabled-source count, and parsed
last-sync time survive. Database path, schema version, endpoint details, and
raw server diagnostics are dropped.

## Public Source and Update Mapping

Backend source summaries may populate `organization` from `jobs.org_id`, where
normalization currently stores the source ID. The projection therefore keeps
`sourceID` unchanged as the opaque public identity and derives `displayName`
separately as candidate-facing text.

The display projection trims surrounding whitespace, treats underscores and
hyphens as word boundaries, collapses repeated whitespace, and removes the
same reviewed ATS and infrastructure vocabulary used by candidate-facing job
organization cleanup. Tokens are compared case-insensitively as exact whole
words; no substring deletion is performed. Machine-separated labels use
deterministic, locale-independent casing. A short single token is rendered as
an acronym only when it came from machine-separated text or was entirely
lowercase; already candidate-facing names, including title-case single tokens,
retain their existing word casing. Empty, separator-only, reviewed fallback,
or ATS-only results fail closed as `invalidResponse` rather than exposing the
raw slug.

There is no source-ID-to-name table and no runtime dependency on organization
YAML or another configuration file. Live `/api/sources` responses and restored
snapshot source summaries call the same projection. Source projection still
requires an exact non-empty source ID and non-negative counts. Update
projection remains unchanged: it requires a non-empty identifier, non-negative
source-run counts, and deterministic optional date parsing.

`changedJobCount` is the checked sum of inserted and updated rows. Arithmetic
overflow fails as `invalidResponse`; it never wraps.

## Public-Detail Provenance

An arbitrary `AtlasPublicJobReference` does not authorize detail access. A
reference is usable only after the same adapter has successfully validated and
issued that public job through search.

Before a successful issuance, or after eviction, detail fails as
`invalidRequest` without calling the client. A returned detail must have the
same public job ID. The result reuses the previously issued safe projection so
an upstream detail response cannot replace list identity or presentation.

Public detail follows the existing candidate-detail formatter's section-first
policy. After metadata filtering, non-empty candidate-facing section bodies and
row values are authoritative and retain their existing order. When at least one
such component exists, the top-level description is not projected. The trimmed
top-level description is fallback-only and is used only when no usable
candidate-facing section component remains.

Complete metadata and raw sections are excluded before either their body or
rows are examined. The canonical excluded titles are:

- `Job Record`;
- `Classification`;
- `Locations`;
- `Source Features`;
- `Raw Source Data`.

The backend `Classification` section is a complete classification database row
and can contain internal taxonomy and evidence fields. It is not the narrow,
reviewed classification summary rendered from safe public search fields.
Matching trims surrounding whitespace, normalizes case deterministically, and
compares the entire normalized title. It does not use substring, fuzzy,
body-content, row-value, or sentinel scanning. A candidate-facing title such as
`Raw Source Data Guidance` therefore remains eligible. Metadata-only sections
and candidate-facing sections with only empty bodies and row values do not
suppress a valid top-level description fallback. If neither section content nor
a usable description remains, detail fails as `invalidResponse`; excluded
metadata is never used as a fallback.

This is source precedence, not textual deduplication. The projection performs no
semantic, substring, fuzzy, hash, sentinel, or content-equality comparison.
Repeated content in separate candidate-facing sections remains repeated.
Section titles and row labels are not added. Components remain joined with
`"\n\n"`. Provenance authorization and returned identity validation are
unchanged.

## Bounded Provenance and Eviction

Provenance is actor-isolated, in memory only, and stores only safe public
projections. The default capacity is fixed at 200 references.

Issuance follows deterministic FIFO order. Reissuing an existing ID refreshes
its place in that order. Inserting beyond capacity evicts the oldest issued
reference. Provenance is absent from descriptions and is never persisted or
seeded from saved-only navigation.

## Error Redaction

Public API failures map only to:

- `invalidRequest`;
- `unavailable`;
- `invalidResponse`.

Transport messages, HTTP bodies, URLs, hosts, queries, job IDs, decode
diagnostics, localized descriptions, and underlying errors are never forwarded.

## Snapshot Restorer Boundary

`AtlasApplicationSupportPublicSnapshotRestorer` implements only
`AtlasPublicSnapshotRestoring`. It receives a root provider and a narrow
read-only file seam. The public initializer uses the Foundation reader; tests
inject a recorder.

Construction resolves no root and reads no file.

Snapshot job projection reuses the same status, identity, duplicate, and safe
field validation as live search. It does not reuse the live-search page limit:
the existing cache writer intentionally requests a large open-job snapshot, so
the restorer requires a positive snapshot limit, validates non-negative total
and offset values plus result-count consistency, and then projects all reviewed
public rows. A cache `limit` above the live 200-row request maximum therefore
does not invalidate an otherwise valid snapshot.

## Application Support Path

Explicit restore resolves the reviewed Application Support root and appends
exactly:

1. `Atlas`;
2. `atlas-local-snapshot.json`.

The standardized candidate must be a strict descendant of the standardized
root.

## Missing Snapshot

A missing snapshot returns `nil`. The Foundation reader recognizes both the
general Cocoa no-such-file code and the file-read no-such-file code emitted by
`attributesOfItem(atPath:)`. Restore creates no directory or file and does not
attempt a read.

## Private and Legacy Key Rejection

The restorer parses the top-level JSON object before model decoding. Any
private or legacy key, including saved searches, saved jobs, tracker state,
notes, snippets, drafts, or generated-document references, causes
`invalidSnapshot`.

Rejection is schema-based. It does not scan arbitrary user values for
sentinels.

## Conservative Unknown-Key Policy

Version one accepts exactly:

- `savedAt`;
- `baseURL`;
- `health`;
- `searchResponse`;
- `sources`;
- `recentRuns`.

Every unknown top-level key fails closed. This intentionally trades
forward-compatible silent acceptance for an explicit review gate whenever the
public snapshot schema changes.

## Base URL and Database-Path Removal

The legacy public snapshot still contains a base URL and health diagnostics.
The production projection discards the base URL, database path, schema
version, facets, unclassified count, and any data not represented by
`AtlasProductionPublicSnapshot`.

## Detail-Cache Exclusion

The restorer never inspects `JobDetails`, staging, previous copies, detail
counts, warmup state, or per-job detail files. Public detail cache use while
locked remains blocked pending a separate provenance and namespace design.

## Path and Symlink Policy

The root and candidate must be safe absolute local file URLs. Root `/` is
rejected. The existing target must be a regular file.

Both root and candidate are symlink-resolved. The resolved candidate must
remain a strict descendant of the resolved root. Path or symlink escape fails
as `invalidSnapshot`; access failures map to `unavailable`. Errors contain no
path.

## Restore-Only Guarantee

The file seam exposes only:

- file status;
- symlink resolution;
- data read.

There is no save, replace, delete, directory creation, detail restore, detail
copy, or warmup operation.

## Single-Selected-Vault Policy

`AtlasKeychainVaultSelectionRegistry` represents exactly:

- no selected vault;
- one selected non-semantic vault ID.

It provides no list, label, count, timestamp, switcher, creation, deletion,
directory discovery, or multiple-vault policy.

## Keychain Registry Rationale

Selection must survive application restarts without entering UserDefaults,
paths, logs, or presentation state. The existing `AtlasKeychainClient` seam
provides a tested device-local storage boundary while keeping direct Security
framework calls out of the registry.

The registry service is distinct from the vault-key service, so selection
operations never query or overwrite vault-key items.

## Fixed Service and Account Metadata

The registry uses one fixed service and one fixed account. Neither contains:

- vault ID;
- label;
- path;
- count;
- timestamp;
- record metadata.

The selected ID exists only inside Keychain value data.

## Versioned Registry Envelope

The private persisted envelope contains exactly:

```json
{
  "format": "atlas-vault-selection",
  "version": 1,
  "vault_id": "<validated-id>"
}
```

The envelope contains no vault key, passphrase, recovery key, path, label,
timestamp, or count. Public selection models remain non-Codable.

## Load, Store, and Clear Behavior

Load:

- item not found returns `.none`;
- valid format/version/ID returns `.selected`;
- malformed or unsupported data returns `invalidRegistry`;
- Keychain access failure returns `unavailable`.

Store:

- adds one device-only generic-password item;
- duplicate item updates only value data;
- envelope encoding failure returns `unavailable` before any Keychain call;
- any Keychain failure returns `unavailable`.

Clear:

- deletes the fixed item;
- item not found is success;
- any other Keychain failure returns `unavailable`.

Raw `OSStatus`, metadata, selected ID, and underlying errors are never exposed.

## No Automatic Selection

Construction performs no Keychain call. Public search, app start, unlock
capability display, and panel display do not call load, store, or clear.
Selection remains an explicit later host operation.

## Construction Side Effects

All three adapters perform assignments only during construction:

- no API call;
- no task creation;
- no root resolution;
- no file status or read;
- no directory creation;
- no Keychain operation;
- no logging.

## TDD Evidence

The valid red checkpoint compiled the test target until the three concrete
Phase 2D-55 type names were unresolved. The red test commit was pushed before
production source existed.

Tests were then expanded to cover public projection, provenance, fail-closed
snapshot behavior, Keychain metadata isolation, fixed errors, side effects,
source guards, artifacts, and exact scope.

Review-fix cycle 8 first added a structural regression that failed while
`stableDateText(_:)` constructed an `ISO8601DateFormatter` per call. Exact UTC
output, concurrent use, and a 10,000-row fake snapshot batch were also covered
before the immutable reusable format style was introduced. Optimized
before/after benchmark evidence is recorded outside the repository; wall-clock
timing is deliberately not a CI correctness threshold.

Review-fix cycle 9 added live and restored-source regressions before production
changes. They failed while source/org slugs remained the candidate display,
ATS tokens remained visible, and ATS-only or separator-only labels were
accepted. The implementation then introduced one generic separator-aware,
exact-token projection shared by both live and snapshot source summaries. An
exact-head review regression then narrowed acronym uppercasing so clean
title-case single-token labels remain unchanged.

## Test Coverage

The focused suite covers:

- construction call counts;
- health/search/source/update/detail mapping;
- candidate-facing source-label normalization with unchanged opaque source IDs;
- exact ATS-token removal without substring deletion;
- deterministic machine-separator casing and preservation of already clean
  organization names;
- preservation of clean title-case short single-token labels;
- empty, separator-only, fallback, and ATS-only source-label rejection;
- identical live and restored-snapshot source projection;
- non-open row, decoder-fallback, duplicate, and pagination validation;
- exact deterministic UTC dates for epoch, nonzero-time, day-boundary, and
  subsecond inputs;
- identical live-search and snapshot date projection;
- immutable reusable date-format structure and concurrent deterministic use;
- 10,000-row fake snapshot date projection without a timing threshold;
- bounded FIFO provenance;
- detail identity and issuance;
- normalized exact-title exclusion of complete metadata/raw sections, including
  their bodies and rows, while retaining ordinary candidate-facing prose;
- section-first precedence for exact duplicate and structured-description
  payloads;
- preservation of candidate section body and row-value order without section
  titles or row labels;
- description fallback after metadata-only or empty candidate sections;
- no-content fail-closed behavior and preservation of intentionally repeated
  candidate section content without broad deduplication;
- error sentinel redaction;
- missing, valid, malformed, private-key, unknown-key, path, symlink, and file
  status snapshot cases;
- cache snapshot limits above the live-search page maximum;
- registry load/store/update/clear and malformed envelope cases;
- store-time envelope encoding failure before Keychain access;
- fixed Keychain metadata and device-only accessibility;
- source guards, allowlist, and artifact scans.

The full Swift suite remains the regression gate.

## Go/No-Go Update

| Capability | Status |
| --- | --- |
| Public-search contract | Implemented |
| Concrete public-search adapter | Implemented |
| Candidate-facing public source summaries | Implemented with generic exact-token normalization shared by live and snapshot projection |
| Public-snapshot restore contract | Implemented |
| Concrete public-snapshot restorer | Implemented |
| Public-detail provenance and candidate-facing projection | Implemented with bounded in-memory constraints, normalized metadata exclusion, and section-first description fallback |
| Detail cache while locked | Blocked |
| Vault-selection contract | Implemented |
| Single-vault Keychain registry | Implemented |
| Multiple-vault registry or UI | Not implemented |
| Production host | Not implemented |
| App-entry wiring | Blocked |
| Local-key unlock capability | Available behind existing runtime seams |
| Passphrase or recovery unlock | Blocked without reviewed providers |

## Deferred Work

Deferred work includes:

- runtime-neutral production host actor;
- host-owned sanitized presentation updates;
- process-global unlock admission;
- authoritative host reconciliation;
- presentation-owner reset acknowledgement;
- concrete app-host composition;
- actual app-entry and navigation wiring;
- detail-cache provenance;
- multiple-vault management;
- production passphrase and recovery providers;
- private rendering and write-side product behavior;
- migration, plaintext cleanup, cloud sync, recovery, onboarding, and key
  rotation.

## Next Product Gate

Phase 2D-56 must implement the runtime-neutral production host actor with:

- explicit start and stop;
- a host-owned sanitized presentation-update source;
- process-global unlock admission;
- lazy vault selection;
- lazy unlock-controller construction;
- authoritative reconciliation;
- injected and testable presentation-owner reset acknowledgement;
- private-free teardown;
- reuse of Phase 2D-54 contracts and Phase 2D-55 adapters;
- no app-entry wiring.

Phase 2D-55 creates no Phase 2D-56 branch or files.
