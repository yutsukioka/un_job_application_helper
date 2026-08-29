# AtlasVault Key-Delivery Cryptographic Decision And Migration Plan

Status: **RATIFIED - OPTION B**

Decision key: `P3-C09-T22`

## Decision To Record

The maintainer considered the two outcomes in
[`atlasvault_key_delivery_crypto_options.md`](atlasvault_key_delivery_crypto_options.md):

- **A - RFC 9180 HPKE:** replace the version-1 custom KEM/KDF/AEAD composition
  with an explicitly versioned HPKE envelope.
- **B - reviewed preservation:** retain the version-1 construction, remove
  production entropy injection, and make independent cryptographic review a
  release-blocking P3 condition.

Decision D047 selects **B - reviewed preservation**. The current version-1
contract remains normative, C10-C12 make no wire-format change, and the
existing vectors are retained. Outcome A remains the fallback if independent
review finds a valid weakness that cannot be corrected safely within the
ratified version-1 construction.

## Invariants For Either Outcome

The selected design must preserve all of these properties:

1. Fresh platform authorization occurs immediately before the first temporary
   inviter vault-key copy and is never cached or serialized.
2. Delivery is bound to the exact transcript, inviter, invitee, signed request,
   vault, positive key epoch, bootstrap hash, expiry, and delivery identifier.
3. The inviter's Ed25519 identity authenticates the delivery independently of
   file transport or server state.
4. Unknown algorithms, modes, versions, fields, or noncanonical bytes fail
   closed before decryption or persistence.
5. Production entropy is generated inside the sealing boundary. Deterministic
   keys and nonces remain available only through test/vector facilities.
6. Replay consumption, monotonic deadlines, aggregate bounds, staged-artifact
   integrity, and bilateral trust ordering remain unchanged.
7. Version selection is explicit and authenticated. A receiver never silently
   downgrades after a newer version has been selected for a transaction.
8. Raw vault keys, ephemeral private keys, shared secrets, and delivery keys
   are not serialized, logged, uploaded, or retained beyond their documented
   protected-state/process lifetime.
9. Python, Dart, and Swift verify the same canonical bytes and negative vectors.
10. Neither outcome claims to solve compromised-recipient recovery, registry
    convergence, revocation, key rotation, or malicious-server rollback.

## Shared Compatibility Model

### Version Authority

The delivery artifact's format and version are the cryptographic mode
authority. Algorithm inference from field shape is forbidden. Pairing
transaction state records the admitted delivery format/version so resume cannot
switch algorithms after interruption.

### Reader And Writer Policy

- Readers reject unknown versions and unsupported algorithm identifiers.
- Writers emit one configured version only; opportunistic retry with an older
  algorithm is forbidden.
- An admitted transaction finishes under its recorded version or is reset
  through the existing exact-artifact cleanup boundary.
- A wire-format migration never rewrites or re-encrypts an already exported
  artifact in place.
- Compatibility readers, if retained, are time-bounded and covered by the same
  canonical, replay, expiry, and authorization tests as the preferred writer.

### Key-Epoch Compatibility

Both outcomes retain the authenticated positive `key_epoch` field. C11 must
define current/retained epoch selection, a usable multi-epoch key ring,
crash-safe migration, recovery, and older-epoch rejection. C09 does not assign
new epoch semantics or advance an epoch.

## Outcome A Fallback Plan: RFC 9180 HPKE

If T22 is reopened and A is later ratified, the replacement work must:

1. Specify a version-2 envelope with explicit HPKE mode and ciphersuite IDs.
2. Start from the candidate Base-mode suite
   DHKEM(X25519, HKDF-SHA256)/HKDF-SHA256/AES-256-GCM, then confirm mode and
   exact encoding during implementation review.
3. Define byte-exact `info` and AAD values that retain every current binding.
4. Carry the HPKE encapsulated key and ciphertext; do not retain a
   caller-selected AES-GCM nonce field in version 2.
5. Keep the Ed25519 signed envelope unless a separately reviewed design proves
   an equally strong mapping to AtlasVault device identity and acknowledgements.
6. Raise or pin the Python cryptography floor to a version with the required
   HPKE suite, validate Apple deployment availability, and choose a reviewed
   Dart implementation/dependency strategy.
7. Add RFC 9180 vectors plus AtlasVault context/AAD vectors across all three
   languages before changing production writers.
8. Add version-1-to-version-2 transaction tests: clean cutover, in-progress
   resume, explicit reset, unsupported-version rejection, and downgrade
   rejection.
9. Switch writers to version 2 only after every supported runtime can read it.
10. Retain a version-1 reader only for the ratified transition window; record
    its removal condition in the P3 gate decision.

### Outcome A Rollback

Before version-2 writers ship, rollback is a code rollback. After version-2
artifacts can be emitted, rollback may disable new pairing but must not silently
emit version 1 to a transaction that selected version 2. A corrective release
must either retain a safe version-2 reader or require explicit transaction reset.

## Governing Outcome B Plan: Reviewed Version-1 Preservation

Under D047, C10 and later P3 chunks must:

1. Keep the current version-1 canonical envelope and algorithm identifiers
   unchanged unless an independent-review finding requires a version bump.
2. Introduce a production sealing API that internally generates the inviter
   ephemeral X25519 key and AES-GCM nonce.
3. Isolate deterministic entropy injection in clearly test-only/vector APIs
   that production composition cannot call accidentally.
4. Add repeated-key/nonce, concurrency, crash, revision, malformed-input, AAD,
   and signature-layering tests across Python, Dart, and Swift.
5. Prepare a review package covering KDF salt/info, all-zero handling, entropy,
   AAD completeness, signature order, canonicalization, key lifetime, and
   failure behavior.
6. Resolve every valid independent-review finding before the P3 gate. Any
   wire-affecting correction creates an explicit version 2 rather than silently
   changing version-1 semantics.
7. Retain the existing fixed and runtime interoperability vectors and add a
   decision-vector manifest tied to the reviewed implementation tree.

### Outcome B Rollback

The wire remains version 1. A rollback may restore an earlier implementation
only if it preserves the same canonical semantics and does not re-expose
caller-controlled production entropy. A review finding classified as
security-critical blocks rollback to an affected implementation.

## Verification Required Before Either Cutover

- deterministic red/green misuse tests in Python, Dart, and Swift;
- cross-language positive, malformed, tamper, wrong-peer, wrong-transcript,
  wrong-epoch, expiry, and downgrade vectors;
- stress, crash, and concurrency evidence for entropy ownership;
- exact compatibility behavior for in-progress protected transactions;
- platform key-custody and fresh-authorization evidence;
- a documented independent-review disposition with all valid findings closed;
  and
- one P3 gate PR at C12 with reviewed/merged tree identity.

## Ratification Record

| Field | Value |
|---|---|
| Decision | `B - reviewed version-1 preservation` |
| Date | 2026-08-29 |
| Maintainer | `yutsukioka` |
| Rationale | Preserve near-term delivery because the Dart HPKE lift and version-2 migration are infeasible in the current milestone; retain the already interoperable version-1 format under release-blocking independent review. |
| Compatibility path | Governing Outcome B plan above; no wire-format change in C10-C12 and existing vectors retained. |
| Independent-review condition | Reviewer `UNASSIGNED`; name before T28, complete review and resolve all valid findings before C12 merge and any production release. C10, C11, and P7 construction changes are included. |
| Reopen condition | Any valid construction weakness reopens T22 for Outcome A. |
| Evidence | D046, D047, and E-C09-001 through E-C09-005. |

C09 is complete under D047. C10 may proceed under the Outcome B constraints,
but no C10 implementation is part of this decision-record update.
