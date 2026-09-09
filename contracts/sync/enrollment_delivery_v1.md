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

## Current Evidence Boundary

The shared vectors prove recipient HPKE agreement, protected epoch-owner
installation, exact retry/reopen, and both retained-author cases. They do not
prove production coordinator wiring, native secure-custody provisioning, P5
runtime projection installation, or a completed user-mediated pairing ceremony.
Those remain mandatory before T74/Gate B can be claimed. No C31 authorization is
implied by a successful primitive installation.
