# AtlasVault Key-Delivery Cryptographic Options

Status: **RATIFIED: OPTION A - RFC 9180 HPKE**

Baseline: `638eafeba38e03fa943d2a6b366fff76af1c3d31`

## Decision Scope

P3/C09 compared the cryptographic envelopes available to deliver an existing
32-byte AtlasVault vault key during explicit trusted-device onboarding:

- **Option A:** adopt RFC 9180 Hybrid Public Key Encryption (HPKE).
- **Option B:** preserve the current construction, conditional on independent
  cryptographic review and resolution of every valid finding.

Decision D050 supersedes D047 and selects Option A. This document preserves the
comparison and historical recommendation; the governing compatibility details
are in `atlasvault_key_delivery_crypto_decision.md`.

## Verified Current Construction

The version-1 delivery envelope is not HPKE. It currently uses:

- a fresh invitee ephemeral X25519 public key in the signed key request;
- a fresh inviter ephemeral X25519 key pair per production delivery;
- rejection of an all-zero X25519 shared secret;
- HKDF-SHA256 with the transcript hash as salt and
  `atlasvault-vault-key-delivery-v1` as `info`;
- AES-256-GCM over exactly 32 vault-key bytes with a 12-byte nonce;
- canonical AAD binding delivery, transcript, both devices, request, vault,
  epoch, bootstrap, and expiry;
- a separate Ed25519 signature over the canonical delivery envelope; and
- P2 monotonic deadlines, fresh inviter authorization, replay state, aggregate
  bounds, and fail-closed transaction recovery around the primitive.

The production Dart and Swift transaction coordinators generate fresh
ephemeral keys and nonces. The Python, Dart, and Swift primitive APIs accept
those values from callers to support deterministic cross-language vectors.
That test seam is also a production misuse hazard until C10 separates it from
the production sealing API.

## Option A: Adopt RFC 9180 HPKE

### Candidate Profile

The least disruptive candidate is HPKE Base mode with:

| Component | Identifier | Algorithm |
|---|---:|---|
| KEM | `0x0020` | DHKEM(X25519, HKDF-SHA256) |
| KDF | `0x0001` | HKDF-SHA256 |
| AEAD | `0x0002` | AES-256-GCM |

The invitee's ephemeral request key remains the HPKE recipient key. The
existing Ed25519 delivery signature remains the inviter-authentication layer,
so HPKE authenticated mode is not required merely to duplicate that identity
binding. The final C10 design would have to specify exact `info`, AAD, envelope
encoding, and whether Base mode remains the approved mode.

### Security

Advantages:

- RFC 9180 supplies reviewed labeled KEM extraction/expansion, key scheduling,
  domain separation, AEAD base-nonce derivation, and sequence handling.
- A single-shot HPKE API owns encapsulation randomness and nonce derivation,
  reducing the number of caller-controlled cryptographic parameters.
- Standard algorithm identifiers and published vectors make the construction
  easier to compare with independent implementations.

Residual risks:

- HPKE does not provide application replay protection, trusted-device policy,
  downgrade prevention, or revocation; AtlasVault must retain those controls.
- Recipient-key compromise can expose earlier ciphertexts. Using an ephemeral
  invitee key limits that key's intended lifetime but does not survive an
  already compromised recipient process or OS.
- Bad encapsulation randomness can destroy confidentiality. Production entropy
  ownership and failure behavior still require review.
- The existing Ed25519 signature and AtlasVault AAD/versioning remain custom
  protocol layers and still need cross-language review.

### Interoperability

- RFC 9180 defines the wire-level KEM/KDF/AEAD algorithms and vectors.
- Apple CryptoKit exposes an HPKE API, subject to deployment-target and exact
  ciphersuite validation during implementation.
- Python `cryptography` added RFC 9180 HPKE in version 47.0.0, while the
  repository currently declares `cryptography>=42.0`; adoption therefore
  requires an explicit supported-version decision rather than relying on the
  current lower bound.
- The repository's Dart `cryptography` 2.9.0 dependency documents X25519,
  HKDF, and AEAD primitives but no HPKE API. Option A therefore needs a reviewed
  Dart HPKE implementation, a suitable dependency, or platform bridges before
  it can be considered cross-platform.

### Migration Cost

Cost is **high** relative to Option B:

- define a version-2 delivery envelope and canonical encoding;
- replace explicit inviter ephemeral public key and nonce fields with the HPKE
  encapsulated key and ciphertext representation;
- establish exact `info` and AAD mappings without weakening current bindings;
- add RFC and AtlasVault vectors across Python, Dart, and Swift;
- support or deliberately terminate in-progress version-1 transactions;
- define dual-read/write cutover and downgrade behavior; and
- review dependency floors and platform availability.

### Review Burden

The protocol-composition burden is lower because the KEM and key schedule are
standardized. The implementation, version migration, signature layering,
transaction integration, and Dart support still require independent review.

## Option B: Preserve the Current Construction Under Independent Review

### Security

Advantages:

- The construction already binds the complete onboarding context and has
  deterministic three-language vectors and runtime interoperability evidence.
- Each production delivery currently uses fresh X25519 and AES-GCM randomness.
- Existing signatures, replay guards, fresh authorization, and transaction
  recovery remain unchanged.

Required conditions:

- C10 must internalize production ephemeral-key and nonce generation and leave
  deterministic injection only in an unmistakable test/vector boundary.
- An independent cryptographic reviewer must assess the KDF salt/info choices,
  AAD and signature layering, all-zero handling, entropy ownership, error
  behavior, canonical envelope, and key lifetime.
- Every valid review finding must be resolved before P3 can be GREEN.
- The protocol must reserve an explicit version transition; silent fallback or
  algorithm negotiation is forbidden.

Residual risks:

- AtlasVault retains the proof burden for a bespoke composition even though
  each primitive is standard.
- Cross-language implementations can agree with one another while sharing the
  same design mistake.
- Future maintainers must preserve domain separation and field binding without
  the guardrails of an RFC-defined key schedule.

### Interoperability

Interoperability cost is **low** in the near term. Python, Dart, and Swift
already implement the version-1 envelope with fixed and runtime evidence, and
no new dependency or platform availability floor is required.

### Migration Cost

Cost is **low to medium**:

- preserve version-1 wire bytes and existing in-progress transactions;
- split production and deterministic test APIs without changing the envelope;
- expand misuse, malformed-input, property, and cross-language coverage; and
- incorporate independent-review changes under an explicit version bump when
  a finding changes wire semantics.

### Review Burden

The review burden is **high and release-blocking**. Primitive correctness and
interoperability tests do not replace an independent construction review.

## Comparison

| Criterion | Option A: RFC HPKE | Option B: Reviewed preservation |
|---|---|---|
| Standardized KEM/key schedule | Strong | No; AtlasVault-specific |
| Production nonce misuse surface | Lower after HPKE API adoption | Must be removed explicitly in C10 |
| Existing cross-language support | Incomplete, especially Dart | Complete for version 1 |
| Wire compatibility | Requires version 2 and migration | Preserves version 1 |
| Near-term implementation cost | High | Low to medium |
| Long-term protocol review burden | Lower | Higher and continuous |
| Independent review | Still required for integration/migration | Required for the entire construction |
| Replay/revocation solution | Not provided by HPKE | Not provided |
| Compromised-recipient protection | Not provided | Not provided |

## Technical Recommendation For Ratification

**Provisional recommendation: Option A, provided the project accepts the Dart
implementation/dependency work and a version-2 migration.** Standardizing the
KEM and key schedule removes more bespoke cryptographic surface than API
hardening alone and gives later reviewers a clearer security reference.

**Option B remains a defensible near-term choice only when independent review
is funded as a P3 gate and C10 first removes caller-selected production entropy.**
Its lower migration cost is real, but it does not reduce the custom protocol's
review burden.

### Maintainer Disposition

Decision D050 ratifies Option A because standardizing the KEM and key schedule
before nonce and epoch work is less costly than preserving a recurring custom
construction review burden. C10 introduced the isolated HPKE v2 seam and C11
added bounded epoch support while retaining v1 read compatibility.

D054 supplies objective engineering assurance through official RFC 9180
vectors, reference-differential equality, and fail-closed adversarial tests.
This is not production sign-off: external human review of the Dart RFC 9180
composition remains release-blocking at P9/P10.

## Primary References

- [RFC 9180: Hybrid Public Key Encryption](https://www.rfc-editor.org/rfc/rfc9180.html)
- [Apple CryptoKit HPKE](https://developer.apple.com/documentation/cryptokit/hpke)
- [Python cryptography HPKE](https://cryptography.io/en/47.0.0/hazmat/primitives/hpke/)
- [Dart cryptography 2.9.0 API](https://pub.dev/documentation/cryptography/latest/)
- [`contracts/sync/vault_key_delivery.md`](../../contracts/sync/vault_key_delivery.md)

## Ratification

- [x] **A - Adopt RFC 9180 HPKE.**
- [ ] **B - Preserve version 1 under independent cryptographic review.**

Ratified by maintainer decision D050 on 2026-08-29. D054 governs the C12
engineering-conformance gate. External human review remains a P9/P10 release
gate.
