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

This proof does not establish first-contact freshness, reveal globally withheld
updates, resolve a P6 fork, or prevent local-filesystem rollback. An isolated
client cannot infer a newer activation the server has never disclosed. Those
limitations remain explicit; contradictory authenticated histories stay fenced.
