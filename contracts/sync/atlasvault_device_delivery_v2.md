# AtlasVault Per-Device Delivery Proof v2

Status: C27 implementation of D089. This is not P7 or production sign-off.

The D087 activation record remains the epoch commit point. Aggregate delivery
proof v1 remains immutable and verifiable with its complete wrapper collection.
Removing any wrapper invalidates that signature. A legacy record without a v2
attestation is unavailable for selective catch-up or recovery:
`ATLAS_PER_DEVICE_PROOF_REQUIRED`. The backend must not redact and serve v1.

An authorized ACTIVE client signs a v2 attestation over the original activation
identifier, original plan and signed registry transition, one recipient identity
and agreement-public-key fingerprint, and the hash of that recipient's exact
existing HPKE wrapper. The proof also binds the original rotation signer,
separately from the upgrading proof issuer. P6 history authority does not change
when a different ACTIVE client supplies the attestation.

The canonical encoding is sorted-key, compact ASCII JSON. The root is SHA-256
of `atlasvault-device-delivery-proof-v2\n` followed by canonical proof fields
excluding `root` and `signature_b64`. Ed25519 signs
`atlasvault-device-delivery-signature-v2\0` followed by the 32 root bytes.
The closed JSON schema and synthetic shared vector specify every field.

The signed plan binds account, vault, adjacent epochs, prior/post registry roots,
state root, initiating device and sorted unique active recipient set. The set
commitment retains the C26 domain and encoding. `registry_generation` equals the
new epoch; the signed revocation sequence retains its C25 per-generation meaning.
HPKE remains suite `0x0020/0x0001/0x0002`, epoch wrapper version 2. Neither
encapsulation nor ciphertext is regenerated during proof creation.

Publication verifies the issuer and recipient are currently ACTIVE and compares
the submitted metadata and wrapper with the immutable accepted activation.
Historical proof signatures validate against the authenticated post-transition
registry. Retrieval additionally requires CURRENT ACTIVE requester membership.
Each authenticated response contains only that requester's packet. Exact retry
does not alter the activation, cursor or registry; conflicting proof content is
rejected. No backend signing capability or unwrapped key is involved.

Activation POST returns a version-2 receipt (identifier, accepted status and epoch),
never the aggregate wrapper collection. The immutable backend activation is still
version 1. Receipt equality is not a substitute for verifying the signed proof.

Canonical unsigned fields and roots agree byte-exactly across clients. CryptoKit
randomizes newly generated Ed25519 signatures; it does not promise deterministic
signature bytes. All clients verify the same stored vector signature and runtime
Swift signatures. Cache and retry the exact signed packet, not a freshly signed
replacement. See [Apple's signature API](https://developer.apple.com/documentation/cryptokit/curve25519/signing/privatekey/signature%28for%3A%29).

## Catch-Up, Recovery, and Cleanup

An ACTIVE requester obtains its own packet for each adjacent missed epoch. The
caller supplies the latest authenticated activation identifier; it must not infer
freshness from an isolated server view. Missing/reordered proofs, other recipients,
or context/signature mismatches leave `CATCH_UP_PENDING`, with stale writes disabled.
Intervening signed state updates use the existing P6 validator, including history
and tombstone checks. Staged updates never replace accepted state individually.

The encrypted owner publishes registry, key ring, history, queues and epoch as one
generation. A complete encrypted write-ahead recovery record precedes replacement
of the owner file. A mismatch is unavailable, not a fallback to an older owner.
Repair restores a complete generation in `CATCH_UP_PENDING`; current device-bound
proof revalidation is required before writes resume. A protected local recovery
record and its storage key must remain available. If both accepted history and
its protected recovery record are lost, this API cannot invent a trusted baseline.
Contradictory P6 evidence remains durable and `RECOVERY_PENDING`/`MANUAL_REQUIRED`.

Cleanup records the explicit retained-epoch policy before deletion. A different
policy cannot replace an interrupted cleanup. The current epoch and epochs needed
by queued writes must remain. Caller policy must retain any epochs still required
for historical ciphertext. Platform adapters invoke Keychain, Android-backed and
Windows-backed deletion APIs and check application retrieval returns no entry.
Failure stays `CLEANUP_PENDING`; exact retry resumes. Mutable temporary buffers
are cleared where supported; managed immutable copies cannot be guaranteed erased.

There is no claim of physical SSD/flash erasure, deletion from OS backups or
filesystem snapshots, forensic erasure, or remote deletion from an offline device.
Revocation cannot erase epoch-N keys, ciphertext, or plaintext already held by the
revoked device. It protects future epoch-N+1-and-later data.
This does not add multi-replica coordination (R024), full recovery UX, C28 platform
sign-off, or resolution of the controlled Swift cancellation residual (R026).

This proof does not establish first-contact freshness, reveal globally withheld
updates, resolve a P6 fork, or prevent local-filesystem rollback. An isolated
client cannot infer a newer activation the server has never disclosed. Those
limitations remain explicit; contradictory authenticated histories stay fenced.
