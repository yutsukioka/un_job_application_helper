# AtlasVault Encrypted Patch and Transfer Queue Contract

Status: C17 v1 contract

## Boundary

An encrypted patch is a client-owned operation descriptor around the P4
`atlasvault-opaque-ciphertext-envelope`. The backend stores and returns the
envelope as opaque bytes. It does not parse patch plaintext, passphrases, or
vault keys.

The operation descriptor contains only opaque identifiers, ordering metadata,
and encrypted envelope fields:

- `operation_id` is a canonical lowercase UUID and is also the HTTP
  `Idempotency-Key` for every retry of that operation.
- `operation_type` is `upsert` when `envelope.tombstone` is false and `delete`
  when it is true.
- `author_device_id` identifies the signing device without exposing vault
  content.
- `author_sequence` is positive and strictly contiguous for one author.
- `lamport` is positive and advances the operation's logical clock.
- `envelope.parent_revision` supplies the causal parent link.
- `envelope.revision` is the resulting opaque revision.

No operation or queue state may contain record plaintext, passphrases, raw or
wrapped vault keys, or access tokens.

## Ordering

The parent-revision chain and each author's contiguous sequence define the
partial causal order. A deterministic transport order is the ascending tuple:

```text
(lamport, author_device_id, author_sequence, operation_id)
```

This total order is for queueing and deterministic replay only. It does not
define conflict or tombstone convergence; those semantics are reserved for
C19.

## Outbox

The durable outbox:

1. encrypts its complete state with AES-256-GCM under a caller-supplied local
   queue key held by the platform secure-storage boundary;
2. uses a fresh internally generated 96-bit nonce for every state commit;
3. commits by atomic file replacement before reporting success;
4. returns operations in deterministic transport order;
5. retains an operation across send failures and process restarts; and
6. removes it only after the caller confirms remote acceptance for the same
   `operation_id`.

Delivery is therefore at least once. Remote idempotency makes retries
exactly-once in effect.

## Inbox And Cursor

The durable inbox stages one remote page at a time. Staging validates all new
operations, persists them encrypted, and leaves the current remote cursor
unchanged. `applyNext` invokes the caller's durable apply boundary, then
atomically records the operation as applied. The cursor advances to the page's
`next_cursor` only after every operation in that page is durably recorded as
applied. A terminal page may carry a null `next_cursor`; the encrypted inbox
therefore stores an explicit staged-page marker instead of inferring pending
state from cursor nullability.

An already-applied `operation_id` with identical canonical bytes is a harmless
duplicate. Reuse of an ID with different bytes fails closed. New operations
must preserve author sequence, total order, and object parent revision.

The inbox's applied-operation receipts are transport receipts, not C19 record
convergence state.

## On-Disk Envelope

Outbox and inbox files contain only:

```json
{
  "format": "atlasvault-encrypted-transfer-queue",
  "version": 1,
  "nonce_b64": "<canonical Base64, 12 bytes>",
  "ciphertext_b64": "<AES-256-GCM ciphertext and 16-byte tag>"
}
```

Queue kind and version are authenticated as AES-GCM associated data. The
unencrypted remote cursor, operation metadata, and patch envelope never appear
in the file.

## Crash Semantics

After any successful enqueue, page stage, acknowledgement, or apply commit, a
process may be killed without losing the committed state. A restart decrypts
and validates the last complete atomic replacement. Temporary or tampered
state is never accepted as a queue.
