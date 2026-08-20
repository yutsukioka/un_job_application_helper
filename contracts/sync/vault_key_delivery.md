# AtlasVault Authenticated Vault-Key Delivery Contract

This contract extends the Phase 2F-1 signed transcript. It delivers one
existing 32-byte AtlasVault vault key to an explicitly confirmed clean-install
invitee. It is not RFC HPKE and makes no claim of being HPKE.

## Short Authentication String

Both devices derive:

```text
HMAC-SHA256(
  pairing_session_key,
  UTF8("atlasvault-pairing-sas-v1:") || transcript_sha256
)[0:6]
```

The six bytes render as uppercase hexadecimal `ABCD-EF12-3456`. Both users
must explicitly confirm that the displayed codes match before delivery or
installation proceeds.

## Signed Invitee Request

`atlasvault-pairing-key-request` version 1 has exactly ten keys: format,
version, request ID, transcript SHA-256, inviter ID, invitee ID, invitee
ephemeral X25519 public key, 32-byte nonce, issue time, and expiry. Expiry must
follow issue time and be no more than 30 minutes later.

`atlasvault-signed-pairing-key-request` version 1 contains exactly `format`,
`version`, `request`, `invitee`, and a 64-byte Ed25519 signature. The signature
input is:

```text
UTF8("atlasvault-pairing-key-request-signature-v1:") ||
canonical_json(request)
```

The embedded signed descriptor identifies and verifies the invitee. The
ephemeral private key exists only in protected transaction state.

## Bootstrap

`atlasvault-pairing-bootstrap` version 1 has exactly `format`, `version`,
`snapshot_id`, `created_at`, `vault_metadata`, and `records`. Metadata is the
existing AtlasVault v1 metadata. Records remain in exact encrypted order,
including unsupported records and tombstones.

The bootstrap excludes local-store ID and timestamps, selection, public cache,
plaintext, and raw vault key. Its canonical SHA-256 binds delivery and
acknowledgement.

Every string value and object key in the canonical bootstrap must be nonempty
printable ASCII (`0x20...0x7E`). Python, Dart, and Swift enforce the same text
domain before hashing or signature verification. The rule applies during
direct model construction and strict decode, so no runtime can sign a
bootstrap another runtime must reject solely because it bypassed its decoder.

## Delivery Encryption

The inviter creates a fresh ephemeral X25519 key. Both sides derive the
ephemeral shared secret, reject all-zero output, and derive:

```text
HKDF-SHA256(
  ikm = ephemeral_x25519_shared_secret,
  salt = transcript_sha256,
  info = UTF8("atlasvault-vault-key-delivery-v1"),
  output_length = 32
)
```

AES-256-GCM encrypts exactly the 32-byte vault key with a fresh 12-byte nonce.
The transported ciphertext is `ciphertext || 16-byte tag`.

AAD is canonical `atlasvault-vault-key-delivery-aad` version 1 and binds the
delivery ID, transcript, inviter ID, invitee ID, signed-request SHA-256, vault
ID, positive signed-64-bit key epoch, bootstrap SHA-256, and expiry.

The signed delivery additionally carries the inviter ephemeral public key and
is signed over the canonical delivery with:

```text
UTF8("atlasvault-vault-key-delivery-signature-v1:") ||
canonical_json(delivery)
```

The inviter signed descriptor is embedded. Received signatures are preserved
exactly. Python fixed signatures remain the deterministic vector instances;
fresh CryptoKit signatures need only verify, consistent with the device
identity contract.

## Acknowledgement

After store-first, key-second, selection-third installation and runtime
activation, the invitee signs `atlasvault-pairing-acknowledgement` version 1.
It binds acknowledgement and delivery IDs, transcript, both device IDs, vault
ID, key epoch, bootstrap SHA-256, and installation time. The signature domain
is `atlasvault-pairing-acknowledgement-signature-v1:`.

The invitee commits inviter trust only after activation. The inviter commits
invitee trust only after verifying the acknowledgement signature, requiring its
invitee identity to equal the authenticated delivery and journaled peer, and
durably consuming the acknowledgement. The invitee stages and reads back the
exact signed acknowledgement before committing inviter trust.

## Secret Lifetime And Failure

Raw vault key, shared secret, delivery key, and ephemeral private keys are
never serialized outside platform-protected transaction state. Implementations
wipe mutable temporary copies best-effort. Every failure is fixed and redacted.
Wrong transcript, peer, request, bootstrap, epoch, expiry, signature,
ciphertext, or acknowledgement fails without installation or trust commitment.

## Deferred Work

This contract has no backend, ongoing synchronization, existing-vault merge,
revocation, or key rotation. Key epoch is authenticated but not advanced by
Phase 2F-2. A monotonic post-presentation deadline, aggregate protected-state
bounds, and explicit inviter step-up authorization are separately tracked in
issue #101 and block a production multi-device-readiness claim.
