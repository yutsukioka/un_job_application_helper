# Guarded Sync Admission and Manual Recovery (C23)

This bounded client admission API composes the C21 collection signature and C22
authenticated state view; neither existing signing transcript changes. It is not
the C24 full multi-device proof or a new consensus protocol.

## Complete Candidate Admission

The signed C21 `state_sha256` binds the exact UTF-8 bytes described by
`guarded_sync_recovery_v1.schema.json`. The C22 `collection_root` binds that C21
commitment. Each `patch`, `snapshot`, or `compaction` route submits a complete
candidate collection, not a partial replacement that can silently forget a
tombstone. The input limit is 1 MiB and 256 ciphertext envelopes. The adapter
must route candidates through `GuardedSyncState` before acknowledging them.

The client pins account, vault, collection, epoch, and signing public key. It
checks signatures, exact state digest, actual registry digest, next sequence,
both predecessor links, and previous registry digest. An accepted record cannot
be omitted. A terminal tombstone's complete canonical envelope fingerprint must
remain identical, including its revision and ciphertext-safe fields. There is
no resurrection, tombstone pruning, or epoch advancement policy in this version.

Accepted views, terminal record fingerprints, and cursor move together in one
existing encrypted atomic-file replacement. The cursor is the accepted view's
root; its signed sequence is the corresponding receipt. An identical latest
retry is harmless. An older signed replay alarms without advancing either.
Queue/outbox acknowledgement must follow successful admission, never a rejected
delivery. This adds an admission boundary; it does not replace C17-C19 queue,
snapshot, or conflict algorithms or claim whole-application UI integration.

## Durable Recovery View Model

Each state file has one owning client instance. Concurrent operations on that
instance serialize (or reject while busy on Dart); cross-process shared-file
writers are not supported. The test clients use separate files and processes.

`ACTIVE -> MANUAL_REQUIRED` occurs on authenticated rollback, equivocation,
registry mismatch, invalid chain/context, stale candidate, or resurrection.
The alarm and available signed evidence are persisted before the error returns.
`automaticSync`/`automatic_sync`, ingestion, and peer comparison are fenced.

The visible view model contains only fixed reason codes, sequences, roots,
registry roots, epochs, and disposition. A separate bounded evidence accessor
retains signed local history and the authenticated peer view(s). It never
fabricates a missing peer prefix. Unsigned malformed input is not retained.
Only a presented registry digest is retained, not untrusted descriptor contents.

Manual disposition must echo both displayed tip roots, preventing a stale UI
decision. `retain_accepted` may return to `ACTIVE` only for a signed replay
already present in accepted history. The decision and rejected evidence remain;
no accepted root or receipt is rewritten. `select_peer`, `keep_blocked`, and any
genuine fork yield `RECOVERY_PENDING`. P7 rotation/revocation may be needed for
safe future resolution; C23 does not implement it. At eight retained cases or
256 accepted views, admission fails closed rather than deleting evidence.

## Backend Append Admission

Authenticated GET/POST `/v1/vaults/{vault_id}/commitments` use the closed C22
state-view schema. Existing P4 account/device authorization, throttling, body
bounds, and fixed secret-free errors apply. A registry entry hashes the public
device identifier and canonical signed descriptor payload (excluding signature).
The current signed registry and append are inspected under the backend's same
registry-transition lock. The first append pins the authenticated device's
public signing authority for that vault stream; changing that authority is not
an automatic recovery operation.

SQLite `BEGIN IMMEDIATE` covers read, validation, capacity admission, and insert.
Unique `(account,vault,sequence)` and `(account,vault,root)` constraints ensure
only one conflicting child wins, including independent database connections.
Exact previously accepted payload retries return `appended=false`. Changed
content with the same identifier, gaps/regressions, wrong predecessor or registry,
and any epoch change fail with fixed `Commitment conflict.`. Limits are 256 views
per stream and 8192 total entries. No entry is overwritten or pruned.

Only public commitments/signatures and pinned public authority enter the log.
The P4 service remains single-instance and ephemeral by default; a supplied SQLite
file supports the tested local reopen boundary, not a new production deployment.
The backend cannot inspect tombstones hidden inside ciphertext: clients enforce
candidate preservation. It prevents conflicting appends, not server withholding
or choosing between an already-created fork.

## Explicit Limits and Detection Trigger

A previously synchronized client rejects its own older or incompatible history.
Two separately persisted clients detect a fork when they exchange inconsistent
authenticated views, or receive a view that conflicts with their accepted root.
First-contact freshness needs an external trusted checkpoint. An isolated client
cannot detect globally withheld updates it has never observed. Local-filesystem
rollback is outside this proof. Already-observed forks require visible manual
recovery and remain blocked when this version cannot safely resolve them.

C24 retains the full malicious-server phase gate. P7 retains revocation, rotation,
and any authority/epoch transition policy. No production cryptographic sign-off
or general distributed-freshness guarantee follows from these tests.
