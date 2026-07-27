# Phase 2E-1 Dart AtlasVault Compatibility Foundation

## 1. Purpose

Provide a runtime-neutral Dart implementation of the reviewed AtlasVault wire
formats and cryptographic vectors. This is a compatibility foundation, not a
Flutter privacy-completion claim.

## 2. Scope

The phase adds strict in-memory models, canonical JSON, vector-compatible
record crypto, historical passphrase-wrap v1 compatibility, recovery-wrap v2,
and encrypted-export verification. It adds no UI, storage, networking, or app
runtime wiring.

## 3. Phase 2D-64 Apple Closure Baseline

The final planned Apple-only saved-search closure is merged. Apple list,
create, delete, edit, rename, and lock-before-public-search execution are the
reviewed baseline; this phase starts cross-platform parity instead of another
Apple private-record feature.

## 4. Cross-Platform Privacy Gap

Flutter has no production AtlasVault session, encrypted local store, secure
platform key boundary, recovery journey, or bidirectional encrypted exchange.

## 5. Existing Flutter Plaintext Cache Risk

The current local cache still serializes `saved_searches` and
`tracker_records` into plaintext JSON. Phase 2E-1 records that blocker and does
not modify, migrate, or create a second storage authority.

## 6. Existing Compatibility Endpoints

Flutter saved-search and tracker compatibility endpoints remain in the current
data path. The new library does not call or replace them.

## 7. Existing Shared Contracts

The implementation follows `contracts/sync/encrypted_vault.md` plus current
Python and Swift behavior. Opaque vault and record identifiers retain their
established validation rules; export identity remains a canonical lowercase
UUID.

## 8. Existing Shared Vectors

The payload, record-crypto, passphrase-wrap, and recovery/export vector JSON
files are consumed directly and remain byte-identical.

## 9. Dart Package Boundary

Consumers import `package:atlas/atlas_vault.dart`. Internal implementation
lives under `lib/src/atlas_vault/`, and the barrel exposes only reviewed models
and operations.

## 10. Runtime-Neutral Policy

Production AtlasVault files import no Flutter API, `dart:io`, FFI, browser API,
platform channel, controller, cache, API-client, or UI type.

## 11. Dependency Decision

The only direct runtime dependencies added are `cryptography` 2.9.0 and
PointyCastle 4.0.0, as resolved by the current Dart SDK.

## 12. Why `cryptography` Is Used

`cryptography` supplies AES-256-GCM, HKDF-SHA256, SHA-256, HMAC-SHA256, and
reviewed secure random generation across the target Dart and Flutter
platforms.

## 13. Why PointyCastle Is Limited To Argon2id V1

PointyCastle is imported only by the v1 passphrase derivation path. It exists
solely to reproduce the historical Argon2id vector and is not used for record
or recovery cryptography.

## 14. Why `cryptography_flutter` Is Deferred

No platform optimization plugin is needed for format compatibility. Adding a
plugin would cross the later runtime/platform boundary, so it is deliberately
absent.

## 15. Checkpoint A Boundary

Checkpoint A added tests and then implemented formats only. No cryptographic
dependency or operation existed until the strict-format checkpoint was green,
committed, and pushed.

## 16. Strict Semantic Parsing

Semantic decode accepts harmless JSON whitespace and key order differences,
then validates exact field sets, types, versions, algorithms, and scalar
formats.

## 17. Canonical ASCII JSON

Canonical output is compact UTF-8 JSON with recursively sorted object keys,
preserved arrays, lowercase non-ASCII `\u` escapes, surrogate pairs for
non-BMP scalars, and no trailing newline.

## 18. Scalar Validation

Validators reject missing and unknown fields, Boolean or floating integer
aliases, explicit null where omission is required, malformed UUIDs,
timestamps, dates, Base64, unsupported versions, and isolated UTF-16
surrogates. Canonicalization also rejects malformed UTF-16 keys and values
rather than silently replacing them.

## 19. Payload-Envelope Models

Strict schema-1 envelopes cover saved searches, saved jobs, application notes,
profile snippets, and draft metadata. Optional fields are omitted rather than
serialized as null.

## 20. Encrypted-Record Model

The model validates the exact metadata set, schema version, nonempty opaque
identifiers, 12-byte canonical nonce, and ciphertext containing at least a
16-byte GCM tag.

## 21. Versioned Key-Wrap Models

Dispatch accepts historical passphrase v1 without `wrap_version` and recovery
v2 with `wrap_version == 2`. Unknown types, versions, fields, algorithms,
lengths, and duplicate wrap identifiers fail.

## 22. Local-Store Model

The in-memory `atlasvault-local-store` version-1 envelope validates timestamps,
metadata, and ordered encrypted records. It performs no filesystem I/O.

## 23. Encrypted-Export Model

The exact six-field `atlasvault-export` version-1 envelope validates canonical
export identity, UTC seconds, metadata, and ordered encrypted records. Store
IDs, paths, selection state, and raw keys are absent.

## 24. Checkpoint B Boundary

Checkpoint B added missing-crypto tests before dependencies or implementation.
It then implemented only the reviewed pure-Dart compatibility operations.

## 25. Record-Key HKDF

Record keys use a 32-byte vault key, salt
`atlas-vault:v1:<vault_id>`, info `record:<record_id>`, HKDF-SHA256, and a
32-byte output.

## 26. Record AES-GCM

Record sealing uses AES-256-GCM, a 12-byte nonce, canonical authenticated
metadata, and serialized `ciphertext || 16-byte tag`. Open and seal match the
shared vector exactly.

## 27. Passphrase-Wrap V1 Compatibility

PointyCastle Argon2id uses the vector memory, iterations, parallelism, salt,
version 1.3, and 32-byte output. AES-256-GCM reproduces the exact v1 wrap.
Untrusted values are bounded by the documented v1 production profile:
65,536 KiB memory, three iterations, and four lanes.

## 28. Historical V1 Vault-Binding Limitation

Passphrase v1 AAD authenticates format, version, wrap ID/type, and KDF
parameters but excludes vault ID. Dart preserves this limitation byte-for-byte
and does not use v1 for new recovery material.

## 29. Recovery-Key Codec

The codec combines 32 recovery bytes with the first five bytes of
SHA-256 over the domain-separated input, emits uppercase unpadded Base32, and
uses the exact grouped `AVRK1` text.

## 30. Recovery-Wrap V2

Recovery v2 derives a 32-byte wrapping key with HKDF-SHA256 and seals the
32-byte vault key with AES-256-GCM, a 32-byte salt, and a 12-byte nonce.

## 31. Vault-Bound AAD

V2 AAD binds the validated vault ID, fixed wrap identity/type/version,
AES-256-GCM profile, HKDF algorithm, salt, and info.

## 32. Encrypted-Export Compatibility

Dart reproduces canonical export bytes and their lowercase SHA-256 digest,
strictly reparses the result, and unwraps its single recovery wrap in memory.
The current shared export vector contains no encrypted records.

## 33. Python Agreement

Dart matches Python canonical plaintext, record key, AAD, record ciphertext,
v1 wrap, recovery text, v2 wrap, export bytes, and export digest.

## 34. Swift Agreement

Dart matches Swift canonical payload/export behavior, record HKDF/AES-GCM,
recovery text, recovery v2 AAD/wrap, and strict versioned models.

## 35. Malformed-Input Policy

Malformed JSON, fields, scalar aliases, identifiers, dates, Base64, algorithms,
versions, lengths, and wrap collisions fail with fixed non-echoing errors.

## 36. Tamper And Wrong-Key Policy

Wrong vault, passphrase, or recovery keys and changes to AAD, identifiers,
parameters, salt, nonce, ciphertext, or tag fail closed.

## 37. Secret-Lifetime Limitations In Dart

Mutable temporary passphrase bytes, key copies, derived keys, decrypted vault
keys, and plaintext copies are zero-filled where practical, and
`SecretKeyData.destroy()` is used. A retained recovery-key object has an
idempotent `destroy()` operation that zero-fills its buffer and rejects later
use. Dart copies and dependency internals prevent a universal zeroization
guarantee. Secrets are never logged or included in errors or descriptions.

## 38. Public Dart API

The public barrel exposes strict models, fixed errors, record operations,
passphrase v1 compatibility, recovery-key/recovery-wrap operations, export
unwrap, and SHA-256. Test vector loading and internal helpers are not exported.

## 39. No Flutter Runtime Integration

`atlas.dart`, app controllers, app shell, cache, saved-search, and tracker code
remain unchanged. Construction and use are explicit library calls only.

## 40. No Platform Key Storage

Android Keystore, Windows DPAPI/Credential Manager, Apple Keychain, secure
storage plugins, and platform channels are outside this phase.

## 41. No Plaintext Migration

No plaintext saved-search or tracker data is removed or migrated. An explicit
rollback-capable decision remains separately gated.

## 42. No UI

No screen, widget, route, state owner, or automatic operation uses the library.

## 43. No Cloud Sync

No network or synchronization behavior is added.

## 44. TDD Checkpoint A Evidence

The named red commit precedes the named strict-format implementation commit.
Focused tests, analysis, and the full Flutter suite were green before
Checkpoint B began.

## 45. TDD Checkpoint B Evidence

The named crypto red commit failed only for absent operations. The
implementation then made exact-byte, wrong-key, and tamper tests green. The
first exact-head review added deterministic regressions for malformed UTF-16,
bounded Argon2 work factors, and destroyed recovery-key use.

## 46. Flutter Analysis And Tests

Formatting, analysis, seven focused AtlasVault suites, and the full Flutter
suite are required green before review and merge.

## 47. Android Build Evidence

The Android debug APK build is required before review. It verifies package
compatibility only and is not an Android privacy-runtime journey.

## 48. Windows Verification Status

The implementation host is macOS, so a Windows build is not run. Pure-Dart
tests provide compatibility evidence, not Windows runtime verification.

## 49. Go/No-Go

- Strict Dart AtlasVault models: implemented.
- Canonical Dart serialization: implemented.
- Payload vector parity: implemented.
- Encrypted-record crypto parity: implemented.
- Passphrase-wrap v1 parity: implemented.
- Recovery-wrap v2 parity: implemented.
- Encrypted-export parity: implemented.
- Flutter runtime integration: not implemented.
- Android secure-key storage: not implemented.
- Windows secure-key storage: not implemented.
- Encrypted Flutter saved searches: not implemented.
- Encrypted Flutter tracker records: not implemented.
- Plaintext migration: not implemented.
- Bidirectional production exchange: not implemented.
- Cross-platform privacy readiness: not claimed.

## 50. Deferred Work

Runtime vault ownership, atomic encrypted local-store I/O, platform secure key
storage, activation policy, explicit migration/removal, production
import/export, and cloud synchronization remain deferred.

## 51. Next Product Gate

Phase 2E-2 must implement the Android AtlasVault secure-key boundary and
encrypted local-store wiring while preventing new plaintext private-state
writes after vault activation. Plaintext migration, bidirectional exchange,
and Windows secure-key storage remain separately gated.
