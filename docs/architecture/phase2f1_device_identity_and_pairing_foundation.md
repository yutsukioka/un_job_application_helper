# Phase 2F-1 Device Identity And Pairing Foundation

## 1. Purpose

Phase 2F-1 defines possession-oriented installation identities, pairing
transcripts, and platform-local identity custody as foundations for a later
explicit trusted-device flow.

## 2. Scope

The phase covers strict Python, Dart, and Swift cryptographic models plus
Apple, Android, and Windows custody. It does not link devices, deliver vault
keys, add a backend, synchronize records, revoke devices, or rotate keys.

## 3. Corrected Allowlist Authorization

The user authorized a one-for-one correction retaining exactly 31 paths:
nonexistent `packages/vaultsync/vaultsync/init.py` was removed and actual
`packages/vaultsync/vaultsync/__init__.py` was authorized.

## 4. Prior Blocker

The literal original allowlist could not satisfy package-root exports. Work
stopped before branch creation until the path correction was explicit.

## 5. Package Initializer Correction

Only `__init__.py` supplies approved package-root exports. `init.py` remains
absent; no alias or compatibility shim exists.

## 6. Phase 2E-7 Baseline

Phase 2E-7 completed encrypted backup interoperability among Apple, Android,
and Windows. It intentionally provided no linked-device identity or pairing.

## 7. Why Device Identity Is Next

Multi-device key delivery requires authenticated device-held keys and a
reviewed transcript before any transport, trust registry, or sync service can
be designed safely.

## 8. Threat-Model Relationship

The normative threat model classifies present mitigations and deferred server,
rollback, replay persistence, trust, revocation, and rotation work.

## 9. Identity Assets

Each installation owns an Ed25519 seed, an X25519 private key, derived public
keys, an opaque device ID, a signed public descriptor, and a local secret
bundle.

## 10. Key Separation

Signing, agreement, vault, recovery, and pairing-session keys have separate
algorithms, domains, storage, and lifetimes.

## 11. Ed25519 Policy

Ed25519 signs descriptors, offers, and acceptances. Seeds are 32 bytes,
public keys 32 bytes, signatures 64 bytes, and verification accepts no
alternate algorithm.

## 12. X25519 Policy

X25519 derives a 32-byte shared secret from the separate agreement key pair.
All-zero output is rejected; P-256 and RSA substitutions are absent.

## 13. Device-ID Derivation

`avd1-` prefixes lowercase SHA-256 of the device-ID domain followed by signing
and agreement public keys in that exact order.

## 14. Public Descriptor

The seven-key descriptor contains only format, version, opaque ID, two public
keys, creation time, and key epoch. Strict decoders reject all extra metadata.

## 15. Signed Descriptor

The descriptor signature covers a domain plus canonical descriptor bytes. It
proves possession of the signing key, not user trust or registry membership.

## 16. Secret Bundle

The seven-key local bundle contains the opaque ID, timestamp, epoch, Ed25519
seed, and X25519 private key. Every load re-derives public keys and device ID.

## 17. Canonical Serialization

Python-style sorted compact UTF-8 JSON, strict Base64, exact key sets, strict
integers, lowercase UUID/hex, and UTC-second timestamps are normative.

## 18. Pairing Offer

The offer binds a UUID, signed inviter descriptor, 32-byte nonce, issue time,
and expiry under an Ed25519 signature.

## 19. Pairing Acceptance

The acceptance binds the exact signed-offer SHA-256, signed invitee descriptor,
32-byte nonce, and acceptance time under a separate Ed25519 signature.

## 20. Offer Expiry

Expiry must follow issue time and offer lifetime cannot exceed 600 seconds.
Current time at or after expiry fails.

## 21. Clock Skew

Verification permits at most 120 seconds of future issue or acceptance-window
skew and uses caller-supplied time rather than server authority.

## 22. Transcript Hashing

SHA-256 covers the transcript domain and exact canonical signed offer and
acceptance bytes.

## 23. Length-Prefix Requirement

Each signed envelope is prefixed with its unsigned 64-bit big-endian byte
length. Plain ambiguous concatenation is prohibited.

## 24. X25519 Session Derivation

Both roles derive the same nonzero X25519 shared secret using local private and
peer public agreement keys.

## 25. HKDF Domain Separation

HKDF-SHA256 uses the shared secret as input, transcript hash as salt,
`atlasvault-pairing-session-v1` as info, and emits 32 bytes.

## 26. Directional HMAC Proofs

Separate inviter and invitee HMAC-SHA256 domains bind role and transcript.
Proof comparisons are constant time and swapped roles fail.

## 27. Replay-Guard Requirement

Complete verification requires an injected consumer keyed by offer ID,
transcript hash, and expiry. It runs only after all cryptographic checks.

## 28. Durable Replay Deferred

Only deterministic in-memory replay guards exist in this phase. Cross-process
or cross-device durable consumption belongs to Phase 2F-2.

## 29. Self-Signature Limitation

A descriptor self-signature proves control of its private signing key. It does
not establish identity provenance, device safety, or authorization.

## 30. Metadata Leakage

Public envelopes reveal public keys, opaque IDs, UUIDs, nonces, timestamps,
epochs, and sizes. Labels, platform details, account IDs, vault IDs, and private
values are excluded.

## 31. Python Reference Implementation

`vaultsync.device_identity` and `vaultsync.pairing` provide immutable strict
models, deterministic fake-vector signing, secure generation, verification,
agreement, transcript, HKDF, HMAC, and replay interfaces.

## 32. Python Package-Root Exports

Approved APIs are re-exported from tracked `vaultsync/__init__.py` without
duplicates, side effects, file reads, or key generation.

## 33. Dart Implementation

Pure Dart models use the existing `cryptography` package without Flutter,
MethodChannel, `dart:io`, native FFI, or another cryptographic dependency.

## 34. Swift Implementation

Swift uses Foundation and CryptoKit `Curve25519.Signing`,
`Curve25519.KeyAgreement`, `HKDF<SHA256>`, and `HMAC<SHA256>` with strict
Sendable models and fixed redacted errors.

## CryptoKit Ed25519 Signature Generation Compatibility

Fixed Python vector signatures remain canonical deterministic test instances.
Swift decodes, re-encodes, and verifies their exact envelopes. CryptoKit may
generate another valid Ed25519 signature for the same key and payload; fresh
signatures are verified locally and by Python and Dart, not compared for
equality or inequality with fixed signatures. Unsigned canonical payloads are
deterministic, and a signed envelope is canonical for its supplied signature.
Fixed-vector transcripts use fixed envelopes. Runtime transcripts use exact
runtime envelopes. No external Swift dependency or custom Ed25519 arithmetic
was added.

## 35. Shared Vector

The fake vector fixes two identities, canonical payloads, fixed signatures,
signed envelopes, transcript, shared secret, HKDF output, proofs, and invalid
cases. Python is the reference generator.

## 36. Apple Keychain Custody

Checkpoint B stores one canonical bundle as a create-only,
non-synchronizable, device-only Keychain generic-password item.

## 37. Apple Limitations

Keychain custody is not claimed to use Secure Enclave and cannot protect
against an unlocked malicious process or compromised OS.

## 38. Android Custody

Checkpoint B reuses the existing Android Keystore-protected blob and no-backup
file boundary with a fixed `device-identity` purpose.

## 39. Android Limitations

StrongBox and hardware backing are not required or claimed. Process and OS
compromise remain outside this boundary.

## 40. Windows Custody

Checkpoint B reuses the strict AVWBLB01 protected blob with current-user DPAPI,
vault-independent entropy, Local AppData, and cross-process locking.

## 41. Windows Limitations

Machine-wide DPAPI is absent; TPM backing and strict machine binding are not
claimed. Same-user running malware remains out of scope.

## 42. Explicit Identity Creation

Constructing custody objects performs no platform call. Generation and
create-only persistence occur only through an explicit caller action.

## 43. No Runtime Integration

Ordinary app startup, vault activation, private state, cache behavior, and UI
do not auto-create or auto-load a device identity.

## 44. No QR UI

No QR generator, scanner, camera permission, or pairing presentation is added.

## 45. No Device Linking

No trust confirmation, trusted-device registry, or link commitment occurs.

## 46. No Vault-Key Delivery

No AtlasVault key is wrapped, transported, installed, or rotated for another
device.

## 47. No Backend

No account, device, pairing, ciphertext, or sync API is introduced.

## 48. No Synchronization

No snapshot, patch, record, tombstone, or device-list synchronization occurs.

## 49. No Revocation

No device removal or revocation state is created or enforced.

## 50. No Rotation

Device-key and vault-key rotation, epochs, rewrap, and re-encryption remain
deferred.

## 51. Checkpoint A Evidence

The preserved red commits establish missing three-language APIs and clock
bounds. Focused Python and Dart vectors pass. Swift verifies fixed signatures,
generates fresh valid signatures, and writes a public-only runtime artifact
that Python and Dart verify with matching transcript and proofs.

## 52. Checkpoint B Evidence

Pending the secure-custody red and implementation commits.

## 53. Apple Evidence

Pending fake-client and real fake-service Keychain verification.

## 54. Android Evidence

Pending fresh-process Keystore-protected identity verification.

## 55. Windows Evidence

Pending fresh-process current-user DPAPI identity verification.

## 56. Cross-Language Verification

Python, Dart, and Swift agree on fixed payloads and transcripts. The external
fresh Swift artifact proves signature and transcript interoperability without
publishing private keys, shared secrets, or session keys.

## 57. Error Redaction

Public failures are fixed and exclude private bytes, identifiers supplied in
invalid input, platform paths, OS error codes, and cryptographic intermediates.

## 58. Secret Lifetime

Mutable temporary key, secret, session, and bundle copies are wiped on a
best-effort basis. Immutable runtime/library copies cannot be guaranteed erased.

## 59. Go/No-Go

- corrected Python package initializer: authorized and used;
- nonexistent `init.py`: absent;
- formal threat model: implemented;
- Ed25519 signing identity: implemented;
- X25519 agreement identity: implemented;
- deterministic device ID: implemented;
- signed descriptor: implemented;
- signed pairing offer: implemented;
- signed pairing acceptance: implemented;
- transcript derivation: implemented;
- confirmation proofs: implemented;
- expiry validation: implemented;
- replay-guard protocol: implemented;
- durable replay store: not implemented;
- Apple custody: pending Checkpoint B;
- Android custody: pending Checkpoint B;
- Windows custody: pending Checkpoint B;
- QR pairing: not implemented;
- device linking: not implemented;
- vault-key delivery: not implemented;
- backend registry: not implemented;
- encrypted sync: not implemented;
- revocation: not implemented;
- key rotation: not implemented;
- production multi-device readiness: not claimed.

## 60. Deferred Work

Phase 2F-2 may add explicit user-mediated pairing, durable replay consumption,
a trusted-device registry, and authenticated key delivery. Sync, rollback
detection, revocation, and rotation remain separate packages.

## 61. Next Product Gate

The next gate is explicit trusted-device pairing and authenticated vault-key
delivery using these reviewed identities and transcripts. It cannot begin
until Phase 2F-1 custody is verified and merged.
