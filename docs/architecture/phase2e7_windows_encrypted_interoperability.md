# Phase 2E-7 Windows AtlasVault Encrypted Interoperability

## 1. Purpose

Phase 2E-7 makes Windows a producer and clean-install consumer of the existing
portable AtlasVault encrypted backup. It adds Windows document transport and a
recoverable device-local install transaction without creating another export
format.

## 2. Scope

The phase covers explicit Windows recovery export, explicit Windows recovery
import, direct encrypted artifact exchange with Apple and Android, and the
Windows DPAPI transaction state needed to resume or safely reset an import.
Cloud sync, device linking, existing-vault replacement, and key rotation remain
out of scope.

## 3. Phase 2E-6 Baseline

Phase 2E-6 supplies current-user DPAPI vault-key storage, canonical encrypted
local-store I/O, selected-vault state, plaintext migration, and one shared
cross-process plaintext-authority admission lock. This phase reuses those
boundaries and does not alter Windows plaintext migration.

## 4. Existing Shared Export Envelope

The portable envelope remains `atlasvault-export` version 1. Its fields,
canonical JSON rules, metadata, recovery-wrap v2, record ordering, encrypted
record envelopes, and tombstone semantics are unchanged.

## 5. Existing Apple Export/Import

Apple already exports and imports the shared envelope through its production
recovery coordinator and protects installed keys with Keychain. The Apple
implementation is used unchanged to produce the Apple-to-Windows case and to
consume the Windows-origin case.

## 6. Existing Android Export/Import

Android already exports and imports the shared envelope through the Dart
coordinator, Android encrypted-document transport, Android Keystore boundary,
and Android-profiled protected import journal. Its journal bytes and install
ordering remain unchanged.

## 7. Existing Windows DPAPI Boundary

Windows protects 32-byte vault keys with current-user DPAPI,
`CRYPTPROTECT_UI_FORBIDDEN`, vault-bound entropy, and independent key-hash
verification. Machine-wide DPAPI, Credential Manager, Windows Hello, and TPM
guarantees are not introduced.

## 8. Existing Windows Selected-Vault State

The existing DPAPI-protected selected-vault marker is create-only and is read
back before authority is committed. Import reuses this marker and creates it
only after the encrypted store and protected key are verified.

## 9. Portability Contract

Portable metadata and encrypted records are platform-neutral. Windows creates
a new local-store ID and local timestamps on installation, but it does not
rewrite imported vault metadata, re-encrypt records, reorder records, or reuse
the export ID as a local-store ID.

## 10. Recovery Key As Separate Channel

The 60-symbol fake or user-held recovery-key text travels separately from the
encrypted document. No recovery key or recovery-key sidecar is written with a
`.atlasvault` artifact.

## 11. No Plaintext Intermediary

Export and import move canonical encrypted bytes only. Temporary document
files contain only the encrypted transport envelope, and no decrypted payload,
raw vault key, public cache, compatibility state, or recovery key is serialized.

## 12. New Windows Interoperability Vector

`atlasvault_windows_interop_vectors_v1.json` is fake test data with strict
Apple-to-Windows, Android-to-Windows, and Windows-to-Apple-and-Android cases.
Each case fixes deterministic cryptographic inputs, canonical export bytes,
SHA-256, record counts, fake payload expectations, unsupported records, and
tombstones.

## 13. Direct Encrypted Artifact Exchange

Direct tests write only fake encrypted `.atlasvault` files and SHA-256 files to
the external Phase 2E-7 checkpoint. Recovery keys stay in the checked-in fake
vector, and no generated artifact is written into the repository.

## 14. Checkpoint A Boundary

Checkpoint A adds Windows production interoperability assembly, explicit
recovery export, native encrypted-document save, the Windows-origin vector,
and Apple/Android consumption of the exact Windows artifact. It does not add
Windows import persistence.

## 15. Windows Export Availability

Export requires an active encrypted runtime whose selected-vault marker matches
the active vault. Pending plaintext migration, pending recovery import,
deactivation, or another interoperability operation blocks export.

## 16. Windows Recovery Setup

When no recovery-wrap v2 exists, the generic coordinator generates a secure
32-byte recovery key and one-shot display text, requires explicit re-entry,
adds exactly one recovery-wrap v2, and verifies the updated store before export.

## 17. Existing Recovery-Wrap Export

When exactly one recovery-wrap v2 already exists, export requires explicit
recovery-key entry and verifies that it unwraps the active vault key. Duplicate
recovery wraps fail closed without mutation.

## 18. Recovery-Key Lifetime

Recovery-key text and decoded bytes remain in local operation memory only.
Inputs are cleared before awaits and after cancellation, lifecycle loss,
completion, hiding, or disposal; temporary byte copies are wiped best-effort.

## 19. Metadata CAS Update

Adding a recovery wrap uses the existing encrypted local-store CAS transaction.
Valid passphrase-wrap v1 entries and encrypted records are preserved, then the
updated canonical store is read back and authenticated before export proceeds.

## 20. Record-Byte Preservation

The exporter preserves the ordered encrypted-record JSON fields, ciphertext,
nonce, revisions, parent revisions, tombstone flags, and key identifiers. A
platform transition does not cause record re-encryption.

## 21. Export Canonicalization

Generated export bytes are strictly decoded, canonically re-encoded, and
required to be byte-equal. SHA-256, recovery unwrap, and complete record
hydration are verified before the save operation is offered.

## 22. Windows Save Dialog

The runner owns an `IFileSaveDialog` using the Flutter window HWND,
filesystem-only selection, overwrite prompting, `.atlasvault` filtering,
no-current-directory changes, and no recent-document entry. Worker I/O writes a
same-directory random temporary file, flushes, atomically replaces, reads back,
and compares before returning success.

## 23. Windows Export Presentation

The generic interoperability owner exposes fixed stages, fixed messages,
encrypted-record count, and recovery-wrap presence only. It exposes no key,
vault ID, export ID, record ID, private value, path, filename, or export bytes.

## 24. Windows-To-Apple Proof

The Apple production parser and recovery-import coordinator consume the exact
Windows canonical bytes, install through the existing Keychain-oriented test
boundary, preserve record order, and render only supported active records.

## 25. Windows-To-Android Proof

The Android production coordinator consumes the exact Windows bytes and uses
real Android secure storage for store-first, key-second, selection-last
installation. Unsupported records and tombstones remain encrypted and hidden.

## 26. Checkpoint B Boundary

Checkpoint B adds Windows encrypted-document picking, the Windows recovery
import journal/profile, clean-install gates, cross-process admission, DPAPI
installation, staged resume, pre-selection reset, and Apple/Android artifact
installation on Windows.

## 27. Windows Clean-Install Import

Import requires no selected vault, inactive encrypted runtime, no plaintext
migration, no unrelated pending import, no in-memory private records, no
private durable or retained cache records, and empty available compatibility
private endpoints.

## 28. Existing-Vault Rejection

Any existing selected-vault marker returns a fixed existing-vault result.
Import never merges, replaces, clears, or enumerates an existing vault.

## 29. Plaintext/Migration Gate

Private plaintext returns `migrationRequired` and performs no journal, store,
key, or selection write. An unavailable compatibility authority fails before
any persistent import side effect.

## 30. Windows Open Dialog

The runner owns one `IFileOpenDialog` using the Flutter window HWND,
filesystem-only single selection, existing-file/path requirements,
`.atlasvault` filtering, no current-directory changes, and no recent-document
entry. Cancellation returns null; Dart receives bytes, never a path.

## 31. Strict Import Preparation

Picked bytes must be nonempty and at most 128 MiB. The coordinator strictly
decodes and canonically re-encodes the export, requires byte equality and
exactly one recovery-wrap v2, validates every encrypted record, and retains the
bytes only in operation memory.

## 32. Recovery-Key Verification

Explicitly submitted recovery-key text is parsed and consumed before the
persistent transaction. The wrap must recover exactly 32 vault-key bytes, and
every encrypted record must decrypt and authenticate successfully.

## 33. Record Hydration Validation

Supported payloads are strictly decoded; tombstones and unsupported private
records are authenticated without being published. Hydrated verification data
is discarded before persistence begins.

## 34. Windows Recovery-Import Profile

Windows journals use the strict format
`atlasvault-windows-recovery-import`. Android retains
`atlasvault-android-recovery-import` and its existing canonical key set. Each
store rejects the other platform's local profile.

## 35. DPAPI-Protected Import Journal

The Windows channel stores `imports/recovery-import.bin` under the managed
Local AppData root using the existing AVWBLB01 protected-blob envelope,
current-user DPAPI, `CRYPTPROTECT_UI_FORBIDDEN`, purpose-specific entropy,
create-only writes, CAS replacement, and hash-bound deletion.

## 36. Import-Journal Privacy

The canonical journal contains transaction IDs, stage, timestamp, and SHA-256
fingerprints. It contains no export bytes, encrypted records, recovery key,
raw vault key, path, filename, private payload, or public cache.

## 37. Cross-Process Import Admission

Windows import uses the Phase 2E-6 durable-cache OS mutation lock and scoped
reentrancy lease. Final clean-install recheck, journal creation, store, key,
selection, activation, and journal completion execute in one admitted async
transaction; picker interaction and key entry occur before lock acquisition.

## 38. Store-First Installation

The transaction creates a canonical local store with a new local-store UUID
and local timestamps, reads it back, verifies its SHA-256, and only then
advances the journal to `store_created`.

## 39. Key-Second Installation

After store verification, the 32-byte vault key is protected through the
create-only current-user DPAPI key store, loaded, compared in constant time,
and journaled as `key_created`.

## 40. Selection-Last Commitment

The selected-vault marker is created only after store and key verification.
It is read back for an exact match before the journal advances to
`selection_committed`; selection is the import commit point.

## 41. Runtime Activation

The selected encrypted vault is activated through the existing private-state
runtime. Supported private records must match the verified import, while public
cache output remains private-free and compatibility private calls remain zero.

## 42. Journal-Clear-Last

After activation, the journal advances to `completion_pending`, is deleted by
expected digest, and is verified absent. Journal deletion is the final
persistent transaction step.

## 43. Interrupted Resume

Resume requires explicit file reselection and recovery-key re-entry. Prepared,
store-created, key-created, selection-committed, and completion-pending stages
verify export, key, store, and selection fingerprints before idempotently
continuing.

## 44. Pre-Selection Reset

Explicit reset is permitted only in prepared, store-created, or key-created
stages. It verifies and removes only the exact journal-bound store and key,
deletes the journal last, and reopens legacy admission only after cleanup.

## 45. Post-Selection Resume-Only Behavior

At or after `selection_committed`, reset is unavailable. A selected import is
the encrypted authority and must be resumed to activation and journal cleanup;
partial plaintext authority is never recreated.

## 46. Unsupported Record Preservation

Authenticated but unsupported private-record envelopes remain byte-identical,
ordered, encrypted, and unrendered in the installed local store and every
subsequent export.

## 47. Tombstone Preservation

Tombstone envelopes remain ordered and encrypted. They are validated during
import but never hydrated into active saved-search or tracker projections.

## 48. Apple-To-Windows Proof

The Apple production export coordinator produces the exact fake vector bytes.
Windows imports those bytes through the production Dart coordinator and real
DPAPI/store/selection adapters, then verifies supported, unsupported, and
tombstone behavior.

## 49. Android-To-Windows Proof

The Android/Flutter production export coordinator produces the exact fake
vector bytes. Windows performs the same real DPAPI installation and activation
checks without changing the imported encrypted records.

## 50. Native Dialog Smoke

Native dialog source and MethodChannel behavior are covered deterministically,
and interactive cancellation is attempted only when a usable Windows desktop
session is available. A noninteractive or black Parallels framebuffer is
recorded as a limitation and is not reported as manual picker evidence.

## 51. Windows Integration Evidence

The Windows-local NTFS checkout runs real current-user DPAPI journal CAS,
Apple/Android artifact installs, exact stage interruption/resume, reset,
authority admission, runtime activation, and Debug/Release `/W4 /WX` builds.

## 52. Android Integration Evidence

The Android emulator installs the exact Windows-origin artifact through real
Android secure storage and verifies installation order, active projections,
opaque-record preservation, tombstones, cache privacy, and endpoint isolation.

## 53. Apple Test Evidence

Swift tests generate the Apple-origin artifact and import the exact
Windows-origin artifact through production format/coordinator types. Generic
Apple simulator builds remain part of the gate.

## 54. Public-Cache Privacy

Active and imported private state is never written to the public cache. The
existing hard plaintext guard and `withoutPrivateState()` write policy remain
in force.

## 55. Compatibility Endpoint Isolation

Recovery import requires a clean compatibility authority, then holds Windows
cross-process admission throughout commitment. Imported encrypted authority
does not read or write compatibility private endpoints.

## 56. Secret Lifetime

Raw recovery and vault keys exist only in bounded operation memory, are copied
only where required by reviewed interfaces, and are wiped best-effort after
use. DPAPI plaintext buffers use secure wipe and Windows-owned allocations are
released through the existing native boundary.

## 57. Error Redaction

Dart and native failures use fixed errors and descriptions. They expose no
path, filename, Win32 code, vault ID, record ID, key, export bytes, or private
payload value.

## 58. No Cloud Dependency

All transport is explicit local encrypted-document I/O. No backend, cloud
account, network synchronization, or server-held vault state is introduced.

## 59. No Existing-Vault Merge

Import is create-only and clean-install only. Cross-vault merge, replacement,
multiple selected vaults, and record conflict reconciliation remain deferred.

## 60. No Linked-Device Behavior

The phase adds no device identity, pairing, QR exchange, device descriptor,
ciphertext patch synchronization, revocation, or key rotation.

## 61. TDD Checkpoint A

Checkpoint A preserves a red test commit followed by the implementation commit
for Windows export and Apple/Android consumption. Focused unit, source,
Windows, Swift, Android, and direct-artifact gates validate that boundary.

## 62. TDD Checkpoint B

Checkpoint B preserves a separate red test commit followed by the Windows
import implementation commit. Unit and real Windows integration cover strict
journals, profiles, transaction ordering, every resume stage, reset, DPAPI
storage, selection, and activation.

## 63. Verification

The merge gate includes Dart formatting and analysis, focused and full Flutter
tests, macOS/Android/Windows builds, Windows and Android integrations, full
Swift tests, both Apple simulator builds, Python vectors, direct artifact hash
and sentinel checks, source guards, exact scope, protected paths, CI, security
scanning, and exact-head review.

## 64. Go/No-Go

- Windows recovery export: implemented.
- Windows recovery setup: implemented.
- Windows encrypted-document save: implemented.
- Apple import of Windows export: implemented.
- Android import of Windows export: implemented.
- Windows import of Apple export: implemented.
- Windows import of Android export: implemented.
- Windows DPAPI installation: implemented.
- Windows interrupted import resume: implemented.
- Windows pre-selection reset: implemented.
- Direct three-platform encrypted exchange: implemented.
- Canonical byte agreement: implemented.
- No plaintext intermediary: implemented.
- Existing-vault replacement: not implemented.
- Cloud sync: not implemented.
- Linked-device identity: not implemented.
- Pairing: not implemented.
- Production multi-device readiness: not claimed.

## 65. Deferred Work

Device identity, malicious-server and stolen-device threat modeling, signing
and key-agreement identities, pairing transcripts, QR onboarding, ciphertext
synchronization, rollback detection, device removal, and key rotation remain
separately gated.

## 66. Next Product Gate

Phase 2F-1 defines the trusted device-identity and threat-model foundation. It
must establish signing and key-agreement identities, secure platform custody,
device-ID derivation, key-usage separation, and replay-resistant expiring
pairing transcripts without adding a sync backend or pairing UI.
