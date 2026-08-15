# AtlasVault Multi-Device Threat Model

## 1. Purpose

This document sets release constraints for trusted multi-device work. It is a
normative boundary for later phases, not a claim that multi-device operation is
ready.

## 2. Scope

Phase 2F-1 covers installation identities, possession proofs, pairing
transcripts, confirmation proofs, expiry, an injected replay-consumption
protocol, and platform-local identity custody. It excludes actual trust,
transport, key delivery, server state, sync, revocation, and rotation.

## 3. Security Properties Under Review

The reviewed properties are key-role separation, strict canonical parsing,
signature authenticity, transcript binding, bilateral agreement, freshness
bounds, replay admission, local secret custody, redacted failures, and absence
of private material from public structures.

## 4. System Assets

Assets are classified by confidentiality, integrity, authenticity, freshness,
and availability. A later design cannot weaken the protections stated here
without a separate review.

## 5. Vault Plaintext

Private saved searches, tracker data, and future private records require
confidentiality and integrity. Phase 2F-1 neither transports nor decrypts them.

## 6. Vault Key

The AtlasVault key requires confidentiality and controlled delivery. It is not
part of any Phase 2F-1 descriptor, transcript, artifact, or custody bundle.

## 7. Device Signing Private Key

The 32-byte Ed25519 seed proves possession and authenticates descriptors and
pairing messages. It must remain in platform-protected local custody.

## 8. Device Agreement Private Key

The separate 32-byte X25519 private key derives pairing session secrets. It is
never reused as a signing key or serialized into public structures.

## 9. Recovery Key

Recovery keys remain governed by encrypted-backup contracts. They are not
device identity, pairing input, or a substitute for trust confirmation.

## 10. Pairing Offer

The offer is public signed metadata with a nonce and short validity window. Its
integrity and freshness matter; its contents are not confidential.

## 11. Pairing Acceptance

The acceptance is public signed metadata bound to the exact signed-offer hash.
Its integrity, participant binding, and freshness matter.

## 12. Pairing Transcript

The transcript hash binds the exact canonical signed envelopes using explicit
length prefixes. Received signatures may not be replaced before hashing.

## 13. Pairing Confirmation Proofs

Directional HMAC proofs demonstrate both participants derived the same session
key and transcript while preserving role separation.

## 14. Trusted-Device State

No trusted-device registry exists in Phase 2F-1. A self-signed descriptor is
not trusted-device state.

## 15. Encrypted Server State

No account or ciphertext server is implemented here. Future server state must
remain untrusted for confidentiality, ordering, completeness, and freshness.

## 16. Trust Boundaries

Boundaries exist at platform secret storage, process memory, user-mediated
pairing transport, account services, networks, and future ciphertext storage.

## 17. Apple Keychain

Apple identity custody uses a non-synchronizable, device-only Keychain item.
This protects at-rest material subject to Keychain and OS compromise limits.

## 18. Android Keystore

Android custody uses the existing Keystore-protected local blob under no-backup
storage. Hardware backing is not assumed or required.

## 19. Windows DPAPI

Windows custody uses current-user DPAPI with UI forbidden and Local AppData.
Machine-wide scope and a TPM guarantee are explicitly absent.

## 20. Local Process Boundary

Private keys and derived session material enter process memory for approved
operations. Best-effort wiping reduces lifetime but cannot defeat a compromised
process or operating system.

## 21. Account-Service Boundary

Future account services are routing and availability dependencies, not trust
roots. Tokens cannot authorize descriptor substitution or plaintext access.

## 22. Ciphertext-Server Boundary

A future ciphertext server is assumed malicious: it may replay, omit, reorder,
fork, or withhold valid encrypted state.

## 23. Adversary Assumptions

Attackers may control networks and future servers, steal tokens, copy public
pairing material, manipulate clocks within platform limits, or obtain a device.
Cryptographic primitives and correctly patched platform secure storage are
assumed to behave as specified until the OS is compromised.

## 24. Malicious Sync Server

**Classification: designed only.** Signatures and future authenticated state
can limit fabrication, but Phase 2F-1 has no sync protocol, freshness ledger,
or fork detection. Server rollback is not solved.

## 25. Compromised Account Token

**Classification: partially mitigated.** A token alone cannot forge an
Ed25519 possession proof. A later registry and authorization design must still
prevent enrollment, omission, and replay abuse.

## 26. Network Attacker

**Classification: partially mitigated.** Signatures, transcript binding,
X25519, proofs, and expiry detect alteration and impersonation without private
keys. No transport or traffic-analysis protection exists yet.

## 27. Stolen Locked Device

**Classification: partially mitigated.** Platform-local custody protects
identity material according to each platform's lock and credential policy.
Offline extraction resistance varies and hardware backing is not guaranteed.

## 28. Stolen Unlocked Device

**Classification: deferred.** An attacker using an unlocked device may invoke
authorized signing or read process-accessible secrets. Phase 2F-1 does not
claim protection.

## 29. Same-User Local Malware

**Classification: outside model.** Malware running as the same user may access
process memory or invoke platform APIs. Current-user DPAPI and ordinary
Keychain/Keystore access do not provide a complete defense.

## 30. Compromised Operating System

**Classification: outside model.** A compromised kernel or trusted platform
service can bypass process and storage boundaries. No claim survives total OS
compromise.

## 31. Pairing-Offer Screenshot

**Classification: designed only.** Offers contain public data and expire in at
most 600 seconds. A later UI must authenticate user intent and warn that a
screenshot can be forwarded during that window.

## 32. Pairing-Offer Forwarding

**Classification: partially mitigated.** Invitee acceptance, exact offer-hash
binding, session proofs, and user confirmation can detect substitution. Phase
2F-1 provides no interactive confirmation or transport binding.

## 33. Pairing Replay

**Classification: partially mitigated.** The verifier requires injected
single-use consumption after all cryptographic checks. Durable cross-process
and cross-device replay storage is deferred.

## 34. Acceptance Replay

**Classification: partially mitigated.** Acceptance is bound to one exact offer
and transcript, and duplicate consumption fails in the protocol. Durable replay
enforcement remains deferred.

## 35. Device-Descriptor Substitution

**Classification: mitigated in Phase 2F-1.** Strict self-signature validation,
device-ID recomputation, and transcript signatures reject key or descriptor
substitution. This proves possession, not trustworthiness.

## 36. Device-List Substitution

**Classification: deferred.** No signed trusted-device list exists. A later
registry must authenticate revisions and membership.

## 37. Device-List Omission

**Classification: deferred.** A malicious server can omit a device or revision
until a completeness and consistency protocol exists.

## 38. Encrypted Snapshot Rollback

**Classification: deferred.** Phase 2F-1 has no synchronized snapshot sequence,
checkpoint witness, or rollback detector.

## 39. Record Rollback

**Classification: deferred.** Existing record revisions authenticate local
history but no multi-device global freshness authority exists.

## 40. Tombstone Resurrection

**Classification: deferred.** Existing encrypted tombstones are preserved, but
future sync needs monotonic revision and deletion conflict rules.

## 41. Recovery-Key Brute Force

**Classification: outside model.** Recovery-wrap v2 is inherited unchanged.
Phase 2F-1 neither weakens nor replaces its entropy requirements.

## 42. Metadata Observation

**Classification: partially mitigated.** No private payload or private key is
placed in public envelopes. Device IDs, public keys, timing, UUIDs, and message
sizes remain observable.

## 43. Clock Manipulation

**Classification: partially mitigated.** Caller-supplied current time, a
600-second lifetime, and 120-second skew limits reject many stale/future
offers. A later UI also needs a monotonic deadline after presentation.

## 44. Revoked-Device Replay

**Classification: deferred.** No revocation registry or epoch distribution
exists. Phase 2F-1 cannot identify a revoked but cryptographically valid key.

## 45. Key-Epoch Rollback

**Classification: designed only.** Key epochs in the shared signed 64-bit range
`1...9223372036854775807` are authenticated in descriptors, but no trusted
monotonic epoch authority exists.

## 46. Security Goals

Phase 2F-1 must provide strict cross-runtime structures, possession proofs,
separate signing/agreement keys, deterministic device IDs, transcript-bound
agreement, directional proofs, bounded freshness, replay admission, and local
secret custody without leaking private material.

## 47. Explicit Non-Goals

Actual pairing, trust commitment, QR transport, vault-key delivery, account
registration, backend storage, synchronization, rollback detection,
revocation, and rotation are non-goals.

## 48. Metadata Leakage

Public envelopes disclose device IDs, public keys, UUIDs, timestamps, nonces,
key epoch, and sizes. They must never contain labels, account identifiers,
platform details, vault IDs, or private data.

## 49. Device-ID Leakage

The opaque deterministic ID permits correlation of the same installation where
descriptors are reused. It does not encode a user, platform, model, or account.

## 50. Key Separation

Ed25519 signs; X25519 agrees. Vault keys, recovery keys, signing seeds,
agreement keys, and derived session keys are distinct and domain-separated.

## 51. Pairing Expiry

Offers expire strictly before the verifier's current time reaches expiry and
cannot have a lifetime over 600 seconds.

## 52. Clock-Skew Policy

At most 120 seconds of future issue skew and acceptance-window skew is allowed.
Server time is never authoritative.

## 53. Replay-Consumption Requirement

Every complete verifier requires a replay guard and consumes only after
signature, relation, transcript, agreement, and proof verification. No
permissive default is allowed. In-process check-and-consume is serialized so
concurrent verifier threads cannot both admit the same transcript. Durable
cross-process and cross-device replay state remains deferred.

## 54. Server Non-Authority

Future servers cannot establish identity trust, select keys, determine
freshness alone, or decrypt private data. Clients must authenticate all state.

## 55. Self-Signed Descriptor Limitation

A self-signature proves control of the descriptor signing key. It does not
prove identity ownership, user approval, device safety, registry membership,
or authorization to receive a vault key.

## 56. Platform-Custody Limitations

Keychain, Keystore, and DPAPI provide device/user-local at-rest boundaries with
different semantics. Phase 2F-1 claims neither universal hardware backing nor
resistance to unlocked-process or OS compromise.

## 57. Phase 2F-1 Mitigations

Strict models, canonical payloads, Ed25519 verification, X25519 agreement,
length-delimited transcript hashing, HKDF, directional HMAC, expiry checks,
mandatory replay admission, redacted errors, and explicit local custody are
implemented. Signature-byte variation from a valid randomized API is not a
trust failure. Failure to verify a valid cross-platform signature is a
compatibility failure.

## 58. Deferred QR Transport

**Classification: deferred.** QR or equivalent user-mediated transport needs
separate size, authenticity, screenshot, forwarding, and accessibility review.

## 59. Deferred Key Delivery

**Classification: deferred.** No vault key is wrapped or delivered. A later
phase must bind delivery to the verified transcript and explicit confirmation.

## 60. Deferred Durable Trust Registry

**Classification: deferred.** No local or server trusted-device registry is
created. Trust establishment remains absent.

## 61. Deferred Backend

**Classification: deferred.** No account/device API, ciphertext service, or
server authorization rule is implemented.

## 62. Deferred Rollback Protection

**Classification: deferred.** Snapshot, record, device-list, and key-epoch
rollback detection require a later authenticated monotonic design.

## 63. Deferred Revocation

**Classification: deferred.** Device removal, propagation, offline behavior,
and stale-device handling remain unimplemented.

## 64. Deferred Key Rotation

**Classification: deferred.** Vault-key epochs, device-key rotation, rewrap,
and re-encryption policies remain unimplemented.

## 65. Required Adversarial Tests

Tests must cover signature and payload tamper, key substitution, wrong device
ID, unknown fields, non-canonical values, excessive lifetime, expiry, future
issue, wrong offer hash, same-device pairing, all-zero X25519, transcript
tamper, proof-role swap, duplicate replay consumption, malformed secret
bundles, create collisions, protected-store tamper, and fresh-process loads.
They must also verify fixed Python signatures and fresh CryptoKit signatures
across Python, Dart, and Swift without substituting signature bytes.

## 66. Release-Blocking Conditions

Release is blocked by parser ambiguity, signature verification failure,
transcript disagreement for identical envelopes, optional replay admission,
private material in public artifacts or logs, auto-created identity, insecure
platform storage, unsupported hardware claims, missing fresh-process evidence,
scope expansion, or any implementation of trust/linking/key delivery before a
separate review. Different valid signed envelopes may have different transcript
hashes; treating that expected difference as equivalent to a mismatch for the
same bytes is incorrect.

## 67. Phase 2F-2 Local Onboarding Boundary

Phase 2F-2 authorizes explicit file-mediated pairing, durable local replay
consumption, authenticated encrypted vault-key delivery, clean-install
installation, and bilateral local trust records. It does not authorize a
backend, network transport, synchronized records, revocation, or rotation.

## 68. Stolen Pairing Artifact

**Classification: mitigated for confidentiality and trust; denial remains.**
Offer and acceptance artifacts are signed and transcript-bound. Delivery is
encrypted to the invitee ephemeral X25519 key and binds both device IDs,
request, bootstrap, vault, epoch, expiry, and delivery ID. A thief can delete,
delay, copy, or replay files, but cannot decrypt the vault key without the
protected ephemeral private key. Durable replay state rejects consumed logical
objects. File possession alone never commits trust.

## 69. Authentication-String Mismatch

**Classification: fail closed.** Both users must explicitly confirm the same
six-byte SAS rendered as `XXXX-XXXX-XXXX`. Delivery and installation remain
blocked until the role-specific confirmation. The UI warns users to compare on
both recognized devices rather than through the artifact channel.

## 70. Transaction Interruption

**Classification: mitigated locally.** Platform-protected transaction state,
hash-bound staged artifacts, monotonic stages, create-only resources, and
read-back verification support resume. Before selected-vault creation, reset
deletes only exact staged resources. After selection, reset is unavailable and
the transaction is resume-only. Transaction clearing is last.

## 71. Registry Equivocation

**Classification: partially mitigated locally; synchronization deferred.** A
device-local registry is strict, revisioned, create-only, descriptor-verified,
and conflict-safe. No server or peer can replace a committed logical peer
silently. Registries are not synchronized, so cross-device list convergence,
server equivocation detection, and monotonic rollback protection remain open.

## 72. Delivery-Key And Ephemeral-Key Lifetime

**Classification: mitigated best-effort.** Delivery uses fresh ephemeral
X25519, transcript-salted HKDF-SHA256, and AES-256-GCM. The delivery key,
shared secret, and raw vault key are process-local and wiped where mutable.
The invitee ephemeral private key may persist only inside protected transaction
state until installation or permitted cleanup.

## 73. Bilateral Trust Asymmetry

**Classification: explicitly ordered.** The invitee commits inviter trust only
after store/key/selection installation, runtime activation, and bootstrap
verification. The inviter commits invitee trust only after verifying and
durably consuming the signed installation acknowledgement. An interrupted
exchange can therefore be incomplete but cannot silently claim bilateral
completion.

## 74. Remaining Multi-Device Risks

Phase 2F-2 still has no ongoing ciphertext exchange, synchronized registry,
malicious-server rollback state, remote removal, compromise recovery, or vault
key rotation. A previously paired stolen device remains trusted until a later
revocation and epoch-rotation phase. The local onboarding milestone must not be
described as full multi-device readiness.
