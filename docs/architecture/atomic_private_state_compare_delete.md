# Atomic Private-State Compare-and-Delete

## Purpose

Phase 2E-6 must delete compatibility saved searches and tracker records only when the server still holds the exact content that the migration reviewed and encrypted. Identifier-only deletion cannot provide that guarantee because another client can update a record between a read and a later delete.

This backend package adds atomic expected-content deletion as an independent prerequisite. It does not modify Flutter or replace the existing compatibility routes.

## Scope

The package adds:

- one standard-library JSON transaction boundary shared by both stores;
- conditional saved-search deletion;
- conditional tracker-record deletion;
- two POST command routes carrying strict expected snapshots;
- fixed, private-free mismatch and identity errors.

Existing identifier-only DELETE routes remain available for legacy callers. Phase 2E-6 must use only the conditional routes during migration.

## Atomic JSON Store

Each JSON target has one adjacent `<target>.mutation.lock` file. The lock contains no private content. POSIX hosts use an exclusive `fcntl.flock`; Windows repeatedly attempts a nonblocking `msvcrt.locking` operation over one fixed byte until contention clears. This explicit retry gives Windows the same wait-for-serialization behavior as POSIX even when a transaction exceeds the platform helper's bounded blocking interval. Acquisition and release occur in a `finally`-safe context.

Every read obtains the same lock and therefore observes one coherent committed snapshot. Every mutation performs one non-recursive transaction:

1. acquire the target lock;
2. load and validate bounded UTF-8 JSON;
3. apply one in-memory mutation;
4. encode using the store's existing JSON format;
5. create a same-directory temporary file with create-new semantics;
6. write completely, flush, and call `fsync`;
7. replace the target atomically with `os.replace`;
8. verify exact read-back bytes;
9. best-effort `fsync` the parent directory on POSIX;
10. release the lock.

Temporary files are removed after failure. Malformed, oversized, or wrong-shape JSON is rejected without rewriting the current target. Filesystem durability ultimately depends on the host filesystem and storage device.

## Saved Searches

`save_search`, `remove_saved_search`, reads, and `compare_and_remove_saved_search` use the same target lock. The existing versioned object format, field names, timestamps, sorted indented encoding, trailing newline, CLI collision behavior, and identifier-only removal remain intact.

Conditional comparison includes:

- name;
- description;
- complete request;
- created timestamp;
- updated timestamp.

Every persisted entry and its complete request are validated against the exact stored schema. Unknown fields, unsupported missing fields, wrong shapes, and map-key/name disagreement fail closed with a fixed error. Historical version-1 files may legitimately omit only `volunteer_kinds` and `exclude_expired_open`, which were introduced without a store-version increment. Reads and mutations upgrade those omissions to the current interpreted defaults inside the same mutation lock, atomically persist the upgraded representation, and expose only that complete representation for review. A failed upgrade replacement preserves the original file. Comparison then uses canonical JSON bytes for the complete raw stored and expected payloads, so reconstruction cannot drop or default content before comparison. The operation returns `deleted`, `absent`, or `mismatch`.

## Tracker Records

`upsert_record`, `create_record`, `delete_record`, reads, and `compare_and_delete_record` use the same target lock. The existing top-level array, application-record fields, timestamp update behavior, sorted indented encoding, and identifier-only deletion remain intact.

Conditional comparison includes:

- record ID;
- job key;
- status;
- notes;
- applied timestamp;
- updated timestamp.

Stored tracker objects must contain exactly the reviewed application-record fields and unique, nonempty IDs. Each raw object must round-trip through the model without normalization. Conditional deletion compares the complete raw object and removes exactly one validated record. Unknown fields, missing fields, coercible aliases, and duplicate IDs fail closed without rewriting. The operation returns `deleted`, `absent`, or `mismatch`. A mismatch preserves the current record.

## HTTP Contract

The API adds:

```text
POST /api/saved-searches/{name}/conditional-delete
POST /api/tracker/{record_id}/conditional-delete
```

POST command routes are used because request bodies on DELETE are not handled consistently by all clients and intermediaries. Each request contains exactly one `expected` snapshot. Every stored snapshot field is required, even when its value equals a normal model default. The saved-search request model contains only the exact persisted request fields, and tracker snapshots redeclare every persisted field without defaults. Missing, unknown, and API-only fields are rejected.

Both commands use a dedicated full decoded path capture that matches every code point, including `/` and encoded control characters. Historical saved-search names remain addressable, including the empty name accepted by the existing store, while tracker snapshots retain their strict nonempty-ID requirement. The converter URL-encodes identifiers for reverse routing. Neither command normalizes or reinterprets its identifier: the captured path value must still equal the exact expected snapshot identifier.

The path identifier must equal the expected snapshot identifier. An identity mismatch returns fixed HTTP 400. Exact current content is deleted and returns `{"outcome":"deleted"}`. A missing current record is idempotent and returns `{"outcome":"absent"}`. Changed current content returns fixed HTTP 412 and remains untouched.

Responses and errors never return current private content. Missing bodies, malformed JSON, and strict-model validation failures on the two command routes all use the same fixed 422 response. Validation failures on unrelated routes retain FastAPI's existing behavior. Store failures use a fixed private-state operation error rather than exposing a path or malformed value.

## Concurrency Evidence

Deterministic thread gates intercept lock acquisition and the locked read boundary. They prove that:

- a saved-search update committed before comparison produces `mismatch`;
- a completed conditional delete cannot corrupt a later save;
- tracker upsert and conditional delete serialize through the same lock;
- resulting JSON remains valid;
- a pre-replace failure preserves the previous target, removes temporary files, and releases the lock.
- unsupported saved-search fields and duplicate or extended tracker records cannot be deleted;
- historical version-1 saved searches are atomically upgraded before review;
- Windows lock contention retries until acquisition;
- parser failures use the fixed private-free command error.

The tests use synchronization events rather than sleep-based race assumptions. The source test also requires both POSIX and Windows lock implementations. The Windows branch is exercised when the Python suite runs on Windows.

## Privacy And Failure Policy

The mutation lock stores no private data. Temporary files contain only the same JSON already intended for the target and remain adjacent to it. Fixed errors do not contain saved-search names, requests, job keys, notes, record IDs, target paths, or current server content.

## Phase Boundary

This package is a backend prerequisite for Phase 2E-6. It adds no Flutter code, Windows native code, authentication change, encrypted-sync format, UI, platform storage, migration behavior, or cloud synchronization.

## TDD Evidence

The red checkpoint was committed before production changes. It collected 27 tests with 24 expected failures and 3 existing passes. A red-only correction then added deterministic tracker ordering coverage. The failures identified the absent atomic helper, absent conditional store functions, and absent command routes.

The implementation checkpoint requires the focused contract suite, complete `packages/jobagg/tests`, complete repository `tests`, the repository Python CI mirror, exact eight-file scope, and protected-path and artifact hygiene. A review-fix red checkpoint added ten deterministic failures for complete raw comparison, deep validation, duplicate-ID rejection, Windows contention retry, and parser-error normalization before those corrections were implemented.

## Go/No-Go

- server-side saved-search compare-and-delete: implemented;
- server-side tracker compare-and-delete: implemented;
- shared lock for corresponding reads and mutations: implemented;
- same-directory atomic JSON replacement: implemented;
- mismatch preserves changed content: implemented;
- idempotent absent response: implemented;
- existing identifier-only routes: preserved;
- private content in errors: absent;
- Flutter changes: absent;
- Phase 2E-6 migration integration: deferred to PR #94 after this package merges.
