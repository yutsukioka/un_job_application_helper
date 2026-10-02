# Recipient-Bound Enrollment Delivery v1

Scope: C30/T74, D100 and D102-D106. This transport carries only the exact
D102-authenticated current ciphertext projection, its public enrollment/anchor
evidence, and fresh recipient-specific HPKE deliveries. It neither rewrites D087
activation records nor reuses/rebinds D089 delivery proofs.

## Authentication

The ACTIVE initiating client signs the canonical packet, including every HPKE
ciphertext and the complete bounded historical-authority supplement. The packet
root is SHA-256 of `atlasvault-enrollment-delivery-v1\n` followed by canonical
JSON excluding `root` and `signature_b64`. Ed25519 signs
`atlasvault-enrollment-delivery-signature-v1\0` followed by the binary root.
The signature authenticates delivery bytes, not new historical authority facts.
Historical authorization still requires the existing signed evidence verified
under [the D106 contract](historical_authority_v1.md).

Each HPKE invocation uses the existing P3 epoch-key-delivery API and suite
`0x0020/0x0001/0x0002`. Its application context is
`atlasvault-enrollment-delivery-hpke-v1\0` followed by SHA-256 of canonical packet
fields excluding `deliveries`, `root`, and `signature_b64`. The existing epoch
context prefix remains unchanged. No caller-selected production nonce is added.

The sorted delivery epochs must equal the union of the active epoch and epochs
referenced by the exact current projection. No additional historical key is
permitted. Each entry contains one epoch number, a 32-byte encapsulation and a
48-byte ciphertext for the one enrolled recipient. No other recipient's artifact
is copied. The backend is not a signer and receives no unwrapped key.

Recipient expectations (anchor root, current context, recipient, transcript and
trusted signing public key) are inputs from the authenticated ceremony, not an
unauthenticated registry lookup. These primitive APIs do not themselves implement
the production ceremony or its fresh-authorization and expiry checks.

## Bounded Installation

Maximum canonical packet size is 2 MiB. The authenticated current ciphertext
projection is at most 1 MiB/256 records; at most 32 epoch deliveries are allowed.
The D106 supplement retains its tighter independent limits and fail-closed
preimage requirement. An all-current-epoch view must not include an unnecessary
historical-authority supplement.

Verify the complete packet, anchor, recipient, authority, exact epoch set and
HPKE opening before writing a recipient store. Persist an immutable package-hash
receipt before protected history components. Serialize installation across
processes. Exact retry may finish this same installation, but another package
cannot replace it or reset an established accepted history. Recovery/fork fences
remain authoritative on retry.

History/authority publication and the final P7 owner use the existing encrypted
atomic storage boundaries. Python stores the receipt hash in a local SQLite
transaction; Dart and Swift use an encrypted receipt and OS file lock. Keys are
never stored in the receipt. Temporary mutable buffers are cleared where the
runtime permits; immutable/runtime copies are not claimed physically erased.

## Production Ceremony Binding

The epoch-backed coordinator uses the existing signed offer, acceptance, SAS
comparison and transcript proofs. It never enters the legacy-session export
path. The protected offer journal pins the original enrollment context before
authorization. The signer revalidates that exact target, context and monotonic
deadline immediately before atomically publishing enrollment plus its cached
recipient packet. A retry cannot substitute the newly observed context for the
original journal pin. Missing historical preimages remain a hard failure.

The manual artifact retains `atlasvault-pairing-artifact` version 1 and adds a
delivery payload variant containing exactly `enrollment_delivery` and
`inviter_proof`. Recipient trust comes from the independently SAS-confirmed
signed offer identity and transcript, not a signing key carried by the packet.
Only after verifying that peer's packet signature may its current-context
attestation supply D102 pins. Full D102/D106 and HPKE verification remains required.

Native custody is provisioned before the final runtime binding. The protected
owner publishes the verified key ring, anchored history and authenticated P5
current projection together. The P5 replica ingests these as remote operations;
it has no synthetic local outbox writes and retains terminal tombstones. The
small runtime binding is published last. An interrupted install with no binding
is unavailable, not an empty ACTIVE replica. Exact receipt retry can finish the
same installation; it cannot overwrite an established history.

The acknowledgement artifact contains exactly `enrollment_acknowledgement`.
Its [schema](atlasvault_enrollment_acknowledgement_v1.schema.json) binds the exact
canonical delivery-artifact SHA-256, anchor root, transcript and recipient.
Ed25519 signs `atlasvault-enrollment-acknowledgement-v1\0` followed by canonical
unsigned acknowledgement JSON. The recipient emits it only after protected
installation and runtime activation. The sender verifies it against the signed
acceptance identity and persisted delivery hash before consuming the replay
receipt. Persisted acknowledgement bytes are reused on retry. As in the existing
[identity contract](device_identity.md), CryptoKit signatures may be randomized;
unsigned bytes and verification agree across languages, not newly generated
signature bytes.

The synthetic production vector covers nonempty retained ACTIVE-author and
revoked-historical-author records plus a terminal tombstone. Gate B additionally
requires production coordinator, native custody, adversarial, real termination,
P6 and security regression evidence. Helper or vector success alone does not
complete T74, T75 or authorize C31.
