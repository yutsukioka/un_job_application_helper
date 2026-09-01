# AtlasVault Encrypted Patch Convergence Contract

Status: C19 v1 contract

## Boundary

Convergence is a client-owned reduction over authenticated opaque ciphertext
patches. The backend transports and stores opaque envelopes; it never reads
record contents, passphrases, or vault-key material.

Each replica durably retains:

- the canonical fingerprint of every accepted operation UUID;
- the complete opaque operation while it is needed for reduction;
- authenticated snapshot baselines imported from C18; and
- the UUIDs of locally produced operations awaiting remote acceptance.

The accepted operation set is a mathematical set. Delivery order and duplicate
delivery do not alter the reduction result.

## Conflict Rule

For one `object_id`, concurrent live operations are resolved by the maximum
C17 total-order tuple:

```text
(lamport, author_device_id, author_sequence, operation_id)
```

`lamport` is a logical clock, not wall time. The remaining fields form stable
monotonic/deterministic tie-breakers. Selecting the maximum over a set is
commutative, associative, and idempotent, so applying `{A, B}` in either order
produces the same winner.

Parent revisions remain authenticated causal evidence. A parent may be absent
temporarily because offline or reordered delivery is allowed. Self-parenting,
revision aliasing with different bytes, per-author sequence aliasing, and a
cycle among known revisions fail closed.

## Tombstones

Delete wins for this format version. If any accepted candidate for an object is
a tombstone, every live candidate for that object is hidden. Multiple
tombstones use the same maximum total-order tuple. An authenticated snapshot
tombstone is also a terminal delete fence.

There is no implicit resurrection operation in version 1. A delayed create or
edit, a replayed operation, or an older snapshot cannot revive an object after
a tombstone has been accepted. A future explicit resurrection protocol would
require a new versioned contract.

## Offline And Retry Semantics

Creating an operation offline atomically adds it to the local accepted set and
the encrypted pending-send set. Both survive restart. Reconnect sends pending
operations in C17 total order. Local removal occurs only after remote durable
acceptance.

If a process stops after remote acceptance but before local confirmation, the
operation remains pending. Retry is safe because the remote receipt maps the
operation UUID to its canonical fingerprint:

- identical UUID and fingerprint: duplicate, no new effect;
- identical UUID with different fingerprint: fail closed.

Bidirectional exchange of the same operation set yields the same reduced state.

## Snapshot Baselines

Only successfully authenticated C18 snapshots may be merged. Snapshot records
are baseline candidates below explicit operations of the same delete/live
class. Tombstone precedence applies across both snapshots and operations.
Snapshot operation fingerprints participate in duplicate detection, so
compaction does not erase idempotency history.

## Deferred Boundary

C19 defines and tests the local two-replica reduction mechanics. The formal
cross-platform two-device production journey and phase-gate PR remain C20.
