# AtlasVault Trusted-Device Registry Contract

This contract defines the Phase 2F-2 device-local trust record and durable
pairing replay state. It does not define a server registry, synchronization,
revocation, or key rotation.

## Canonical Encoding

Registry and replay objects use compact, lexicographically sorted UTF-8 JSON.
Decoders require exact key sets, canonical Base64, lowercase hexadecimal
digests, canonical lowercase UUIDs, and UTC-seconds timestamps. Boolean values
are never accepted as integers. A strict byte decoder re-encodes and compares
the entire input.

## Registry

The root has exactly eight keys:

```json
{
  "format": "atlasvault-trusted-device-registry",
  "version": 1,
  "local_device_id": "avd1-<64 lowercase hex>",
  "revision": "<canonical UUID>",
  "parent_revision": null,
  "created_at": "2026-01-15T12:00:00Z",
  "updated_at": "2026-01-15T12:00:00Z",
  "devices": []
}
```

Each peer has exactly nine keys:

```json
{
  "peer_device_id": "avd1-<64 lowercase hex>",
  "peer_descriptor": {},
  "pairing_transcript_sha256": "<64 lowercase hex>",
  "linked_at": "<UTC seconds>",
  "role": "inviter",
  "vault_id": "<validated non-semantic ID>",
  "key_epoch": 1,
  "delivery_id": "<canonical UUID>",
  "acknowledgement_sha256": "<64 lowercase hex>"
}
```

`role` is `inviter` or `invitee`. A registry contains at most 64 peers, never
contains its local device ID, has unique peer IDs, and orders peers by device
ID. The signed descriptor is verified and must derive the peer ID.

Trust commitment is create-only by logical peer ID. An exact existing peer is
idempotent. A peer with the same ID and different authenticated content is a
conflict. There is no delete, revoke, or key-epoch update operation in v1.

Every mutation creates a new revision, points `parent_revision` to the prior
revision, and advances `updated_at`. Device-local stores apply plaintext-digest
compare-and-swap while holding their platform mutation boundary.

## Durable Replay State

The replay root uses the same revision fields with:

```json
{
  "format": "atlasvault-pairing-replay",
  "version": 1,
  "local_device_id": "avd1-<64 lowercase hex>",
  "revision": "<canonical UUID>",
  "parent_revision": null,
  "created_at": "<UTC seconds>",
  "updated_at": "<UTC seconds>",
  "entries": []
}
```

An entry has exactly `kind`, `object_id`, `transcript_sha256`, `consumed_at`,
and `expires_at`. `kind` is `offer` or `acknowledgement`. The logical key is
`kind:object_id`. The same object and transcript is already consumed; the same
logical key with another transcript is a conflict.

The store retains at most 2,048 entries. Before insertion it removes entries
whose expiry is not after caller-supplied current time, then sorts by expiry,
kind, and object ID. If still oversized, it removes the earliest entries. No
in-memory permissive fallback is allowed.

## Protection And Non-Goals

Registry and replay bytes are device-local protected state: device-only
Keychain on Apple, the existing Keystore-protected blob boundary on Android,
and current-user DPAPI on Windows. They are not `.atlaspair` payloads, public
cache, backend state, or portable vault metadata. A record means only that the
reviewed pairing transaction completed; it does not provide revocation,
cross-device registry convergence, or rollback detection.
