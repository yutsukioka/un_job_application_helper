# Phase 2E-2 Android Secure Key and Encrypted Store

## 1. Purpose

Phase 2E-2 adds the Android device-local key and encrypted local-store
boundaries needed to use the pure-Dart AtlasVault compatibility layer. It also
gates Flutter private-state activation so new saved-search and tracker writes
cannot fall back to plaintext after activation.

## 2. Scope

The phase is limited to Android secure storage, explicit activation of an
existing AtlasVault, encrypted saved-search and tracker mutations, public-only
cache writes while active, and compatibility endpoint suppression. It adds no
activation UI, migration, cloud behavior, Apple changes, or Windows storage.

## 3. Phase 2E-1 Baseline

Phase 2E-1 supplied strict pure-Dart AtlasVault models, canonical JSON,
record encryption, passphrase-wrap v1 compatibility, recovery-wrap v2
compatibility, and encrypted-export compatibility. Phase 2E-2 reuses those
models and crypto operations without modifying their wire behavior.

## 4. Existing Android Boundary

Before this phase, Android exposed only the historical `atlas/storage`
`appFilesDir` operation. It had no reviewed Keystore boundary, wrapped
device-local vault key, no-backup AtlasVault location, or atomic encrypted
store operation.

## 5. Existing Plaintext Risks

The Flutter local cache can serialize saved searches and tracker records as
plaintext JSON. Those legacy stores remain in place for inactive mode, and
existing plaintext is treated as a migration requirement rather than silently
deleted or imported.

## 6. Existing Compatibility Endpoints

The compatibility API includes private saved-search and tracker operations.
They remain available in legacy inactive mode. An active AtlasVault session
must not call them for private reads or writes.

## 7. Original API-23 Blocker

The first Checkpoint A implementation explicitly set API 23. Flutter's
`integration_test` Android library declared API 24, causing manifest merging
to reject the application before real Android verification.

## 8. Flutter 3.44 API-24 Floor

Flutter 3.44.4 defines an effective Android minimum of API 24. The repository
baseline APK already inherited that floor before Phase 2E-2.

## 9. Integration-Test API-24 Requirement

The installed Flutter SDK's `integration_test` Android library uses the
Flutter API-24 minimum. Keeping integration testing while lowering the app to
API 23 is unsupported by the current toolchain.

## 10. API-24 Policy Correction

The application now states `minSdk = 24` directly. This supersedes the earlier
API-23 phase requirement and records the actual supported platform policy.

## 11. Android 6.0 Disposition

Android 6.0 and API 23 are not supported by this phase. Supporting API 23 would
require a separate legacy-toolchain product decision.

## 12. No Manifest Override

No `tools:overrideLibrary` rule is used. The integration-test manifest is not
changed, and the application does not claim compatibility below its tested
toolchain floor.

## 13. No Insecure Fallback

There is no software master-key fallback, device-identifier derivation,
plaintext key file, SharedPreferences key, or API-23 compatibility bypass.

## 14. Checkpoint A Boundary

Checkpoint A owns the dedicated Android MethodChannel, Keystore wrapping,
no-backup storage, strict Dart adapters, atomic create, CAS replacement,
mocked tests, APK verification, and real Android integration. It does not
activate private state.

## 15. Android Keystore Master Key

The Android Keystore holds one non-exportable AES master key used only to wrap
32-byte AtlasVault vault keys. The master key is never returned to Dart or
serialized.

## 16. Master Alias

The fixed alias is
`com.yutsukioka.jobagg.atlas.atlasvault.master.v1`.

## 17. Keystore Parameters

The master key is AES-256 with GCM, no padding, encrypt and decrypt purposes,
randomized encryption required, and no user-authentication requirement.
Keystore generates the encryption nonce, which is required when randomized
encryption is enforced.

## 18. Hardware-Backed Capability

Capabilities report whether the installed master key is inside secure
hardware. Hardware backing is observational and is not required or claimed.

## 19. StrongBox Capability

Capabilities separately report StrongBox backing when Android exposes that
security level. StrongBox is not required. The API-37 emulator used for
Checkpoint A reported false.

## 20. Vault-Bound AAD

Wrapped-key AAD is
`atlasvault-android-key-wrap-v1:com.yutsukioka.jobagg.atlas:<vaultID>`.
Changing the vault ID causes authenticated decryption to fail.

## 21. Wrapped-Key Envelope

The strict Android-local envelope contains only format, version, validated
vault ID, canonical Base64 nonce, and canonical Base64 ciphertext plus tag.
The nonce is 12 bytes and the ciphertext plus tag is 48 bytes.

## 22. No-Backup Key Path

Wrapped keys live under the application no-backup directory at
`atlasvault/v1/keys/<sha256(vaultID)>.json`. Paths are never returned through
the MethodChannel.

## 23. Create-Only Key Operation

Key creation rejects an existing envelope, writes atomically, reads the
envelope back, unwraps it, and compares the restored key in constant time.
Existence checks first enter `AtomicFile.openRead()` recovery so a committed
backup from an interrupted write cannot be mistaken for absence. Temporary
plaintext key copies are wiped best effort.

## 24. Key Load and Deletion

Load returns null only for absence and otherwise returns exactly 32 copied
bytes. Corrupt or invalidated keys fail with a fixed redacted error. Deletion
uses `AtomicFile.delete()` to remove only the requested base, new, or backup
envelope and does not delete the master key.

## 25. No-Backup Local-Store Path

Encrypted stores live under
`atlasvault/v1/vaults/<sha256(vaultID)>/atlasvault-local-store.json` in the
application no-backup directory. Managed path segments are checked with
`lstat`, and canonical containment prevents escapes.

## 26. Atomic Store Create

Creation requires a nonempty bounded canonical store for the requested vault,
rejects an existing destination, uses Android `AtomicFile`, flushes the file
descriptor, and verifies read-back bytes. Reads and create-only decisions use
`AtomicFile.openRead()` first so an interrupted prior write recovers its
committed state before absence is decided.

## 27. CAS Replace

Replacement requires an existing store and the lowercase SHA-256 digest of its
current bytes. A stale digest fails before the atomic write and is never
silently retried. The digest is calculated from the state recovered through
the same `AtomicFile` abstraction.

## 28. Store Read-Back

Every read is bounded to 128 MiB, rejects zero-length and non-regular files,
strictly validates the local-store envelope, requires canonical bytes, and
checks the metadata vault ID. Base, legacy backup, and new-file artifacts are
validated before `AtomicFile.openRead()` restores interrupted state.

## 29. Path Containment

Vault IDs are validated before hashing or file access. The implementation
rejects direct symbolic links and non-regular managed paths, while accepting
Android's trusted canonical mapping of app-data ancestors.

## 30. Size Limit

Wrapped-key envelopes are bounded to 16 KiB. Encrypted local stores are
bounded to 128 MiB in both Kotlin and Dart.

## 31. Real Android Storage Integration

Checkpoint A runs a fake-data journey through the real MethodChannel,
Android Keystore, wrapped-key file, `AtomicFile` store creation, CAS
replacement, read-back, duplicate rejection, and cleanup.

## 32. Checkpoint B Boundary

Checkpoint B begins only after Checkpoint A is green and committed. It owns
explicit activation, encrypted private mutations, cache and endpoint policy,
deactivation, and the real private-state integration journey.

## 33. Explicit Activation

Construction performs no I/O. A caller must explicitly activate a validated
existing vault ID; this phase adds no automatic or UI-driven activation.

## 34. Migration-Required Preflight

Activation first checks in-memory and persisted legacy private state. If
plaintext saved searches or tracker records exist, activation returns a fixed
`migrationRequired` result before any key or encrypted-store operation.

## 35. No Plaintext Deletion

The migration-required path deletes nothing and leaves all legacy data
unchanged. Phase 2E-2 does not silently migrate or establish dual authority.

## 36. Saved-Search Mapping

Active Flutter saved searches map only to encrypted AtlasVault
`saved_search` payloads. Runtime projections expose app-domain values, not
record IDs, revisions, key IDs, ciphertext, or paths.

## 37. Tracker/Saved-Job Mapping

Active tracker records map to encrypted AtlasVault `saved_job` payloads.
Other encrypted private families remain preserved in the store but unexposed
by this runtime.

## 38. Record Identity

The runtime keeps record ID, current revision, and key ID only in its internal
mapping. Updates preserve record ID and key ID and use the prior revision as
parent revision.

## 39. Encrypted Create/Update

Each active save becomes one encrypted record create or update using the
Phase 2E-1 record crypto. Timestamps, UUIDs, and nonces are injectable for
deterministic tests; production nonces use cryptographically secure
randomness. A stale runtime rehydrates the current store and rejects a
concurrent logical-name or job-key create before UUID generation, encryption,
or CAS replacement, so duplicate private records are never committed.

## 40. Unchanged-Record Preservation

Mutation replaces only the targeted encrypted record. Every unrelated
encrypted record and unsupported private family remains byte-identical and in
store order.

## 41. Runtime Key Lifetime

The active vault key exists only in the runtime session. Deactivation cancels
and drains mutation work, wipes the mutable key buffer best effort, clears
metadata mappings, and clears projected private lists.

## 42. Public-Only Cache

While AtlasVault is active, controller cache writes explicitly transform
snapshots to remove saved searches and tracker records while preserving every
committed public field. The controller separately retains the last committed
public-search request, so a private saved-search draft cannot enter the public
cache while a debounced public search is still pending.

## 43. Hard Plaintext Guard

The local-cache store receives an injected active-protection policy. If active
and given a snapshot containing private state, it throws a fixed error before
creating a temporary file or modifying the destination.

## 44. Compatibility Endpoint Suppression

While active, saved-search and tracker private reads and writes make zero
compatibility endpoint calls. Encrypted failures do not fall back to those
endpoints. A controller authority generation fences activation against
compatibility mutations: results already in flight before activation are
discarded, and no new compatibility mutation starts while activation is in
progress.

## 45. Legacy Inactive Compatibility

Inactive mode keeps historical cache and compatibility behavior unchanged.
Public search, details, health, sources, and updates remain available in both
modes.

## 46. Deactivation

Explicit deactivation drains active mutation work, wipes key material best
effort, clears mappings and private projections, leaves wrapped keys and the
encrypted store intact, and returns to inactive mode without loading legacy
plaintext.

## 47. Real Android Private-State Integration

Checkpoint B provisions a fake vault, activates it, commits fake saved-search
and tracker values, proves raw encrypted-store and public-cache bytes omit
their sentinels, verifies decrypted records, confirms zero private
compatibility calls, deactivates, and cleans up.

## 48. Public Endpoint Continuity

AtlasVault activation does not disable public search, job detail, health,
source, or update operations.

## 49. No Activation UI

This phase adds no screen, navigation route, automatic prompt, or app-start
activation.

## 50. No Migration

Legacy plaintext migration is deferred. The only Phase 2E-2 behavior for
detected plaintext is a fixed migration-required result.

## 51. No iOS Interoperability

Bidirectional production iOS-Flutter encrypted exchange is not implemented in
this phase.

## 52. No Windows Storage

Windows DPAPI or Credential Manager storage is not implemented in this phase.

## 53. TDD Checkpoint A Evidence

The preserved red commit established missing Android storage protocols and
integration behavior. The implementation gate includes mocked Dart tests,
Flutter analysis, APK and Gradle builds, lint, manifest minSdk verification,
and a real Android storage integration.

## 54. TDD Checkpoint B Evidence

Checkpoint B preserves a separate red commit before runtime wiring. Its green
gate covers activation, migration preflight, encrypted mutations, endpoint
suppression, cache guarding, private-draft/public-request separation,
deactivation, and real Android integration. Exact-head Codex review added
deterministic regressions for interrupted `AtomicFile` recovery, stale-runtime
logical creates, and in-flight compatibility saves crossing activation.

## 55. Verification

Final verification includes Dart formatting, Flutter analysis, the full
Flutter suite, Android APK build and lint, both Android integrations,
Phase 2E-1 Dart vectors, Python vectors, Swift vectors, source guards, exact
scope, protected paths, and artifact scans.

## 56. Go/No-Go

- Android API-24 floor: implemented.
- API-23 support: not implemented.
- Insecure override: absent.
- Keystore boundary: implemented.
- Wrapped key: implemented.
- Encrypted local store: implemented.
- Atomic create/replace: implemented.
- Explicit activation: implemented.
- `migrationRequired` gate: implemented.
- Encrypted saved-search writes: implemented.
- Encrypted tracker writes: implemented.
- Active plaintext cache writes: blocked.
- Private compatibility endpoints while active: blocked.
- Automatic activation: not implemented.
- User-facing activation: not implemented.
- Migration: not implemented.
- iOS-Flutter exchange: not implemented.
- Windows storage: not implemented.
- Production cross-platform privacy readiness: not claimed.

## 57. Deferred Work

Deferred work includes rollback-capable plaintext migration, bidirectional
iOS-Flutter exchange, Windows secure storage, activation UI, recovery UI, and
cloud or linked-device behavior.

## 58. Next Product Gate

After Phase 2E-2 merges, Phase 2E-3 must implement an explicit,
rollback-capable migration of Flutter plaintext saved searches and tracker
records into the Android AtlasVault store. It must validate encrypted
read-back before deleting plaintext, preserve interruption recovery, and
prohibit silent dual plaintext/encrypted authority. Bidirectional
iOS-Flutter exchange and Windows secure-key storage remain separately gated.
