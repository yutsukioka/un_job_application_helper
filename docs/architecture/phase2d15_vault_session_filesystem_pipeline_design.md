# Phase 2D-15 AtlasVault Session Filesystem Pipeline Design

Status: design only. This phase defines how future Apple runtime code should
coordinate unlocked vault sessions with the local encrypted-store filesystem
pipeline. It does not add runtime code, UI hydration, file writes, Keychain
wiring changes, migration execution, cloud sync, device onboarding, key
rotation, or cleanup of legacy plaintext snapshots.

## 1. Purpose

Phase 2D-15 connects the already-isolated design pieces at the architecture
level before implementation. Future runtime code will need to coordinate vault
unlock state, encrypted local-store paths, directory preparation, local-store
read/write, record crypto, Keychain-backed key material, and the public
snapshot/private vault boundary.

The goal of this phase is to define that coordinator boundary without wiring it
into `AtlasSearchViewModel`, SwiftUI screens, `AtlasLocalCache`, or app startup.

## 2. Scope

In scope:

- design for future session and persistence coordination;
- unlock/load and save/write sequencing;
- locked-state behavior;
- error-state taxonomy;
- atomic-write and crash-safety considerations;
- public snapshot and migration boundaries;
- future implementation tests.

Out of scope:

- Swift runtime coordinator code;
- UI implementation or SwiftUI hydration;
- file reads or writes;
- Keychain wiring changes;
- LocalAuthentication or biometric prompts;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## 3. Existing Building Blocks

`AtlasVaultKeyStore` defines the key-store protocol used by unlock/session
logic. The current testable unlock service can create an `AtlasVaultSession`
from caller-provided or stored 32-byte vault keys and clear it on lock.

`AtlasKeychainVaultKeyStore` is the SecItem-backed implementation behind the
protocol. It stores vault-key material by random `vaultID`, validates key
length, and remains unwired from runtime app behavior.

`AtlasVaultRecordCrypto` derives per-record keys from the vault key, vault ID,
and record ID. It encrypts and decrypts record payload bytes while keeping
record type and private payload fields inside ciphertext.

`AtlasVaultLocalStoreIO` encodes, decodes, reads, and writes
`atlasvault-local-store` envelopes at explicit caller-provided URLs. It preserves
encrypted record envelopes and does not decrypt records or choose paths.

`AtlasVaultPathLocator` computes
`<root>/Atlas/Vaults/<vaultID>/atlasvault-local-store.json` from an injected
root and validated non-semantic vault ID. It does not write files or discover
Application Support.

`AtlasVaultDirectoryPreparer` prepares only parent directories for a
caller-provided local-store URL under a caller-provided root. It rejects
outside-root paths, avoids deletion, and does not create the final store file.

`AtlasPublicLocalSnapshot` and `AtlasLocalCache` now represent the public cache
split. Public cache data can remain available while private vault state is
locked.

## 4. Proposed Coordinator Boundary

Future runtime work should add a small coordinator layer rather than pushing
vault logic into view models or cache helpers. Candidate names:

- `AtlasVaultSessionStore`;
- `AtlasVaultPersistenceCoordinator`;
- `AtlasVaultRuntimeEnvironment`.

The exact names can change, but responsibilities should remain separated.

`AtlasVaultSession` should hold the random `vaultID`, unlocked vault key, and
decrypted private records in memory only. It should clear key material and
private payloads on lock, failed unlock reset, corruption, account switch, or app
termination.

`AtlasVaultPersistenceCoordinator` should receive injected dependencies:

- an `AtlasVaultKeyStore`;
- an `AtlasVaultPathLocator`;
- an `AtlasVaultDirectoryPreparer`;
- local-store IO functions;
- record crypto functions;
- a caller-provided root provider selected outside the coordinator.

Responsibilities:

- use the locator to compute the encrypted local-store URL for a random vault ID;
- read the local-store envelope when a vault file exists;
- decrypt records only after a valid vault key is available;
- encrypt records before saving;
- call the directory preparer before writes;
- call local-store IO to read and write encrypted envelopes;
- never write decrypted payloads to disk;
- never mutate public snapshot files with private state;
- return non-sensitive state and errors to runtime callers.

The coordinator must not own UI text, prompt flows, LocalAuthentication, or
legacy migration cleanup.

## 5. Unlock And Load Flow

Future load sequence:

1. The app has a current random `vaultID` from reviewed configuration or account
   state.
2. Unlock obtains the 32-byte vault key from passphrase/recovery unwrapping or
   `AtlasVaultKeyStore`.
3. A session is created with `vaultID` and vault key.
4. The path locator computes the local-store URL from the injected root.
5. If the local-store file is present, `AtlasVaultLocalStoreIO` reads and
   validates the encrypted envelope.
6. `AtlasVaultRecordCrypto` decrypts each encrypted record into in-memory
   private models.
7. Public cache state remains independent and can continue serving public search
   results and job details.

Unlock must not hydrate partial plaintext after wrong-key, corrupt-store, or
unsupported-version failures. It should either produce a complete unlocked
private state or a non-sensitive failure state.

## 6. Save And Write Flow

Future save sequence:

1. Private in-memory records are modified while the vault is unlocked.
2. The coordinator serializes private payload models into canonical plaintext
   bytes in memory.
3. `AtlasVaultRecordCrypto` encrypts each record with the current vault key and
   vault ID.
4. The coordinator creates or updates an `AtlasVaultLocalStoreEnvelope` with
   encrypted record envelopes and non-sensitive vault metadata.
5. The path locator computes the local-store URL.
6. The directory preparer prepares only the parent directory.
7. `AtlasVaultLocalStoreIO` writes encrypted JSON at the explicit URL.

Overwrite behavior must remain explicit. Atomic replacement, staging names, and
recovery from partial writes require a separate reviewed policy before runtime
implementation.

## 7. Locked Behavior

While locked:

- public search results, public facets, source summaries, public source runs,
  health, and public job detail cache can remain usable;
- saved searches, saved jobs, application notes, profile snippets, and draft
  metadata should be hidden, disabled, or represented by a locked placeholder;
- decrypted private UI state must be cleared;
- no local-store decryption should be attempted;
- no saved-only job keys should be used to warm public detail caches;
- public snapshot writes must remain public-only.

Locking should clear the session and private in-memory arrays without deleting
encrypted vault files or legacy plaintext snapshots.

## 8. Error Handling

Future coordinator errors should be typed and non-sensitive:

- `locked`: no active unlocked session;
- `missingVaultFile`: no encrypted local-store file exists at the computed URL;
- `missingKeychainItem`: local unlock material is unavailable;
- `wrongKey` or `authFailed`: key retrieval or authentication did not produce a
  usable vault key;
- `corruptLocalStore`: envelope or encrypted-record validation failed;
- `unsupportedStoreVersion`: local-store version is not supported;
- `directoryPreparationFailed`: parent directory could not be prepared;
- `writeFailed`: encrypted local-store write failed;
- `partialWriteRisk`: crash-safety outcome is uncertain until atomic policy is
  reviewed.

Missing Keychain item, missing vault file, corrupt vault file, and locked state
must remain separate. Error values and user-facing messages must not include
record IDs tied to user meaning, job keys, saved-search names, notes, snippets,
document references, decrypted payloads, passphrases, recovery keys, or vault
keys.

## 9. Atomic Write And Crash Safety

The current local-store writer uses atomic `Data.write` at an explicit URL, but
runtime persistence needs a reviewed crash-safety policy before app wiring.

Design considerations:

- write to a temporary file in the same directory, then replace the live file;
- use generic staging names that do not contain private metadata;
- decide whether to keep a previous encrypted-store backup;
- define behavior if staging exists after a crash;
- decide whether fsync or platform-specific file coordination is required;
- test directory-preparer failure and write failure without modifying the
  existing live store.

No atomic-write coordinator is implemented in this phase.

## 10. Privacy And Logging Rules

Future coordinator code must not log or serialize:

- vault keys, record keys, passphrases, recovery keys, or derived keys;
- decrypted payloads or plaintext record JSON;
- saved-search names, search text, filters, job keys, statuses, notes, profile
  snippets, draft metadata, generated document references, or personal context;
- private record counts if they reveal saved-job or saved-search membership.

Logs should use non-sensitive error classes and redacted paths. Random vault IDs
should be logged only after review; prefer redaction by default.

## 11. Interaction With Public Snapshot

The public snapshot remains public-only.

Allowed public snapshot data includes public health, search results, facets,
source summaries, source runs, public detail-cache metadata, and non-private
cache timestamps.

The public snapshot must not store:

- saved-search names or saved-search requests;
- saved-job membership or saved-only job keys;
- notes, snippets, draft metadata, document references, or private record
  payloads;
- private record counts or progress values that reveal private membership;
- encrypted vault paths if those paths could expose private metadata.

If future configuration stores the current `vaultID`, it must be random,
non-semantic, and reviewed. Public detail-cache warmup must not infer saved-job
membership from unlocked private state.

## 12. Migration Boundary

Migration execution remains separate from session and filesystem coordination.

Future migration should be additive and user-confirmed. It may read legacy
plaintext private fields only in a reviewed migration flow, write encrypted
records after validation, and report non-sensitive summaries. It must not
automatically delete old plaintext snapshots.

Cleanup of old plaintext snapshots requires a later explicit user-confirmed
phase after encrypted-vault validation. Corrupt encrypted stores also should not
be deleted automatically by the coordinator.

## 13. Tests For Future Implementation

Future implementation tests should cover:

- unlock creates a session without writing files;
- lock clears vault-key bytes and private in-memory state;
- missing vault file maps to a missing-vault state without creating files;
- missing Keychain item maps to key-unavailable state;
- wrong key does not hydrate partial plaintext;
- corrupt store does not hydrate partial plaintext;
- save writes only encrypted local-store JSON;
- directory-preparer failure leaves the existing store untouched;
- overwrite and atomic-write failure behavior is explicit;
- public snapshot serialization contains no private state;
- saved-only job detail warmup does not write public-cache membership signals;
- logs and errors contain no private sentinels.

Tests should continue using temporary roots, fake keys, fake Keychain clients,
and shared encrypted-record vectors.

## 14. Open Questions

- What atomic write strategy and staging cleanup policy should runtime use?
- Where should the current random `vaultID` be stored, and is it acceptable in
  public configuration?
- How should multiple vaults be represented in app state?
- Which passphrase and recovery-key flows should produce the vault key?
- Should Keychain items require user presence in a later phase?
- Should encrypted local stores be included in device backups?
- How should conflicts be represented before cloud sync exists?
- What locked-state UI affordances are appropriate for saved searches and saved
  jobs?

## 15. Recommended Next Loop

Recommended next loop:

1. Review Phase 2D-15.
2. Implement Phase 2D-16 as a test-only coordinator protocol plus mocks, or add
   an atomic-write design first if review prefers.
3. Continue deferring runtime SwiftUI hydration, migration execution, cloud sync,
   device onboarding, and key rotation.
