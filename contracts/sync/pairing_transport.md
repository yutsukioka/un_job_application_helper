# AtlasVault Pairing Artifact Transport Contract

Phase 2F-2 uses explicit user-mediated `.atlaspair` files. There is no network,
QR, camera, discovery, cloud, or server pairing transport.

## Outer Envelope

Every artifact is strict canonical UTF-8 JSON with exactly four keys:

```json
{
  "format": "atlasvault-pairing-artifact",
  "version": 1,
  "kind": "offer",
  "payload": {}
}
```

`kind` is exactly `offer`, `acceptance`, `delivery`, or `acknowledgement`.
Unknown keys, unknown kinds, malformed UTF-8, noncanonical JSON, and trailing
bytes fail.

## Payloads

- `offer`: exactly `signed_offer`.
- `acceptance`: exactly `signed_acceptance`, `signed_key_request`, and the
  32-byte canonical-Base64 `invitee_proof`.
- `delivery`: exactly `signed_delivery`, `bootstrap`, and the 32-byte
  canonical-Base64 `inviter_proof`.
- `acknowledgement`: exactly `signed_acknowledgement`.

Nested objects use their own strict contracts. A receiver verifies signatures,
transcript relationships, expiry, directional proof, replay state, and current
transaction state. File extension or MIME type is only a picker convenience.

## File Handling

Import and export are explicit. Platform transport enforces a bounded complete
read, one pending operation, cancellation without side effects, and no path in
observable state. Saves use a same-directory temporary file, complete write,
flush, atomic replacement, read-back, and failure cleanup where the platform
permits. Apple uses sandboxed file import/export, Android uses SAF without a
persisted URI permission, and Windows uses owned shell dialogs with
`FOS_DONTADDTORECENT`.

The transaction journal stores canonical artifact hashes and stages only the
minimum bytes required for interruption recovery. Ephemeral private keys are
protected transaction state and never appear in `.atlaspair` files.

For each imported or generated artifact, the transaction records its expected
kind, byte count, and SHA-256 in a same-stage protected-journal transition
before the create-only staging write. Resume accepts an existing artifact only
when the complete canonical bytes match that intent. A generated delivery may
be adopted after interruption only after signature verification and complete
transaction binding. The invitee stages and reads back its signed
acknowledgement before committing inviter trust.

The platform transports enforce per-artifact bounds. Aggregate protected-state
bounds, a monotonic local deadline after presentation, and inviter step-up
authorization are tracked as production-readiness blockers in issue #101.

## Privacy

Artifacts may contain public descriptors, UUIDs, timestamps, nonces, signed
proofs, encrypted bootstrap records, and one encrypted vault-key delivery.
They never contain a raw vault key, device private key, pairing session key,
delivery key, raw ephemeral private key, plaintext private record, local path,
public cache, platform protected blob, or backend credential.

Anyone holding an artifact can copy, delay, or replay it. Authentication,
expiry, durable replay consumption, explicit SAS confirmation, and transaction
state are therefore mandatory. File possession alone establishes no trust.
