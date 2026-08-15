# Phase 2F-2 Trusted Device Pairing And Key Delivery

## Purpose

Phase 2F-2 turns the Phase 2F-1 possession-proof foundation into an explicit,
local, user-mediated onboarding transaction. It uses strict `.atlaspair`
artifacts, bilateral SAS confirmation, authenticated encrypted vault-key
delivery, clean-install installation, durable replay consumption, and local
trusted-device records.

## Boundary

The phase adds no account, backend, network pairing, QR flow, ongoing record
exchange, revocation, or key rotation. The milestone is local trusted-device
onboarding, not multi-device synchronization.

## Checkpoint A: Contracts And Cryptography

Python is the reference for the fake deterministic vector. Python and Dart
reproduce fixed canonical signed envelopes. Swift verifies and preserves the
fixed Python envelopes, while fresh CryptoKit signatures are required to
verify rather than equal the fixed signatures. Canonical unsigned payloads,
SAS, X25519 agreement, HKDF delivery keys, AAD, AES-GCM ciphertext, bootstrap
hashes, and acknowledgement payloads agree across all runtimes.

The trusted registry is create-only, sorted, capped at 64 peers, revisioned,
and descriptor-verified. The replay store consumes offers and
acknowledgements, is capped at 2,048 entries, and prunes deterministically.

The bootstrap reuses existing AtlasVault metadata and encrypted record
envelopes. Unsupported records and tombstones remain encrypted and ordered.
No imported record is re-encrypted merely for pairing.

## Checkpoint B: Protected State And Transport

The reviewed design protects registry, replay, and transaction bytes with
device-only Keychain on Apple, the existing Android Keystore boundary, and
current-user DPAPI on Windows. Mutations use create-only/CAS semantics and
platform atomic replacement. `.atlaspair` transport is explicit, bounded,
path-free in observable state, and retains at most one pending picker or save.

The protected transaction is the interruption authority. It contains the
ephemeral private key only while required and excludes raw vault keys from
journal serialization. Staged artifacts are hash-bound and cleaned after
completion or permitted pre-commit discard.

## Checkpoint C: Explicit Journey

The inviter requires an active encrypted vault. The invitee requires a clean
installation with empty compatibility private authorities. Neither identity
creation nor pairing starts automatically.

The inviter exports an offer, imports the acceptance, verifies the request and
invitee proof, confirms the SAS, freezes the encrypted bootstrap, exports the
delivery, imports the acknowledgement, consumes replay, then commits trust.

The invitee imports the offer, creates and exports an acceptance, confirms the
same SAS, durably consumes the offer, imports and verifies delivery, then
installs store first, protected key second, and selection third. It activates
and verifies the runtime before committing inviter trust and exporting the
signed acknowledgement.

Transactions clear last. Before selection, exact hash-bound reset is allowed.
After selection, recovery is resume-only. No trust record is created before
the role-specific commitment condition.

## Review Hardening

Android recovery import and trusted pairing share one admitted transaction
boundary, so they cannot both pass clean-install checks and persist competing
authority. Pairing creates its protected transaction before writing any staged
artifact. A pre-commit discard tolerates an artifact that was never written,
but rejects and preserves any artifact whose digest differs from the journal.

The invitee journal clears its ephemeral private key when selection commits.
Post-selection resume reloads the already-protected vault key and verifies its
recorded digest instead of retaining or recreating ephemeral key material. The
runtime-activation transition records the actual installation timestamp, and
the acknowledgement reuses that durable timestamp after interruption.

The inviter revalidates the complete signed key request, including expiry,
immediately before creating a delivery. Pairing transaction decoding accepts
the same positive 64-bit key-epoch range as the signed device and delivery
contracts; smaller implementation-specific integer bounds are not imposed.
Dart derives offer issue and expiry values from one captured clock sample, so
a second-boundary crossing cannot extend or invalidate the fixed ten-minute
offer lifetime.

Before persisting an imported acceptance, each inviter reads and verifies its
trusted-device registry. A full 64-peer registry, a mismatched local-device
identity, or an invitee already present in the registry fails before the
acceptance artifact or transaction intent is written. This prevents the
invitee from installing a vault that the inviter cannot later commit as
trusted.

Apple now journals an offer before staging it and applies the selected-vault
absence check only to invitee discard. Both coordinators treat an already
matching invitee selection as an interrupted successful create, while discard
fails closed whenever an invitee selection exists outside the recorded stage.
The exact signed acknowledgement is hash-bound and staged before the
trust-registry side effect. Resume reuses an existing staged acknowledgement;
when creation failed before any artifact existed, it may replace only the
unmaterialized same-stage intent and then stage the new signed bytes before
trust. Invitee trust uses the journaled installation timestamp,
acknowledgement-saved resume retries cleanup, and Apple replay duplicates
compare their authenticated kind, object ID, and transcript rather than local
consumption timestamps.

Imported acceptance, delivery, and acknowledgement artifacts are also
hash-bound in a same-stage journal transition before their create-only staging
write. Invitee store and key installation records the expected digest before
the corresponding create-only platform operation. Resume accepts only an
absent intended resource or an exact read-back match; discard removes only an
exact intended resource and clears the transaction last.

A fresh delivery import validates expiry against the current clock before its
digest enters the protected transaction. Once that exact digest is journaled,
retries and post-import installation revalidate the authenticated delivery at
the signed key-request issuance time rather than reapplying wall-clock expiry.
This preserves the expiry gate for new artifacts while allowing an already
admitted, hash-bound installation to finish after interruption.

An inviter verifies the signed acknowledgement and binds its invitee identity
to the authenticated delivery and journaled peer before replay consumption or
trust mutation. Android and Windows pairing operations run through their
existing cross-process private-authority admission and reject competing
pairing, plaintext-migration, recovery-import, or selected-vault authority.
Apple pairing, creation, and recovery import share one pending-transaction
authority; pairing mutations additionally fail closed after lifecycle loss or
task cancellation.

Invitee resume handles the durable `sas_confirmed` interruption point on Dart
and Swift. It accepts only an exact replay-store duplicate, completes replay
consumption idempotently, and advances to `offer_consumed`. New Apple replay
entries are rejected unless their expiry is strictly after the caller-supplied
current time.

Flutter presentation cancellation invalidates a scoped operation generation
before draining the retained task. A suspended flow must reauthorize
immediately before each later transaction, artifact, replay, registry, store,
key, selection, activation, or cleanup mutation. Backgrounding, leaving the
Settings tab, hiding the panel, disposal, and terminal stop all invalidate that
lease. Pending pairing authority remains visible while the panel is hidden,
and server-authority changes check it before any active vault is deactivated.

Canonical signed bootstrap strings are restricted to nonempty printable ASCII
on Python, Dart, and Swift. This avoids runtime-dependent Unicode scalar and
normalization behavior in cryptographic canonicalization. Swift enforces the
same recursive rule during direct bootstrap construction as well as decoding.

Apple artifact import opens the security-scoped final path with `O_NOFOLLOW`,
verifies the opened descriptor with `fstat`, and performs a bounded chunked
read that requests at most one byte beyond the remaining limit. It therefore
rejects growth past the 128 MiB boundary before appending excess bytes.

## Cross-Platform Ring

External fake artifacts exercise Apple inviter to Android invitee, Android
inviter to Windows invitee, and Windows inviter to Apple invitee. Each platform
acts once in each role. Artifacts and SHA-256 files live in the persistent
checkpoint outside the repository and contain no secret sidecar.

## Security Position

The SAS provides user-visible bilateral authentication for the exact signed
transcript. The delivery binds that transcript, both peers, request, vault,
epoch, bootstrap, expiry, and delivery ID. File theft permits denial and replay
attempts but not vault-key recovery without the invitee ephemeral private key.
Durable replay state and transaction stages fail closed across restart.

Platform protection does not defend against an already compromised unlocked
process or operating system. The local registry is not synchronized, has no
revocation semantics, and cannot detect rollback of a future malicious server.

## Verification And Go/No-Go

Checkpoint A requires exact Python/Dart vector agreement, fixed-envelope and
fresh-signature Swift verification, authenticated delivery round trips,
tamper/expiry failures, registry conflict behavior, replay behavior, and
secret-free errors. Checkpoints B and C add real platform persistence,
transport, interruption, role-cycle, build, and cross-process evidence before
merge.

Go only for this bounded local onboarding milestone when all three checkpoints,
the exact 53-file scope, all three role cycles, platform builds, exact-head
reviews, and artifact scans pass. Production multi-device readiness remains a
no-go until issue #101 adds a monotonic post-presentation deadline, aggregate
protected-state bounds, and explicit step-up authorization before vault-key
release. Ongoing ciphertext synchronization, trusted-list convergence,
revocation, and key rotation remain deferred.

## Next Product Gate

The next phase may design authenticated ciphertext synchronization only after
this local onboarding boundary is merged and archived. It must separately
address malicious-server replay, monotonic state, conflict convergence,
device-list synchronization, and later revocation/key rotation.
