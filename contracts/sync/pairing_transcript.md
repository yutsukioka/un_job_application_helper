# AtlasVault Pairing Transcript Contract

Phase 2F-1 defines cryptographic transcript objects and verification rules. It
does not define QR, camera, network, discovery, account, or vault-key transport.

## Canonical Encoding

Objects use the same strict sorted, compact UTF-8 JSON rules as the device
identity contract. Canonical unsigned payload bytes are deterministic. A
signed envelope is canonical relative to its supplied 64-byte signature.
Received signature bytes are verified and retained exactly; they are never
regenerated or normalized. A received signed offer or acceptance must be
decoded from its UTF-8 bytes, reconstructed under the strict model,
canonically re-encoded, and compared with the complete input. Whitespace, key
reordering, duplicate keys, malformed UTF-8, and any other noncanonical byte
representation are rejected before transcript derivation.

## Pairing Offer

The unsigned offer has exactly seven keys:

```json
{
  "format": "atlasvault-pairing-offer",
  "version": 1,
  "offer_id": "<canonical lowercase UUID>",
  "inviter": {},
  "nonce": "<canonical Base64 for 32 bytes>",
  "issued_at": "<strict UTC-seconds timestamp>",
  "expires_at": "<strict UTC-seconds timestamp>"
}
```

`inviter` is a strictly verified signed device descriptor. Production nonces
use secure randomness. Expiry must follow issue time and the lifetime must not
exceed 600 seconds.

The signed offer has exactly four keys:

```json
{
  "format": "atlasvault-signed-pairing-offer",
  "version": 1,
  "offer": {},
  "signature": "<canonical Base64 for 64 bytes>"
}
```

The Ed25519 signature input is:

```text
UTF8("atlasvault-pairing-offer-signature-v1:") || canonical_json(offer)
```

## Pairing Acceptance

The unsigned acceptance has exactly seven keys:

```json
{
  "format": "atlasvault-pairing-acceptance",
  "version": 1,
  "offer_id": "<canonical lowercase UUID>",
  "offer_sha256": "<64 lowercase hex>",
  "invitee": {},
  "nonce": "<canonical Base64 for 32 bytes>",
  "accepted_at": "<strict UTC-seconds timestamp>"
}
```

`invitee` is a strictly verified signed device descriptor. The nonce is 32
secure-random bytes in production. The signed acceptance has exactly four keys:

```json
{
  "format": "atlasvault-signed-pairing-acceptance",
  "version": 1,
  "acceptance": {},
  "signature": "<canonical Base64 for 64 bytes>"
}
```

The Ed25519 signature input is:

```text
UTF8("atlasvault-pairing-acceptance-signature-v1:") ||
canonical_json(acceptance)
```

Verification requires matching offer IDs, the exact SHA-256 of the received
canonical signed offer, different inviter and invitee IDs, valid descriptor and
acceptance signatures, and an acceptance timestamp inside the skew-adjusted
offer window.

## Clock Policy

The maximum clock skew is 120 seconds. Verification requires caller-supplied
current time; server time is not authoritative. Reject when current time is at
or after expiry, issue time is more than 120 seconds in the future, acceptance
time is outside the skew-adjusted offer window, or offer lifetime exceeds 600
seconds. A later interactive phase must also maintain a monotonic local
deadline after displaying an offer.

## Transcript Hash

```text
SHA-256(
  UTF8("atlasvault-pairing-transcript-v1:") ||
  uint64_be(signed_offer_byte_count) ||
  signed_offer_canonical_bytes ||
  uint64_be(signed_acceptance_byte_count) ||
  signed_acceptance_canonical_bytes
)
```

Both length prefixes are mandatory. The transcript uses the exact signed
envelope bytes exchanged. Different valid signatures can therefore produce
different valid transcript hashes. Cross-platform agreement is required for
the same received bytes. Fixed-vector assertions use the fixed Python
envelopes; runtime verification uses the actual runtime envelopes.

## Pairing Session Key

The inviter computes X25519 with its private agreement key and the invitee
public agreement key; the invitee computes the inverse operation. Both require
the same 32-byte nonzero shared secret. All-zero output is invalid.

```text
HKDF-SHA256(
  ikm = x25519_shared_secret,
  salt = transcript_sha256,
  info = UTF8("atlasvault-pairing-session-v1"),
  output_length = 32
)
```

The shared secret and session key are process-local and never serialized.
Mutable retained session-key copies expose explicit best-effort destruction;
callers destroy them immediately after confirmation use.

## Directional Confirmation Proofs

```text
inviter_proof = HMAC-SHA256(
  pairing_session_key,
  UTF8("atlasvault-pairing-confirm-inviter-v1:") || transcript_sha256
)

invitee_proof = HMAC-SHA256(
  pairing_session_key,
  UTF8("atlasvault-pairing-confirm-invitee-v1:") || transcript_sha256
)
```

Comparisons are constant time. Swapping roles fails.

## Replay Guard

The verifier requires an injected replay-consumption implementation:

```text
consume(offer_id, transcript_sha256, expires_at)
  -> accepted | already_consumed
```

There is no permissive default. Consumption occurs only after descriptor,
offer, acceptance, offer-hash, transcript, X25519, and confirmation-proof
verification. The check-and-consume operation is atomic for concurrent callers
sharing one guard; exactly one caller can receive `accepted`. Duplicate
consumption fails. Phase 2F-1 permits only in-memory deterministic test guards;
durable replay persistence is deferred.

## Signature Generation Compatibility

The fixed Python vector provides one stable valid signature instance per
envelope. Every runtime verifies those exact bytes. A platform cryptographic
API may generate another valid Ed25519 signature for the same payload. That is
not a wire incompatibility: the canonical envelope includes the actual
signature, and all participants hash and verify that exact received envelope.

## Non-Goals

This contract establishes neither trust nor actual pairing. It does not define
transport, device discovery, a trusted-device registry, vault-key delivery,
backend state, synchronization, revocation, or key rotation.
