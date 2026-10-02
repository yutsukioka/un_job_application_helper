# Recipient-Bound History Bootstrap v1

Scope: D102/D104/D105, C30. This contract establishes a trusted first-contact
origin; it does not authorize replacing an existing history or resolving a fork.
The production pairing-to-epoch-owner installation path is not yet complete.

The signed object is defined by
[the closed schema](atlasvault_history_bootstrap_v1.schema.json). Its root is
SHA-256 of the ASCII domain `atlasvault-history-bootstrap-v1\n` followed by
canonical JSON excluding `root` and `signature_b64`. Ed25519 signs the ASCII
domain `atlasvault-history-bootstrap-signature-v1\0` followed by the decoded
32-byte root. Canonical JSON uses the existing enrollment canonicalizer.
No HPKE or epoch construction changes.

The verifier obtains recipient identity, agreement-key fingerprint, enrollment
transcript, current registry/generation, active epoch, state root, and issuing
public authority from the authenticated enrollment ceremony. It must not derive
these expectations solely from the incoming certificate. The issuer must be
ACTIVE in the authenticated registry. The certificate also authenticates the
collection commitment and exact ciphertext-safe projection, including tombstones.

## Immutable Store Origin

- ORIGIN-1 retains the existing genesis reader and its observable validation.
- ANCHORED uses the signed anchor as its permanent origin. It uses the same
  shared chain and admission code; sequence indexing is relative to that origin.
- Authenticated storage domains bind the recipient and anchor. Neither reader
  may reinterpret the other's persisted state. Exact bootstrap retry is a no-op,
  never a write over existing history or a way to clear pending recovery.
- Contradictory signed evidence leaves the accepted state intact and durably
  fences automatic synchronization. The anchored public recovery category is
  `RECOVERY_PENDING`; underlying P6 branch evidence is retained.
- The application must preserve the authenticated origin pins when installing
  the history into an epoch owner. The current isolated-reader tests do not
  prove that production installation.

The D105 extraction preserves the original check order for origin-1 histories.
Only the origin sequence/root/registry base differs. Tombstone admission and
durable fork alarms are reused, not replaced by a separate permissive validator.

## Evidence And Limitations

The shared [public synthetic vector](test_vectors/atlasvault_history_bootstrap_v1.json)
contains no private keys, key wrappers, SAS, or private record content.
It is verified independently by Python, Dart, and Swift.
The anchor authorizes only this recipient and this confirmed enrollment.
It cannot prove globally unseen updates or repair contradictory authenticated
history. First-contact trust still depends on the authorized enrolling device
and authenticated ceremony. Local-filesystem rollback, withheld peer evidence,
physical erasure, and external Dart HPKE review remain outside this proof.
