# AtlasVault Authenticated Collection Snapshot Contract

Status: C18 v1 contract

## Boundary

An authenticated collection snapshot is a client-created summary of the
committed encrypted patch state at one collection revision. The backend may
store and return the snapshot as an opaque object, but it never receives a
snapshot authentication key and never parses record plaintext or vault-key
material.

The outer object contains:

- `payload`, the canonical whole-collection summary; and
- `authentication`, an HMAC-SHA256 tag over the canonical payload JSON under a
  client-held 32-byte snapshot authentication key.

Canonical JSON uses UTF-8, recursively lexicographic object keys, no optional
whitespace, JSON string escaping, and decimal integers. The authentication tag
is canonical padded Base64. A missing field, truncated document, altered count,
changed envelope, changed receipt, or wrong key fails closed.

## Payload

The payload contains only opaque identifiers, transport metadata, and opaque
ciphertext envelopes:

- `collection_id` identifies the opaque collection namespace.
- `collection_revision` is the number of committed operations represented by
  the snapshot.
- `last_order` is the final C17 total-order tuple.
- `records` contains exactly one latest ciphertext envelope for every object,
  sorted by `object_id`. Tombstone envelopes remain present.
- `applied_fingerprints` retains every represented operation UUID and its
  canonical SHA-256 fingerprint, preserving idempotency after compaction.
- `author_sequences` retains the final contiguous sequence per author.
- `author_sequence_owners` authenticates the operation UUID that owns every
  represented `(author_device_id, author_sequence)` pair. Each author's map is
  contiguous from sequence 1 through `author_sequences[author]`; its operation
  UUIDs exactly equal the keys in `applied_fingerprints`. This prevents two
  otherwise valid snapshots from aliasing one author sequence to different
  operations after compaction.
- `record_count`, `live_record_count`, and `tombstone_count` authenticate the
  whole-collection cardinality.

The payload must not contain record plaintext, passphrases, raw or wrapped
vault keys, access tokens, decrypted indexes, or server-derived content keys.

## Compaction

The durable encrypted collection journal stores an optional authenticated
snapshot plus a total-ordered tail of subsequent encrypted patches. Replay of
`snapshot + tail` must produce byte-identical current envelopes, operation
receipts, author sequences, revisions, and last order as full patch replay.
Snapshot merge also preserves author-sequence ownership, so an authenticated
snapshot cannot hide a conflicting operation behind only a maximum sequence.

Compaction:

1. validates and replays the prior snapshot and complete tail;
2. creates and verifies a new authenticated snapshot;
3. writes a complete encrypted replacement journal to a staged file;
4. flushes the staged file; and
5. atomically replaces the authoritative journal.

The old journal remains authoritative until replacement. A process killed
before replacement therefore restarts from the valid pre-compaction state; a
process killed after replacement restarts from the valid post-compaction state.
Partial snapshot state is never authoritative.

## Deferred Semantics

This format preserves sequentially committed state only. Concurrent conflict
selection, tombstone convergence, resurrection prevention, and offline merge
semantics remain C19. Two-device convergence proof remains C20.
