# Device Revocation and Rotation Plan v1 (C25)

This is the T61-T63 contract, not a completed rotation engine. P3 HPKE v2 and
retained v1 readers, P5 envelopes, and P6 signed-state formats are unchanged.
The new objects have distinct format identifiers and version 1. A caller must
pin the initial registry and state root from its authenticated P6 checkpoint;
an untrusted server response is not a trust anchor.

## State Machine

| Current state | Event | Result |
| --- | --- | --- |
| ACTIVE registry / ACTIVE target | Exact-target confirmation and fresh device presence; valid signed removal | Target REVOKED; registry REVOCATION_PENDING |
| ACTIVE | Denial, cancellation, unavailable/malformed result, exception, timeout, changed target/root | No signed or persisted removal |
| Any | P6 history fence | RECOVERY_PENDING, persisted; no automatic removal |
| REVOCATION_PENDING | Identical signed retry | Idempotent no-op |
| REVOCATION_PENDING | Different transition or another removal | Hard failure; wait for C26 epoch application |
| REVOKED target | Older registry, another signature by target, attempted restoration | Rejected; terminal in this generation |
| RECOVERY_PENDING | Automatic removal or retry | Rejected; no automatic branch choice |

Each registry generation permits one removal, sequence 1, and then fences further
transitions pending rotation. Its trusted genesis includes account, vault, current
epoch, registry root and state root. C26 must derive a new generation from this
accepted transition and retain revoked entries; it must not call initialization
with an old server registry to reset terminal state. This deliberately does not
invent multi-removal batching, recovery-authority enrollment, or a destructive
last-device flow. Only active member signatures are accepted; at least one other
active device must remain. A remaining active device is the supported recovery
authority for this slice. Standalone recovery credentials do not authorize removal.

## Signed Transition

Exact fields: `format`, `version`, `account_id`, `vault_id`, `target_device_id`,
`initiator_device_id`, `prior_registry_root`, `resulting_registry_root`,
`key_epoch`, `sequence`, `authorization_category`, `root`, `signature_b64`.
Format is `atlasvault-device-revocation`; category is `DEVICE_PRESENCE`.
Identifiers are bounded ASCII; roots are 64 lowercase hex characters; positive
current epochs and sequences are less than 9007199254740991. The defined next
epoch may equal that final safe integer; no further removal generation can be
opened at the exhausted limit. Base64 is canonical and length checked.
No unknown fields are accepted.

The registry has 1-256 exact entries: `device_id`, `signing_public_b64`,
`agreement_public_b64`, `state`. The existing avd1 identity derivation validates
each identifier against both public keys. Duplicate identifiers are rejected.
`state` is ACTIVE or REVOKED. Sort by device identifier. The SHA-256 transcript is
`atlasvault-revocation-registry-v1\n` followed by lines
`device_id:signing-public-hex:agreement-public-hex:state\n`.

The transition root hashes `atlasvault-device-revocation-v1\n`, then newline-
terminated values in this exact order: account, vault, target, initiator, prior
registry root, resulting registry root, epoch, sequence, authorization category.
Version and format are checked as exact constants before hashing. Ed25519 signs
`atlasvault-revocation-signature-v1\0` plus the 32 raw root bytes. Verification
uses the initiator's key from the pinned pre-transition registry and recomputes
the result by changing only the target's ACTIVE entry to REVOKED.

The signature binds an authorization *category*, not a remotely attestable proof
that an honest UI ran. Fresh platform authorization is enforced by the trusted
client removal coordinator, as in the existing pairing authorization boundary.
A compromised active device remains a cryptographic authority within its existing
permissions. No claim of protection from its stolen private signing key is made.

Idempotency is keyed by the authenticated transition root, after full signature
verification, not by signature bytes. CryptoKit may produce distinct valid
Ed25519 signatures for identical messages. Retain the first accepted signature;
an alternate valid signature on the identical root does not rewrite history.
Roots and transition fields are deterministic across clients; generated signature
bytes need not be. The shared fixture signature must verify on all three clients.
The observed platform behavior is also described in the
[IETF CFRG Ed25519 interoperability report](https://datatracker.ietf.org/meeting/121/materials/slides-121-cfrg-divergences-of-ed25519-in-web-crypto-and-beyond-00).

## Authorization and UI

Production composition uses a removal-specific Android device-credential or
Windows Hello method, or a fresh Apple LAContext device-owner-authentication
request. No pairing/login result is reused. Only a literal boolean success is
accepted; platform exceptions remain private. An operation expires after 60
monotonic seconds, invalidates on cancellation/selection change, and revalidates
target, registry root, sequence, and P6 recovery status after the prompt and
after signing. Commit admission is the linearization point; an admitted atomic
write is not undone by subsequent UI dismissal.

The production factories require the existing P6 guarded history and existing
device identity. Tests may inject signing/authentication functions. Removal is
performed under the P6 active-history gate; a history failure latches the separate
revocation RECOVERY_PENDING state. The UI displays a validated device identifier,
registry status/root, epochs, future-access and recovery implications. Exact
target confirmation is mandatory. Public keys and signed artifact bodies are not
displayed. P8 owns complete application navigation/wiring; C25 supplies the usable
removal surface and production composition function, not a new app shell.

## Durability and Bounds

Use the existing encrypted atomic queue-file primitive with a separate
`device-revocation-v1` domain, a 1 MiB read bound, and one owning client per file.
Both clients store their own pinned genesis and signed observation. On restart,
verify the signature, context and terminal transition again. Initialization
cannot overwrite an existing file. A recovery fence has no automatic clear API.
Tests kill each independent process after durable commit and reopen its state.
Encrypted state files and in-memory test private keys are never archive evidence.

## Epoch/Rotation Protocol (Definition Only)

`atlasvault-rotation-plan` version 1 binds account/vault, previous epoch N,
new epoch N+1, prior/resulting registry roots, current accepted state root,
initiator, accepted revocation root, and sorted exact ACTIVE recipient identifiers.
The validator consumes an accepted signed removal and pinned state root. It rejects
rollback, an unauthorized skipped epoch, changed context, changed recipient set,
or reuse of an epoch number with changed bound content. The plan alone grants no
authority to apply an epoch or release key material; C26 must authenticate its
application through the signed transition and P6 history admission.

After successful removal, an authorized client must generate fresh vault-key
material using the P3 secure entropy boundary. All future patches, snapshots,
wrappers and newly written ciphertext must use N+1. Only ACTIVE recipients get
device-specific HPKE v2 delivery, bound to the plan, account/vault and recipient.
No wrapper/delivery for a revoked recipient is permitted. The C25 plan/vector
lists recipients only; it contains no vault key, wrapped key or delivery artifact.

Authorized clients may retain older epochs for policy-permitted dual reads using
the existing P3 ring; future writes use only the newest authenticated epoch.
Offline authorized devices must remain write-fenced until authenticated catch-up
(C27). Offline revoked devices can retain previously acquired data/keys; the
protocol cannot erase them remotely. Future-epoch exclusion is a C26/C28 proof,
not a claim established merely by this metadata validator.

## Deliberate Limits

C26 implements key generation/application and future-write rotation. C27 covers
offline catch-up, recovery and deletion policy. C28 proves the full platform
revocation/rotation matrix and is the phase gate. First-contact freshness,
globally unseen withholding, withheld peer evidence and local-filesystem rollback
remain outside the P6 proof. Genuine forks stay RECOVERY_PENDING, including when
safe resolution requires later P7 work. R026 remains an unsuppressed intermittent
Swift cancellation observation. External cryptographic review remains a P9/P10
production-release gate.
