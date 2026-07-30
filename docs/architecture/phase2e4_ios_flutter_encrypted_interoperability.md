# Phase 2E-4 iOS-Flutter Encrypted Interoperability

## 1. Purpose

Provide explicit encrypted AtlasVault export and clean-install import between
Apple and Flutter/Android without a plaintext intermediary.

## 2. Scope

This phase uses `atlasvault-export` version 1 and recovery-wrap v2. It excludes
cloud sync, device linking, Windows storage, existing-vault replacement, and
new Apple private-record features.

## 3. Phase 2E-3 Baseline

Phase 2E-3 established Android encrypted private authority, selected-vault
commitment, protected migration journaling, interruption recovery, and explicit
authority transition.

## 4. Existing Apple Export

Apple already creates strict canonical encrypted exports after recovery-wrap
verification and complete temporary hydration.

## 5. Existing Apple Import

Apple already performs journaled, store-first, key-second, selection-last
clean-install recovery import with read-back verification.

## 6. Existing Dart Export Envelope

Dart strictly parses and canonically encodes the six-key export envelope.

## 7. Existing Dart Recovery Wrap

Dart recovery-wrap v2 matches Swift and Python HKDF-SHA256, AES-256-GCM, AAD,
recovery-key text, and canonical JSON behavior.

## 8. Existing Android Secure Storage

Android uses a non-exportable Keystore master key, no-backup encrypted local
stores, protected journals, and create-only selection.

## 9. Portability Contract

Documents preserve export and vault metadata, key wraps, ordered encrypted
records, revisions, tombstones, nonces, and ciphertext. Device-local store
identity is never portable.

## 10. No Plaintext Intermediary

Only canonical encrypted export bytes cross the document boundary. No
plaintext record, decrypted state, cache snapshot, or plaintext sidecar is
created.

## 11. Recovery Key As Separate Channel

Recovery text is displayed or entered separately and remains absent from the
document, protected storage, observable owner, logs, and paths.

## 12. Interoperability Vector

The fake `atlasvault_ios_flutter_interop_vectors_v1.json` vector contains one
case per direction, exact bytes and digests, ordered records, tombstones, and
fake expected hydrated values.

## 13. Direct Encrypted Artifact Exchange

Tests optionally exchange two `.atlasvault` files under the persistent
checkpoint. Only encrypted documents, digests, and fixed non-secret logs are
permitted there.

## 14. Checkpoint A Boundary

Checkpoint A implements Flutter recovery export, Android save transport,
Flutter presentation, and Flutter-to-Apple proof. Flutter import is excluded.

## 15. Flutter Export Availability

Availability requires an active runtime, matching selection, no mutation, no
migration journal, no import journal, and no other interoperability operation.

## 16. New Recovery Setup

Setup generates an unpersisted key, displays it once, requires re-entry, adds
one v2 wrap, CAS-replaces the store, and verifies read-back before export.

## 17. Existing Recovery-Wrap Export

Exactly one existing v2 wrap requires explicit matching key entry. It is
verified and preserved rather than regenerated.

## 18. Recovery-Key Display Lifetime

The one-shot handle owns mutable code units and returns text once. Widget-local
values clear before awaits, cancellation, hiding, lifecycle loss, and disposal.

## 19. Store Metadata Update

Setup preserves existing passphrase wraps, appends one recovery wrap, updates
only the local-store update timestamp, and uses SHA-256 CAS.

## 20. Record Preservation

Ordered encrypted records remain byte-identical. Ciphertext, nonces, IDs,
revisions, parent revisions, key IDs, and tombstones are not rewritten.

## 21. Export Canonicalization

The coordinator creates a fresh UUID and UTC-seconds timestamp, strict-decodes
its own bytes, requires byte-identical re-encoding, and computes SHA-256.

## 22. Android Save-Document Bridge

The bridge uses `ACTION_CREATE_DOCUMENT`, one pending operation, a 128-MiB
bound, no temporary file, no retained URI, and fixed cancellation/error output.

## 23. Flutter Export Presentation

The owner exposes only fixed status/message, encrypted-record count, and wrap
presence. It retains no key, identifier, bytes, path, URI, or private value.

## 24. Flutter To Apple Proof

Swift imports the Flutter vector through the existing production coordinator
and verifies ordering, hydration, tombstones, selection, and plaintext absence.

## 25. Checkpoint B Boundary

Checkpoint B adds picker, strict preparation, protected import journal, secure
installation, resume/reset, presentation, and Apple-to-Flutter proof.

## 26. Clean-Install Import Policy

Import requires no selected vault, target resource, plaintext authority,
migration journal, or conflicting import.

## 27. Plaintext/Migration Gate

Every in-memory, cache, compatibility endpoint, and migration authority must be
empty and inactive before import persistence.

## 28. Existing-Vault Policy

Import never replaces, merges, or selects over an existing vault.

## 29. Android Open-Document Bridge

Checkpoint B uses `ACTION_OPEN_DOCUMENT`, one openable document, a bounded
read, no persistable permission, and no returned URI or path.

## 30. Strict Import Preparation

Picked bytes are strict-decoded, canonically re-encoded, and digest-bound
before recovery entry or persistent work.

## 31. Recovery Verification

Exactly one v2 wrap is required. Wrong input creates no journal, store, key, or
selection.

## 32. Record Hydration Validation

Every record is authenticated before installation. Unsupported families and
tombstones remain encrypted and unrendered.

## 33. Recovery-Import Journal

Checkpoint B uses a Keystore-protected canonical CAS journal bound to opaque
transaction identifiers and resource digests.

## 34. Import Journal Privacy

The journal excludes export bytes, keys, recovery text, records, paths, URIs,
and plaintext.

## 35. Store-First Ordering

Imported metadata and records enter a new local-store envelope before any key
or selection.

## 36. Key-Second Ordering

The recovered key uses the existing create-only Android secure-key boundary
after store read-back verification.

## 37. Selection-Last Ordering

Selection is the commit point and follows store/key read-back verification.

## 38. Runtime Activation

Activation remains explicit and follows committed selection.

## 39. Journal-Clear-Last

The journal clears only after committed state is fully verified.

## 40. Interrupted Resume

Resume reselects the same digest-bound export and re-enters the recovery key.
Completed steps are verified before advancing.

## 41. Pre-Selection Reset

Explicit reset removes only matching partial resources while selection is
absent and clears the journal last.

## 42. Post-Selection Resume-Only Behavior

Once selection exists, reset is forbidden; only verification and completion
are allowed.

## 43. Additional Private-Record Preservation

Unsupported Apple private families remain encrypted in order and unprojected.

## 44. Tombstone Preservation

Tombstones are imported unchanged, authenticated, and never rendered.

## 45. Apple To Flutter Proof

Checkpoint B consumes Apple vector and direct artifact bytes through the
production Flutter coordinator.

## 46. Native Picker Smoke

Android evidence covers explicit cancellation and completion without exposing
document-provider identity.

## 47. Android Integration Evidence

Real-emulator tests cover secure installation, resume, reset binding,
clean-install admission, and no plaintext transport.

## 48. Apple Test Evidence

Swift proves Flutter-origin import without Apple production-source changes.

## 49. Public-Cache Privacy

Interoperability never writes private payloads into the public cache.

## 50. Compatibility Endpoint Isolation

It does not call compatibility saved-search or tracker endpoints.

## 51. Secret Lifetime

Keys, plaintext, and transport copies use mutable buffers and best-effort
wiping. Dart strings and crypto internals prevent universal zeroization.

## 52. Error Redaction

Production errors omit keys, IDs, values, paths, URIs, hashes, and providers.

## 53. No Cloud Dependency

The flow is local document transport and requires no cloud service.

## 54. No Windows Storage

Windows secure storage and installation remain deferred.

## 55. No Existing-Vault Merge

Conflict merge, multiple selection, and replacement are not implemented.

## 56. TDD Checkpoint A

A valid red commit precedes implementation. Focused Dart and Swift evidence is
stored persistently.

## 57. TDD Checkpoint B

Checkpoint B will preserve a separate red commit before import implementation.

## 58. Verification

Each checkpoint requires focused/full tests, Android and Apple builds, source
guards, exact scope, protected-path checks, and empty artifact scans.

## 59. Go/No-Go

Checkpoint A status:

- Flutter recovery export: implemented.
- Flutter recovery setup: implemented.
- Android encrypted-document save: implemented.
- Apple import of Flutter export: implemented.
- Flutter import of Apple export: pending Checkpoint B.
- Android secure installation: pending Checkpoint B.
- Interrupted Flutter import resume: pending Checkpoint B.
- Import reset before selection: pending Checkpoint B.
- Direct encrypted artifact exchange: pending full two-direction gate.
- Canonical byte agreement: implemented for Flutter-to-Apple.
- No plaintext intermediary: implemented for Checkpoint A.
- Existing-vault replacement: not implemented.
- Cloud sync: not implemented.
- Windows secure storage: not implemented.
- Linked-device synchronization: not implemented.
- Production cross-platform privacy readiness: not claimed.

## 60. Deferred Work

Cloud sync, linked devices, Windows storage, key rotation, replacement, and
conflict merging remain outside this phase.

## 61. Next Product Gate

Phase 2E-5 is declaration-only here and remains separately gated after both
interoperability directions merge.
