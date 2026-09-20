# Epoch Activation v1 (C26 / D087)

This implements one accepted N-to-N+1 activation. It is not missed-epoch
catch-up, revocation convergence, secure deletion, or production sign-off.
The signed C25 removal and C26 rotation proof keep their existing encodings.

## Authority and Admission

`POST /v1/vaults/{vault_id}/activations` accepts an authenticated
`atlasvault-epoch-rotation` proof. `GET` returns the accepted
`atlasvault-activation-record` only to an account-authenticated device in its
ACTIVE recipient set. The closed record schema is
[atlasvault_activation_v1.schema.json](atlasvault_activation_v1.schema.json).
Control-plane session tokens remain in the existing D067 authorization boundary;
they are never part of the activation record.

The backend commits the complete proof, including all HPKE recipient ciphertexts,
in one SQLite transaction with `synchronous=FULL`. Account, vault, predecessor
epoch, current accepted history root, signed prior registry and resulting
registry, authorized signer, generation, recipients, and version are verified
before insertion. Exact content retry is idempotent. Conflicting content fails
without changing history, cursors, receipts, or activation. A per-instance lock
serializes epoch admission with existing storage writes. SQLite additionally
serializes activation inserts; this does not establish multi-replica admission
for the process-local P4 storage/rate counters (R024).

The single durable activation record is the global commit point, not a promise
that independent client disks update simultaneously. Configure the backend with
a durable `commitments_path`; the default in-memory instance remains a test-only
ephemeral store and rejects activation with `503 ATLAS_ACTIVATION_STORAGE_UNAVAILABLE`.
A reloaded service must restore its normal account/device
authentication separately; it must not discard the durable activation database.

## Client Publication

| Journal phase | Externally usable state |
| --- | --- |
| PREPARED | Complete N; prepared deliveries inactive |
| BACKEND_SUBMITTED | ACTIVATION_PENDING; fence before sending the request |
| BACKEND_ACCEPTED | ACTIVATION_PENDING; accepted record retained |
| LOCAL_PUBLISHING | ACTIVATION_PENDING; incomplete staging is not active |
| ACTIVE | Complete N+1; current registry, ring, history and writers agree |
| RECOVERY_PENDING | No automatic selection, activation or future writing |

The caller persists `beginActivation`/`begin_activation` before the authenticated
POST. A lost or uncertain response leaves the client pending. It retrieves and
verifies the accepted record before same-activation completion. A pending client
can retrieve the exact staged ciphertext-safe request through `pendingActivation`
for an idempotent retry after restart. It never regenerates replacement key
material for an uncertain accepted epoch. A pending client
without authorized delivery stays unavailable; it cannot fabricate a key or
resume N writes. An isolated client that has not observed acceptance may create
local N work, but the activated backend refuses publication under N.

`EpochVault` / `AtlasVaultEpochVault` is a single-owner client boundary. Its one
bounded encrypted atomic file contains the actual P5 outbox/inbox state, actual
P6 accepted-history/recovery state, retained key ring, registry, active epoch,
recipient commitment and journal. Component adapters never publish independent
component files. The existing durable write path flushes staging and replaces
the active file last. Interrupted staging cannot become an active mixed
generation. Import existing histories/queues once, then relinquish their old
writer instances; application wiring must use this owner for future writes.

The signed rotation proof bridges the C25 key/state registry root to the pinned
P6 history tip. All old signed views remain intact. Following views preserve
sequence and predecessor linkage, use N+1 and the authenticated resulting
registry root. The existing stream signer is replaced only by the authenticated
rotation signer. Previously accepted ciphertext fingerprints may remain at N;
new records cannot introduce older-epoch ciphertext. Existing terminal tombstone
and recovery rules continue to apply. No history is reset to genesis.

Future patch/snapshot encryption selects only the active epoch with internally
generated nonces. The journal-owned outbox rejects newly queued old-epoch
entries. New commitments bind N+1, and delivery lookup is limited to the exact
sorted ACTIVE recipient set. Retained N keys are read-only compatibility material.

## Exclusion and Limits

The target is absent from every N+1 recipient ciphertext. A revoked reconnect
cannot retrieve delivery, publish old-epoch state, downgrade activation or forge
recipient membership. Even possession of a copied public activation record does
not provide its missing HPKE key. Rejections advance no authoritative state.

revocation cannot erase epoch-N keys, ciphertext, or plaintext already held by the
revoked device. It protects future epoch-N+1-and-later data.

Contradictory P6 evidence remains fenced; no automatic fork choice is introduced.
First-contact freshness, globally unseen withholding, withheld peer evidence and
local-filesystem rollback remain outside the demonstrated boundary. External
cryptographic review remains release-blocking. R026 remains OPEN_CONTROLLED.
Authorized catch-up across missed epochs, post-rotation recovery beyond completing
this accepted activation, secure deletion and final registry convergence are C27.
The full P7 platform gate and PR are C28, not this chunk.
