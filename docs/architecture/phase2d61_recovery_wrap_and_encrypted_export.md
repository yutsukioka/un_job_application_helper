# Phase 2D-61: Recovery Wrap And Encrypted Export

## Purpose

Phase 2D-61 makes a Phase 2D-60 device-local vault recovery-prepared. It adds
a generated recovery key, a vault-bound recovery wrap, and a locally verified
encrypted export before any private-data authoring or rendering is enabled.

## Scope

This phase changes only the reviewed 23-file contract, Python reference, Swift
crypto/export/setup, production composition, production root, and test scope.
It does not change app entry, the process owner, production host, unlock
coordinator, local-vault creation, navigation, private models, or Xcode project
configuration.

## Reconstructed Baseline

Phase 2D-60 was reconstructed from Git and GitHub before implementation. Its
device-local creation, explicit local-key unlock, stop, and relaunch journey
was green. The full Python and Swift suites and both discovered generic iOS
Simulator builds passed before the Phase 2D-61 red checkpoint.

The merge-stability audit found no permanent current-branch or current-blob
assertion that rejects the Phase 2D-61 allowlist.

## Existing V1 Compatibility

The existing `WrappedKey` and `AtlasVaultWrappedKeyEnvelope` remain the
passphrase-wrap v1 models. V1 keeps Argon2id, AES-256-GCM, its 12-byte nonce,
its 48-byte ciphertext-plus-tag, and its historical AAD without `vault_id`.
Phase 2D-61 does not reinterpret, rewrite, or enable production use of that
decode-only Swift profile.

Top-level vault metadata remains `atlas-vault` version 1. A versioned key-wrap
union allows v1 passphrase and v2 recovery wraps to coexist. Swift dispatches
v1 only when `wrap_version` is completely absent and the top-level wrap has
exactly `id`, `type`, `kdf`, `nonce`, and `ciphertext`. Explicit null,
explicit versions, and unknown fields fail without changing v1 crypto,
encoding, or AAD.

## Recovery-Key Format

Production generates exactly 32 random bytes with `SecRandomCopyBytes` in
Swift and `secrets.token_bytes(32)` in Python. The key is independent of UUIDs,
timestamps, devices, accounts, vault IDs, and the vault key.

The transcription checksum is the first five bytes of:

```text
SHA256(UTF8("atlasvault-recovery-key-v1:") || recovery_key)
```

The key and checksum encode as unpadded RFC 4648 Base32. Sixty symbols are
rendered as 15 groups under the `AVRK1` prefix. Parsing accepts ASCII case and
ASCII group spacing, rejects Unicode look-alikes and ambiguous digits, and
uses a constant-time checksum comparison. The checksum is an error-detection
value, not an authentication tag.

## Recovery Wrap V2

The fixed wrap identity is `primary-recovery-v2`. The type is `recovery_key`
and the wrap version is 2. Its KDF is HKDF-SHA256 with a random 32-byte salt,
the fixed info `atlas-vault-recovery-wrap-v2`, and a 32-byte output. This KDF
profile is only for generated high-entropy recovery input and is not a
passphrase KDF.

AES-256-GCM uses a random 12-byte nonce and produces 48 bytes for a wrapped
32-byte vault key plus tag. Strict models reject missing, unknown, malformed,
or noncanonical fields.

## Vault-Bound AAD

The sorted, compact UTF-8 JSON AAD binds:

- `atlas-vault-key-wrap` format and version 2;
- validated `vault_id`;
- fixed wrap ID and recovery type;
- AES-256-GCM;
- HKDF-SHA256 algorithm, salt, and info.

Changing the vault, salt, nonce, ciphertext, wrap identity, type, version,
AEAD, or KDF configuration makes verification fail. V1 AAD remains unchanged.

## Canonical Encrypted Export

The Swift envelope matches Python `atlasvault-export` version 1:

- lowercase opaque export UUID;
- strict UTC timestamp with second precision;
- complete validated vault metadata;
- ordered encrypted records and tombstones.

Python direct construction and untrusted decode apply the same metadata
boundary: `export_id` is a canonical lowercase hyphenated UUID and
`created_at` is an exact valid UTC-seconds timestamp. Explicit empty custom
values fail rather than selecting generated defaults. Export v1 decoding
requires exactly `format`, `version`, `export_id`, `created_at`,
`vault_metadata`, and `records`; missing and unknown keys are rejected.
Export and vault-metadata versions are strict JSON integers: Boolean and
floating-point values are rejected by direct construction and untrusted
decode, while the valid integer version 1 remains unchanged.

Sorted compact UTF-8 JSON excludes the local store ID, path, selection
registry, Keychain state, raw vault key, raw recovery key, recovery text,
passphrase, and plaintext payloads. The fixed filename is
`AtlasVault-Encrypted-Backup.atlasvault`.

## Local Verification Boundary

Before export bytes reach `FileDocument`, the coordinator:

1. encodes canonical export bytes;
2. strictly decodes the bytes;
3. validates format, version, metadata, and active vault identity;
4. unwraps with the entered recovery key;
5. compares the recovered and local vault keys in constant time;
6. hydrates local encrypted records with a temporary unlocked session;
7. hydrates the strictly decoded export records a second time;
8. discards hydration results and best-effort wipes temporary key data.

Corrupt encrypted records prevent export readiness. Hydrated private state is
never published to SwiftUI or presentation state.

## Pre-Confirmation State

Presenting setup performs no I/O. Explicit generation verifies authorization,
loads the selected encrypted vault, and creates recovery material in memory.
The generated code crosses the coordinator boundary through a one-shot handle
and is held only by scene-local SwiftUI state.

Before correct full-code re-entry, setup writes no journal, mutates no store,
creates no export, and changes neither selection nor the local Keychain key.
Pausing while generation is active or while confirmation is pending drains
coordinator cleanup, discards the ephemeral prepared material, and returns to
ready. Because no journal or wrap exists yet, the pause is not presented as
resumable. A fresh recovery key requires another explicit generate action.

## Setup Journal

After correct full re-entry, a device-only Keychain journal is written under:

- service `com.atlasvault.recovery-export`;
- account `pending-v2`;
- accessibility `afterFirstUnlockThisDeviceOnly`.

The strict journal contains format/version, opaque vault/store/export IDs,
wrap ID, the encrypted recovery wrap, and one UTC timestamp. It contains no
raw recovery key, recovery text, vault key, passphrase, path, destination URL,
plaintext record, or hydrated state.

## Atomic Metadata Commit

The journal is saved before metadata mutation. The coordinator adds exactly
one matching recovery wrap, preserves encrypted records and unrelated wraps,
and saves through the reviewed persistence coordinator with `overwrite: true`.
Only confirmed atomic durability proceeds.

The store is loaded back and the exact wrap is verified before export
construction. `committedDurabilityUnconfirmed` leaves the journal and requires
explicit resume.

## Export Completion

File save success clears the setup journal last and marks setup complete. File
save cancellation or failure retains the journal and returns to explicit
resume. No destination URL is persisted.

## Interrupted Resume

A journal never stores the raw recovery key, so resume asks for the separately
saved key. The coordinator verifies selected vault, store ID, journal wrap,
local Keychain vault key, and encrypted records. It commits a missing exact
wrap when safe or reuses the already committed exact wrap.

The journal's export ID and timestamp remain stable during interrupted resume.
Wrong recovery input returns one fixed retryable failure and changes nothing.
Repeated resume does not duplicate the wrap.

Completed setup has no journal. Re-export still requires entry of the saved
recovery key and verifies the committed wrap before producing new export
metadata.

## Explicit Pending Reset

`Restart Recovery Setup` is available only for an unfinished journal and
requires a second confirmation. While locally unlocked, reset removes only
the exact journal-identified wrap, preserves encrypted records and unrelated
wraps, atomically commits, reads the store back, and clears the journal last.

Reset never deletes the vault, local key, selection, or records. A mismatch,
unconfirmed durability, or failed read-back retains the journal and fails
closed. A completed wrap without a journal is not reset or rotated here.

## Authorization And Lifecycle

Every operation requires:

- production flow at `unlockedTransition`;
- runtime status `unlocked`;
- selected vault;
- active/protected-data-safe lifecycle;
- no terminal process stop.

Authorization is rechecked before persistent effects. Backgrounding,
inactivity, and protected-data loss cancel and drain setup, wipe coordinator
secret buffers, and dismiss secret-bearing state while retaining a non-secret
journal when one exists. Unsafe lifecycle dismissal during pre-confirmation
also clears scene-local recovery text and remains hidden even if generation
finishes late. Journal-backed pauses remain explicitly resumable. Termination
and harness stop make the coordinator terminal and await its drain.

## Presentation

The `MainActor` presentation owner publishes only fixed private-free states.
Recovery code, entered key, and encrypted document remain local SwiftUI state.
The UI requires explicit generation, a saved-code acknowledgement, and full
secure re-entry. Resume requires the saved key. Reset requires confirmation.

There is no clipboard integration and no screenshot-prevention claim. The UI
warns that the encrypted backup cannot yet be imported and that loss of both
local and recovery material is unrecoverable.

## Production Composition

One production harness creates one recovery coordinator and presentation
owner using the same runtime services, Keychain client, selection registry,
root provider, per-vault services, host flow, and lifecycle authority already
reviewed for Phase 2D-60.

Construction performs no setup I/O. Multiple roots share the same operation
authority while scene-local claims prevent duplicate sheets. Harness stop
cancels and drains recovery work. Existing harness initializers remain valid
without a recovery context.

## Production Root

The optional recovery context coexists with the optional creation context.
Compatibility initializers construct no recovery owner and display no recovery
action. With context, the root offers `Recovery & Encrypted Export` only at
`unlockedTransition` and never presents it automatically.

The root owns no coordinator, Keychain client, filesystem client, runtime, or
private state. Existing locked, no-vault, creation, public search, and unlock
flows remain unchanged.

## Production Unlock Capability

`AtlasVaultUnlockCapabilities.currentProduction.availableMethods` remains
exactly `[.localKey]`. The recovery wrap is locally proven for preparation and
export, but no recovery provider is supplied to the normal unlock coordinator.

## Secret Lifetime

Mutable recovery and recovered-vault-key buffers are best-effort wiped after
use. Swift cannot promise universal memory zeroization, so no stronger claim
is made. Secrets are absent from descriptions, errors, logs, observable
presentation state, the journal, store metadata, and export bytes.

## Cross-Language Vectors

The fake test-only v2 vector fixes recovery bytes, checksum text, vault key,
salt, nonce, AAD object and bytes, wrap, metadata, export object, canonical
export bytes, and SHA-256. Python and Swift independently recompute and verify
the same values.

The existing passphrase v1 vector remains green. Tamper tests cover vault
binding, wrong key, valid-length salt/nonce/ciphertext changes, strict fields,
canonical Base64, and duplicate recovery-wrap IDs.

Recovery-wrap validation uses the fixed human-facing term `key-wrap`.
Serialized contract fields, including `key_wraps`, are unchanged.

## TDD Evidence

The red checkpoint introduced missing-type and missing-integration tests before
production code. Green coverage includes codec, crypto, vectors, strict export,
transaction ordering, durability, authorization, resume, reset read-back,
private-free presentation, shared composition, root integration, and two real
temporary-root end-to-end journeys.

Review-fix cycle 4 added deterministic red evidence for Python accepting
noncanonical export identifiers/timestamps and Swift accepting
`wrap_version: null` or an unknown v1 field. After the strict-decoder repair,
the focused Python vector suites passed 97 tests, the complete Python suite
passed 162 tests, the strict Swift wrap suite passed 25 tests, and the complete
Swift suite passed 1,129 tests. Both discovered generic iOS Simulator test
builds passed. The existing canonical vector bytes and SHA-256 are unchanged,
and the final reviewed repository scope is exactly 23 files.

Review-fix cycle 5 adds strict Boolean/float version rejection for export and
vault metadata, fixes recovery key-wrap validation wording without changing
serialized fields, and distinguishes ephemeral pre-confirmation pauses from
journal-backed resumable pauses. The overall repository scope remains exactly
23 files. Its focused Python compatibility suites pass 111 tests, the complete
Python suite passes 176 tests, the focused recovery/export view suite passes
15 tests, and the complete Swift suite passes 1,132 tests. Both discovered
generic iOS Simulator test builds pass.

## End-To-End Evidence

The deterministic journey uses the Phase 2D-60 production-like factory, an
in-memory Keychain, real atomic filesystem and runtime boundaries, and a
temporary Application Support root. It:

1. creates and explicitly local-key unlocks a fresh vault;
2. generates and confirms recovery material;
3. commits one v2 wrap;
4. writes and parses a verified encrypted export;
5. proves journal completion and secret exclusion;
6. stops and relaunches;
7. explicitly local-key unlocks again;
8. rejects a wrong recovery key;
9. re-exports with the saved key.

A second journey stops after wrap commit, relaunches, and explicitly resets the
pending setup while preserving key, selection, records, and vault identity.

## Go/No-Go

- v1 passphrase-wrap compatibility: preserved;
- generated 256-bit recovery key: implemented;
- vault-bound recovery wrap v2: implemented;
- strict Python/Swift vectors: implemented;
- atomic wrap metadata update: implemented;
- locally verified encrypted export: implemented;
- interrupted resume and pending reset: implemented;
- shared private-free setup UI: implemented;
- production recovery unlock: not implemented;
- encrypted import/clean-install restore: not implemented;
- private rendering or authoring: not implemented;
- cloud sync and migration: not implemented;
- production readiness: not claimed.

## Deferred Work

Phase 2D-61 intentionally defers export import, clean-install restoration,
ordinary recovery-key unlock, recovery material rotation, vault revocation,
private-data authoring/rendering, cloud sync, and migration.

## Next Product Gate

Phase 2D-62 must implement encrypted export import, clean-install restoration,
and production recovery-key unlock using the reviewed vault-bound recovery
wrap, while keeping private-data rendering and authoring disabled.
