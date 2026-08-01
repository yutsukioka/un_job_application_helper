# Phase 2E-5 Windows Secure Key and Encrypted Store

## 1. Purpose

Establish a Windows device-local AtlasVault boundary for explicitly provisioned
vaults while reusing the reviewed Dart record formats, cryptography, and
private-state runtime.

## 2. Scope

This phase adds current-user DPAPI key protection, native atomic encrypted-store
I/O, Windows Dart adapters, and explicit production runtime assembly. It adds no
migration, selected-vault state, import UI, or automatic activation.

## 3. Phase 2E-4 Baseline

Phase 2E-4 completed Apple-Flutter encrypted artifact interoperability. Android
already had secure key storage, encrypted local storage, migration, and explicit
activation; Windows did not.

## 4. Current Windows Runner

The baseline host was Flutter's generated C++ runner. Phase 2E-5 keeps that
runner and registers one runner-owned AtlasVault bridge.

## 5. Current Windows Plugin State

No generated secure-storage plugin is used or modified. The dedicated channel
is registered directly after generated plugins.

## 6. Existing Windows Plaintext Risk

The historical cache and compatibility endpoints may contain saved searches and
tracker records while AtlasVault is inactive. Explicit activation first drains
admitted compatibility/cache writes, then queries the compatibility authority
and persisted cache before any DPAPI or encrypted-store operation. Detected
plaintext returns `migrationRequired` without migration or deletion.

## 7. Phase Boundary

Windows at-rest protection begins only after a caller explicitly activates a
validated existing vault. Inactive legacy behavior remains unchanged.

## 8. Checkpoint A Boundary

Checkpoint A owns the Windows channel, DPAPI key envelope, Local AppData paths,
cross-process locking, and native atomic local-store operations. It does not
change controller authority.

## 9. Supported Windows Versions

Support follows the active Flutter SDK Windows policy and targets Windows 10
and Windows 11.

## 10. Windows 10/11 Policy

Windows 7 and Windows 8 are not added. Architecture support follows Flutter and
the discovered Visual Studio toolchain.

## 11. Parallels Verification Environment

Native evidence uses a Windows-local NTFS clone in Windows 11 on Parallels. The
observed target is x64 running under ARM64 emulation, not Arm64-native or a
physical Windows PC.

## 12. DPAPI Current-User Scope

`CryptProtectData` and `CryptUnprotectData` run without machine-wide scope. The
protected value is normally tied to the current Windows user's credentials and
computer.

## 13. DPAPI Roaming-Profile Limitation

Roaming-profile behavior can affect portability. Strict physical-machine
binding is not claimed.

## 14. No Machine-Wide DPAPI

`CRYPTPROTECT_LOCAL_MACHINE` is absent. The bridge uses
`CRYPTPROTECT_UI_FORBIDDEN` and no prompt structure.

## 15. No TPM Guarantee

DPAPI availability does not establish TPM or hardware-backed protection. The
capability response reports `hardware_backed_guaranteed: false`. Availability
fields are derived from nonsecret current-user DPAPI round-trip, Local AppData,
cross-process-lock, and atomic create/replace probes rather than fixed literals.

## 16. Optional Entropy

Optional entropy is SHA-256 over the fixed AtlasVault Windows domain,
organization, product, and validated vault ID. It never crosses into Dart.

## 17. DPAPI Integrity Verification

After unprotect, the bridge requires exactly 32 bytes and constant-time compares
their SHA-256 with the local envelope hash before returning a copied key.

## 18. Windows-Local Key Envelope

The binary `AVWKEY01` envelope contains a little-endian version, lengths, key
hash, validated vault ID, and DPAPI blob. It is not a shared wire format.

## 19. Envelope Strictness

Magic, version, vault ID, length bounds, exact total length, nonempty protected
blob, and absence of trailing bytes are mandatory.

## 20. Local AppData Root

Secure resources resolve through `FOLDERID_LocalAppData` under
`UNApplications\AtlasVault\v1`.

## 21. Hashed Vault Paths

Key, vault, and lock names use lowercase SHA-256 of the already validated vault
ID. Secure paths are never returned to Dart.

## 22. Reparse-Point Protection

Application-owned path segments and destination files reject reparse points,
non-directory parents, and non-regular destinations. Inspection uses
`FILE_FLAG_OPEN_REPARSE_POINT` where applicable.

## 23. Per-Vault Cross-Process Lock

Key and store mutations acquire an exclusive byte-range lock through
`LockFileEx` with `LOCKFILE_FAIL_IMMEDIATELY`. Contention therefore returns a
fixed failure without blocking the Windows runner thread. `UnlockFileEx`
releases an acquired lock and handle closure provides crash-safe release.

## 24. Create-Only Key Semantics

The wrapped key is written to a same-directory random temporary file with
`CREATE_NEW`, flushed, and moved without replacement. Collision fails fixed.

## 25. Key Load

Load strictly parses the local envelope, checks the requested vault ID,
unprotects with matching entropy, verifies length and hash, and returns only a
copied 32-byte key.

## 26. Key Tamper Behavior

Malformed envelopes, changed protected bytes, wrong entropy, and failed hashes
return one fixed redacted storage failure.

## 27. Key Deletion

Deletion is exact, per-vault, lock-protected, and idempotent for absence. It does
not delete the encrypted store.

## 28. Encrypted Local-Store Path

Only canonical `atlasvault-local-store` version 1 bytes are stored in the
per-vault Local AppData directory.

## 29. Store Size Bound

Native and Dart boundaries reject empty stores and stores larger than 128 MiB.

## 30. Atomic Store Create

Create writes and flushes a same-directory random temporary file, closes it,
and moves without replacement before exact read-back verification.

## 31. CAS Replacement

Replace validates the current SHA-256 under the per-vault lock. A stale digest
performs no write; a matching digest uses replace-existing plus write-through.

## 32. Store Read-Back

Create and replace read the committed destination and require exact byte
equality. This claims flushed contents and atomic replacement, not guarantees
beyond Windows and the underlying volume.

## 33. Flutter Windows Channel

The dedicated channel is `atlas/vault_windows` with only capabilities, key
create/load/contains/delete, and store read/create/replace/delete methods. Its
capabilities call performs real nonsecret boundary probes and returns no path,
identity, protected blob, or vault identifier.

## 34. Windows Dart Adapter

The adapters validate vault IDs, key lengths, canonical stores, metadata vault
identity, size bounds, and CAS digests before platform calls. Platform details
are mapped to fixed exceptions.

## 35. Public Windows Barrel

`atlas_vault_windows.dart` exports reviewed Windows adapters and required
generic runtime interfaces. It exports no paths, native envelope helpers, test
fakes, or MethodChannel internals.

## 36. Checkpoint A Cross-Process Test

Separate prepare and verify processes prove DPAPI persistence, create-only
semantics, exact store reload, valid and stale CAS behavior, wrong-vault
isolation, tamper rejection, and cleanup using fake data.

## 37. Checkpoint B Boundary

Checkpoint B adds only Windows production assembly and verification of the
existing controller/runtime authority rules.

## 38. Generic Private Runtime Reuse

Windows constructs `AtlasVaultPrivateStateRuntime` with the Windows key and
store adapters. No Windows record format or duplicate mutation engine exists.

## 39. Explicit Activation

Activation remains `activateExistingAtlasVault(vaultId)`. Construction,
startup, public cache loading, connection testing, and public search do not
activate a vault.

## 40. Migration-Required Preflight

In-memory or persisted plaintext private state returns `migrationRequired`
before any Windows key or encrypted-store call. The production Windows assembly
also injects the existing compatibility migration source. Activation admission
drains a retained compatibility mutation before reading that source, preventing
a just-committed compatibility record from being hidden by encrypted authority.
Activation captures the normalized compatibility authority and revalidates it
after every admission or persistence await; a concurrent server selection
invalidates activation before DPAPI or encrypted-store access.
Existing plaintext remains untouched.

## 41. Encrypted Saved-Search Writes

After activation, saved searches mutate the canonical encrypted store through
the generic runtime, verify read-back, and publish only committed state.

## 42. Encrypted Tracker Writes

Tracker records follow the same encrypted CAS transaction and preserve
unrelated encrypted records.

## 43. Compatibility Endpoint Suppression

Active saved-search and tracker reads and writes never use compatibility
private endpoints. There is no fallback after encrypted failure.

## 44. Public-Only Cache

Active cache writes call the existing private-free snapshot boundary. Public
search, source, update, detail, and health state remain available.

## 45. Hard Plaintext-Write Guard

The cache store receives the controller's live private-protection callback and
rejects any accidental active snapshot that still carries private records.

## 46. Deactivation

Deactivation invalidates authority, drains retained mutation work, best-effort
wipes the runtime key, clears private mappings and projections, and leaves the
wrapped key and encrypted store intact.

## 47. Cross-Process Private-State Restoration

Separate Windows processes explicitly activate the same fake vault, restore
encrypted searches and tracker records, commit an update, verify private-free
cache bytes, deactivate, and clean up.

## 48. Windows App Smoke

The normal Release executable is launched without automatic activation or test
vault injection and must remain alive for a bounded interval.

## 49. No Activation UI

This phase adds no Windows creation, import, recovery, activation, or migration
screen.

## 50. No Selected-Vault Marker

No durable Windows selected-vault authority is created. Callers supply a
validated vault ID explicitly.

## 51. No Plaintext Migration

Existing Windows plaintext records are detected and preserved for a separately
reviewed migration phase.

## 52. No Windows Import/Export

The shared encrypted formats remain available in Dart, but this phase adds no
Windows file transport, picker, import, or export journey.

## 53. No Cloud or Linking

Cloud synchronization, linked devices, patches, and key rotation remain
outside this phase.

## 54. TDD Checkpoint A Evidence

The red commit proved absent Windows storage boundaries. Mocked/source tests,
Debug and Release builds, fresh-process DPAPI/store tests, external inspection,
tamper rejection, and cleanup then passed.

Exact-head review added a deterministic source regression requiring capability
values to come from real DPAPI, Local AppData, lock, and atomic-replace probes.
The next review cycle requires nonblocking exclusive lock acquisition so a
contending process cannot indefinitely block the runner thread.

## 55. TDD Checkpoint B Evidence

The red commit produced exactly three missing-production-assembly failures
while 71 assertions passed. The Windows assembly made the focused suite green
without changing generic runtime behavior.

Exact-head review added gated regressions proving activation waits for an
in-flight compatibility mutation, inspects authoritative compatibility state,
returns `migrationRequired`, and makes zero Windows storage calls.
The next cycle binds this admission to the captured normalized server authority
and rejects a concurrent `saveAndReload` authority change before persistence
activation.

## 56. Verification

Required gates include formatting, analysis, focused and full Flutter tests,
Windows Debug/Release builds, both staged Windows integrations, normal app
smoke, Android and earlier-phase regressions, Python/Swift vectors, source
guards, exact scope, CI, reviews, and artifact checks.

## 57. Go/No-Go

- Windows current-user DPAPI boundary: implemented.
- Machine-wide DPAPI: intentionally absent.
- TPM-backed guarantee: not claimed.
- Windows wrapped vault-key storage: implemented.
- Local AppData secure root: implemented.
- Windows encrypted local store: implemented.
- Atomic create and CAS replace: implemented.
- Explicit Windows activation: implemented.
- Encrypted saved-search writes: implemented.
- Encrypted tracker writes: implemented.
- Active plaintext cache writes: blocked.
- Active private compatibility endpoints: blocked.
- Automatic activation: not implemented.
- Selected-vault storage: not implemented.
- Windows plaintext migration: not implemented.
- Windows encrypted import/export: not implemented.
- Production cross-platform privacy readiness: not claimed.

## 58. Deferred Work

Windows plaintext migration, selected-vault state, encrypted file transport,
linked-device synchronization, patch exchange, and key rotation remain gated.

## 59. Next Product Gate

Phase 2E-6 is an explicit rollback-capable Windows plaintext private-state
migration with a DPAPI-protected journal, verified encrypted read-back,
interruption recovery, and no silent dual authority.
