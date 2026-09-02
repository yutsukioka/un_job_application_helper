# Registry-Bound State Views v2 (C22)

This client-only T54/T55 layer wraps, rather than changes, C21 commitments.
It adds no consensus, gossip, backend enforcement, key rotation or fork recovery.

## Authentication and Transition

A view contains exactly the fields in `authenticated_state_view.schema.json`.
Account/vault/collection IDs, epoch and authority Ed25519 public key are pinned
locally during authenticated setup, not learned from the server response.
The `collection_root` is a verified C21 commitment root; its sequence must equal
the view sequence and its predecessor must equal the previously observed C21
root. C21's signature, opaque-state digest and rollback rules remain required.

A registry is a list of 1..256 exact `{device_id, descriptor_sha256}` objects.
Both values are lowercase 32-byte hex digests. `descriptor_sha256` fingerprints
the canonical public device descriptor (not its potentially randomized outer
signature). An adapter must hash the actual verified public descriptors shown
to the user; never trust a server-supplied hash instead of the displayed list.
Entries must have unique device IDs. Input order is immaterial. The root hashes
ASCII `atlasvault-registry-root-v1\n` followed by sorted
`device_id:descriptor_sha256\n` lines. The empty-list digest is the bootstrap
predecessor only; an accepted registry is never empty.

The view's authority signature binds BOTH `previous_registry_root` and the new
`registry_root`: it is an explicit complete-registry transition authorization.
An added/removed/substituted entry without that signed successor fails. A valid
signed replacement can authorize membership changes; this is NOT revocation or
rotation enforcement. The authority is an existing pinned client signing key,
not a server key or new PKI. This proof does not rewrite P4's add-only API;
transport/adapters must deliver this authenticated view alongside that API.

Root transcript: ASCII `atlasvault-authenticated-state-view-v2\n`, followed by
one value and newline for each of these fields, in this exact order:

```text
account_id
vault_id
sequence
previous_root
collection_root
registry_root
previous_registry_root
key_epoch
```

Integers are canonical decimal in [1, 9007199254740991]. Identifiers use the C21
ASCII alphabet and bound. `root` is SHA-256 of the transcript. Ed25519 signs
`atlasvault-state-view-signature-v2` + NUL + the 32 root bytes. Version and
format are closed constants and must equal v2's values. Root bytes are identical
across languages; signatures may be randomized by CryptoKit and are verified,
not compared for byte equality. No private or wrapped key is a view field.

## Independent Observations and Evidence Exchange

Each client owns a SEPARATE encrypted atomic observation file. The file pins
context and retains at most 256 signed views plus a sticky conflict flag. The
bound fails closed; no pruning, reinitialization or automatic recovery is added.
There is one owning process per file, as in C21. Missing/corrupt files fail closed.

Observe verifies the view, complete presented registry, C21 signature and state
digest, context, contiguous sequence, view predecessor, registry predecessor,
and C21 predecessor before atomic persistence. Current-root duplicates are
idempotent. Older state is a rollback error. It never chooses between forks.

`exportEvidence` returns only bounded signed views, no ciphertext, keys, local
encryption metadata or credentials. A client explicitly obtains another
device's evidence through an authenticated exchange (transport is NOT implemented
here). `compareEvidence` verifies the supplied authority signatures, full chain
from genesis, context and registry links before comparing overlapping sequences
with its independently persisted history. A different root at any common
sequence is a hard fork. Both clients must perform comparison to observe it.
The comparison result is the length of the consistent common prefix, NOT proof
that either view is globally fresh. Empty/no-overlap evidence is rejected.

Stable public errors contain only codes:
`ATLAS_STATE_VIEW_REJECTED`, `ATLAS_REGISTRY_SUBSTITUTION`,
`ATLAS_ROLLBACK_REJECTED`, `ATLAS_STATE_EQUIVOCATION`,
`ATLAS_CHECKPOINT_REQUIRED`, `ATLAS_HISTORY_LIMIT`.
Authenticated conflicting views latch equivocation durably. Restart and later
honest responses cannot silently clear it. No recovery API exists in C22.
Malformed/unauthenticated evidence is rejected, not misreported as proven fork.

## Limits and Adversarial Proof

One synchronized client detects rollback relative to its durable anchor.
Two independently stored histories expose equivocation only when authenticated
overlapping evidence is exchanged. A server cannot fabricate signed transitions,
but can replay, omit, reorder or selectively serve valid inconsistent histories;
tests explicitly model those attacks, including valid authority-signed forks.
First contact, an entirely unseen withheld suffix, withheld peer evidence, and
local filesystem rollback remain outside the guarantee. No global freshness or
availability claim is made. C23 owns tombstone/recovery/append-only work; C24
owns the end-to-end malicious-server phase gate. Neither is executed here.
