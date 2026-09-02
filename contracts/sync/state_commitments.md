# Signed State Commitments v1 (C21)

Scope: T51-T53 only. This is a client validation primitive, not a server
consensus, fork/gossip, registry, recovery, or append-only service protocol.

## Wire Contract

`state_commitment.schema.json` defines the closed wire object. A collection ID
is an opaque ASCII identifier, not a name derived from vault contents. Sequence
is an integer in [1, 9007199254740991], with no wraparound. Root digests are
lowercase SHA-256 hex. The genesis predecessor is 64 zeroes, used only at seq 1.

The opaque state is the exact encrypted snapshot/collection-state byte string
being committed, including encrypted records, revisions and tombstones. Clients
hash those bytes, not a plaintext representation or a re-encoded JSON object.
The state must be 16..134217728 bytes. No plaintext, key, or private signing
material is a commitment field. This primitive does not prove that arbitrary
caller-provided bytes were encrypted: the caller must use the encrypted state
producer and must verify the underlying snapshot before applying it.

The root is SHA-256 of this ASCII transcript (including its final newline):

```text
atlasvault-state-commitment-v1\n
<collection_id>\n
<sequence in canonical decimal>\n
<previous_root>\n
<state_sha256>\n
```

There are no blank lines in the transcript. Ed25519 signs the bytes
`atlasvault-state-root-signature-v1` + NUL + the 32 decoded root bytes.
Signature encoding is canonical padded base64 of exactly 64 bytes. The signer
public key is pinned by the local client from an authenticated trust setup;
it is NEVER accepted from the served commitment. Trust rotation is not C21.

## Durable Client Rule

An explicitly initialized encrypted checkpoint pins collection, signer, seq 0
and the zero root. Opening an existing client never implicitly initializes a
missing checkpoint. Callers serialize ownership of each checkpoint path across
processes; the client rejects overlapping operations on its own instance.

On every accept, read and authenticate the checkpoint, validate the closed
commitment, verify the signature and exact opaque-state digest, then require:

* A duplicate at the known sequence has the identical root (idempotent).
* A new commitment has seq = known seq + 1 and previous_root = known root.
* Lower sequences, gaps, changed roots at the known sequence, wrong scope,
  wrong signer, malformed signatures and digest mismatches fail closed.

The new checkpoint is encrypted and atomically replaced using the existing
durable queue-file writer before accept returns true. No decrypted records or
keys are persisted. A failed verification does not mutate the checkpoint. A
failed write returns no success; restart reads a complete old or new anchor.
Checkpoint corruption, loss, or key mismatch is an error, not genesis fallback.

The checkpoint is an observation high-water mark, not the collection database.
Callers must accept before exposing served state and retain/re-fetch the
accepted state after an application interruption. Same-root duplicates permit
that retry. Integrating this primitive into the end-to-end sync protocol is a
later phase-gate proof, not claimed complete here.

## Adversary and Limits

Shared vectors and all three client tests actively serve: an old valid signed
state after seq N; seq N+2 while withholding N+1; an old snapshot below N;
truncated/omitted state bytes with an otherwise valid commitment; and a validly
signed non-chaining successor. Each is rejected and leaves the anchor intact.

A server withholding an entirely unseen suffix cannot be detected without an
independent freshness source. A client with no prior trusted observation cannot
detect a consistent historical prefix. Local checkpoint rollback by an attacker
who controls the client filesystem is outside this malicious-server model.
No global fork/equivocation or availability guarantee is claimed (C22/C24).

Vectors: `test_vectors/atlasvault_state_commitment_vectors_v1.json` contains
synthetic encrypted bytes, public keys and signatures only. Python, Dart and
Swift reproduce the same roots/signatures and enforce the same sequence rules.
