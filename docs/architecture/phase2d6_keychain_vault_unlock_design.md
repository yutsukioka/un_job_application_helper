# Phase 2D-6 Apple Keychain Vault Unlock Design

Status: design only. This document defines the future Apple vault unlock and
session boundary before any Keychain, LocalAuthentication, vault file I/O, or UI
implementation is added.

## 1. Purpose

Phase 2D-6 defines how the Apple app should eventually unlock an AtlasVault,
hold decrypted private state in memory, and keep public cache behavior available
while the vault is locked.

This phase follows the public snapshot split and Swift encrypted-record helper
work. It does not change runtime behavior. It records the design constraints
that later implementation loops should satisfy before any production vault
unlock path is wired into the app.

## 2. Scope

In scope:

- Apple vault unlock and session design;
- Keychain storage policy options;
- unlock state machine design;
- locked-vault app behavior;
- error, logging, and test requirements for future implementation.

Out of scope:

- Keychain code;
- LocalAuthentication code;
- biometric or device-passcode prompts;
- vault unlock UI;
- vault file read/write;
- `.atlasvault` import/export UI;
- migration execution;
- cleanup of old plaintext snapshots;
- cloud sync;
- device onboarding;
- key rotation.

## 3. Threat Model Slice

The first Apple unlock implementation should explicitly account for these
threats:

- Local attacker with filesystem access: public cache files, encrypted vault
  files, logs, and crash artifacts may be copied. They must not contain raw vault
  keys, passphrases, recovery keys, decrypted payloads, saved-search text,
  saved-job membership, notes, generated document references, or record types in
  plaintext.
- App process memory exposure: decrypted payloads and unwrapped vault keys exist
  only while the app is unlocked and running. They should be held in minimal
  owner objects and cleared when the session locks or terminates.
- Lost or stolen device: Keychain access controls may reduce risk, but recovery
  must still depend on passphrase or recovery-key wrapping rather than biometric
  identity alone.
- Wrong passphrase attempts: wrong input must not hydrate partial plaintext or
  reveal which record failed. Retry limits and lockout behavior need product
  review.
- Corrupt vault metadata: corrupt or unsupported vault files must leave the app
  in a locked or corrupt state without exposing partial decrypted records.
- Stale legacy plaintext snapshots: old snapshots may remain on disk until a
  future explicit cleanup phase. The app must not rehydrate private state from
  those legacy fields.
- Crash logs and debug logs: diagnostics must avoid keys, passphrases,
  decrypted payloads, private record IDs tied to user meaning, search text, job
  keys, notes, profile snippets, and generated document references.

## 4. Key Hierarchy On Apple

AtlasVault keeps a random 256-bit vault key as the root content-encryption key.
Record keys are derived from that vault key, vault ID, and record ID using the
existing Swift/Python HKDF-SHA256 contract. Record payloads are encrypted with
AES-256-GCM and authenticated plaintext metadata.

Vault metadata stores wrapped copies of the vault key. Initial wrapping remains
passphrase or recovery-key based as defined by the AtlasVault v1 contract:

- the passphrase or recovery key is never stored;
- a derived wrapping key unwraps the random vault key locally;
- the raw vault key is never serialized in vault metadata or exported files.

Apple may later cache local unlock material in Keychain, but Keychain storage is
device-local convenience, not account recovery. The recovery path must still
work from passphrase or recovery key plus vault metadata.

Biometric or user-presence checks must never be treated as cryptographic keys.
Face ID, Touch ID, or device password can release a Keychain item; they do not
derive or replace the AtlasVault vault key.

## 5. Proposed Swift Types

### `AtlasVaultSession`

Responsibility: process-local owner of unlocked private vault state.

Inputs: decrypted payload models, encrypted-record metadata needed for future
writes, non-sensitive vault identity, and lock/unlock events.

Outputs: in-memory saved searches, saved jobs, notes, profile snippets, draft
metadata, and non-sensitive unlock state for SwiftUI.

Must not store or log: vault keys, passphrases, recovery keys, derived keys,
decrypted payloads, saved-search text, job keys, notes, generated document
references, or raw private record JSON.

Lifetime: process memory only. It clears on lock, failed unlock reset,
explicit sign-out/account switch, vault corruption, key revocation, and app
termination.

Testing strategy: mock decrypted records, assert lock clears private arrays,
assert public snapshot encoding never references session state, and assert error
states do not expose payload strings.

### `AtlasVaultUnlockState`

Responsibility: typed state machine for user-visible and implementation-visible
vault status.

Inputs: vault file presence, metadata validation result, key availability,
passphrase/recovery attempts, Keychain result, and decrypt result.

Outputs: non-sensitive states such as locked, unlocking, unlocked,
wrongPassphrase, keyUnavailable, corruptVault, migrationAvailable, and
needsRecovery.

Must not store or log: user-entered passphrases, recovery keys, vault keys,
derived keys, decrypted payloads, or specific private record contents.

Lifetime: lightweight observable state. It may outlive an unlocked session but
must not retain private payloads.

Testing strategy: deterministic transition tests with mock unlock providers and
assertions that wrong-passphrase and corrupt-vault paths do not hydrate partial
state.

### `AtlasVaultKeyStore`

Responsibility: protocol boundary for obtaining and clearing local unlock
material.

Inputs: vault ID, key ID, access policy, and caller-provided vault key or local
wrapping key during setup.

Outputs: local key material only to the unlock service, plus non-sensitive
availability errors.

Must not store or log: passphrases, recovery keys, decrypted payloads, or
operation transcripts containing key bytes.

Lifetime: abstraction only. Implementations decide persistence policy.

Testing strategy: in-memory mock implementation that records calls without
persisting secrets and supports key deletion/revocation tests.

### `AtlasKeychainVaultKeyStore`

Responsibility: future Keychain-backed implementation of `AtlasVaultKeyStore`.

Inputs: vault ID, key ID, access-control policy, and a raw vault key or
device-local wrapping key depending on selected policy.

Outputs: vault key bytes or local wrapping key bytes to `AtlasVaultUnlockService`
after Keychain access succeeds.

Must not store or log: passphrases, recovery keys, decrypted payloads, or any
plaintext private record values. It should not expose Keychain item attributes
that encode private payload meaning.

Lifetime: persistent Keychain item plus short-lived returned bytes.

Testing strategy: first implementation should use a mock Keychain adapter, not
the real Keychain, so unit tests can prove item labels, access groups, and access
controls without storing real secrets.

### `AtlasVaultUnlockService`

Responsibility: orchestrate unlock attempts.

Inputs: vault metadata, encrypted records, passphrase/recovery input when
provided, optional Keychain item, vault file locator result, and crypto helper
operations.

Outputs: `AtlasVaultSession` on success or non-sensitive `AtlasVaultUnlockState`
failure on error.

Must not store or log: passphrases, recovery keys, vault keys, derived keys,
decrypted payloads, raw plaintext record JSON, or decrypted failure details.

Lifetime: short-lived per unlock attempt plus references to injected
dependencies. It should not own long-term decrypted state after producing a
session.

Testing strategy: dependency-injected mock metadata store, key store, and crypto
adapter; tests for wrong passphrase, corrupt metadata, missing key, user-presence
failure, and successful unlock.

### `AtlasVaultFileLocator`

Responsibility: future resolver for encrypted vault file locations.

Inputs: application support base directory, account/vault identity, and import
context when needed.

Outputs: paths or URLs for encrypted vault files and staging locations.

Must not store or log: decrypted payloads, passphrases, recovery keys, vault
keys, or private record-derived filenames. Paths must not include saved-search
names, job keys, notes, profile text, or generated document references.

Lifetime: stateless resolver.

Testing strategy: temporary-directory tests that assert generated paths are
separate from the public cache snapshot and contain no private sentinel strings.

### `AtlasVaultRecoveryPromptModel`

Responsibility: future presentation model for passphrase or recovery-key input.

Inputs: high-level unlock reason, retry state, and non-sensitive failure class.

Outputs: user-facing labels, placeholder text, and action availability.

Must not store or log: the passphrase or recovery key after the submit action,
vault keys, derived keys, decrypted payloads, or typed input history.

Lifetime: UI-scoped transient model. Submitted secrets should be passed to the
unlock service and cleared immediately after use.

Testing strategy: assert submitted values are cleared, retry state does not
contain secret text, and user-facing errors remain generic.

## 6. Unlock State Machine

Proposed states:

- `noVault`: no encrypted vault has been created or imported.
- `locked`: vault metadata exists, but no usable unwrapped key/session is active.
- `unlocking`: an unlock attempt is in progress.
- `unlocked`: decrypted private records are available in `AtlasVaultSession`.
- `wrongPassphrase`: passphrase or recovery key failed authentication.
- `keyUnavailable`: Keychain item is missing, revoked, inaccessible, or denied.
- `corruptVault`: vault metadata, record envelope, or authenticated data is
  malformed or unsupported.
- `migrationAvailable`: legacy plaintext state may be eligible for a future
  explicit migration flow.
- `needsRecovery`: local Keychain unlock cannot proceed; passphrase or recovery
  key is required.
- `retryLimited`: optional state when retry policy is exceeded.

Allowed transitions:

- `noVault -> locked` after vault creation/import metadata exists.
- `locked -> unlocking` when the user or auto-unlock policy starts an attempt.
- `unlocking -> unlocked` only after vault key unwrap and record authentication
  succeed.
- `unlocking -> wrongPassphrase` on passphrase/recovery unwrap failure.
- `unlocking -> keyUnavailable` on Keychain access failure.
- `unlocking -> corruptVault` on metadata, envelope, version, nonce,
  ciphertext, or AAD failure.
- `wrongPassphrase -> unlocking` on another user-confirmed attempt.
- `keyUnavailable -> needsRecovery` when local unlock material cannot be used.
- `unlocked -> locked` on explicit lock, app lifecycle lock, key revocation, or
  session clearing.
- `corruptVault -> locked` only after user acknowledgement or file replacement.

No transition should copy private state into `AtlasPublicLocalSnapshot`,
UserDefaults, logs, or public cache files.

## 7. Keychain Storage Policy

### Option A: Store Raw Vault Key In Keychain

Pros:

- simplest app unlock path;
- avoids passphrase prompt on every launch;
- directly feeds existing record crypto helpers.

Cons:

- raw vault key exists as a persistent device secret;
- backup and iCloud Keychain behavior require careful policy review;
- key deletion is the primary local lock/reset mechanism.

### Option B: Store Device-Local Wrapping Key In Keychain

Pros:

- vault key remains wrapped outside the immediate unlock operation;
- easier to rotate local device wrapping without rewriting all records;
- can separate vault metadata from device-local convenience state.

Cons:

- more implementation complexity;
- requires a local wrapped-vault-key record and additional corruption handling;
- still depends on Keychain protection for local unlock convenience.

### Option C: Require Passphrase Every Launch

Pros:

- smallest persistent local secret surface;
- recovery path and normal unlock path are the same;
- easier to reason about during early rollout.

Cons:

- worse user experience;
- more frequent passphrase entry increases phishing and shoulder-surfing risk;
- does not use platform secure storage.

### Option D: User-Presence-Gated Keychain Item

Pros:

- supports convenient unlock with Face ID, Touch ID, or device password;
- can require local user presence before Keychain releases the item;
- aligns with platform security expectations.

Cons:

- LocalAuthentication failure modes complicate app state;
- backup/restore and iCloud Keychain behavior need explicit policy;
- biometrics cannot replace passphrase/recovery for new-device recovery.

Recommended first implementation default: use a Keychain abstraction and mock
tests first, then choose either Option A with strict user-presence and
device-only access controls for a minimal prototype, or Option B if review
requires avoiding persistent raw vault key storage. The production choice should
not be finalized until access-control flags, backup behavior, and recovery UX are
reviewed.

## 8. LocalAuthentication And Biometrics

LocalAuthentication is design-only in this phase.

Future behavior:

- Face ID, Touch ID, or device password may authorize Keychain item release.
- Biometric data is never an encryption key.
- The vault key is still random cryptographic material.
- Passphrase or recovery key remains required for new-device recovery,
  Keychain-item loss, or device migration.
- User-presence failure leaves the vault locked and must not expose partial
  decrypted state.

## 9. Locked-Vault App Behavior

On app launch with a locked vault:

- public cache may load from `AtlasPublicLocalSnapshot`;
- public search and public job detail viewing remain available;
- saved-search names, saved-job rows, notes, profile snippets, draft metadata,
  generated document references, and saved-only job membership stay hidden or
  disabled;
- save actions that would create private state require unlock first or remain
  non-persisted until unlock;
- export is disabled while locked;
- import may start, but must not decrypt or merge until unlock material is
  provided;
- missing vault file shows public cache plus a non-sensitive no-vault or
  needs-import state;
- corrupt vault shows public cache plus a non-sensitive corrupt-vault state;
- wrong passphrase shows a generic retryable failure without revealing whether
  metadata, key wrap, or a particular record failed.

## 10. Interaction With Public Snapshot Split

`AtlasPublicLocalSnapshot` remains public-only. It may persist public health,
search results, public detail cache metadata, sources, source runs, public
updates, and public cache timestamps.

Private state hydrates only after successful vault unlock into
`AtlasVaultSession`. Saved-only job keys, saved-job membership, saved-search
names, notes, profile snippets, draft metadata, generated document references,
record type strings, and personal context must not be written to the public
snapshot.

Old plaintext snapshots are not deleted automatically. Cleanup remains a future
explicit user-confirmed phase after encrypted vault validation and migration
verification.

## 11. Error Handling And Logging

Rules:

- never log passphrases;
- never log recovery keys;
- never log raw vault keys or derived keys;
- never log decrypted payloads or raw plaintext record JSON;
- never include private payload values in crash reports;
- map authentication failures to generic wrong-passphrase or unlock-failed
  classes;
- allow non-sensitive diagnostics for unsupported versions, malformed base64,
  invalid nonce length, corrupt envelope layout, missing file, and unavailable
  Keychain item;
- avoid filenames, metrics, or progress messages that reveal saved-only job
  membership or private record counts where that would expose user behavior.

## 12. Tests For Future Implementation

Future implementation loops should add tests for:

- Keychain mock stores and retrieves only the expected item attributes;
- wrong passphrase does not hydrate partial plaintext;
- corrupt vault does not hydrate partial plaintext;
- locked state still allows public search;
- unlocked state hydrates saved searches and saved jobs in memory;
- no private payload appears in `AtlasPublicLocalSnapshot`;
- no vault keys, passphrases, recovery keys, or decrypted payloads appear in
  logs;
- user-presence failure leaves the vault locked;
- Keychain item deletion or revocation locks the vault;
- recovery-key path works without a Keychain item;
- missing vault file keeps public cache usable;
- corrupt vault metadata produces a non-sensitive error;
- old plaintext snapshot fields are ignored unless a future explicit migration
  command reads them.

## 13. Migration And Cleanup Boundaries

No automatic cleanup of old plaintext snapshots should be added with unlock
support. Cleanup requires explicit user confirmation and should occur only after:

- a dry-run migration report is reviewed;
- encrypted records are written and validated;
- decrypt round-trip succeeds;
- public snapshot remains private-free;
- the user confirms cleanup.

Migration execution is later. Cleanup is later. Secure deletion policy is later.

## 14. Open Questions

- Should the first implementation store the raw vault key in Keychain or store a
  device-local wrapping key?
- Which Keychain access-control flags are appropriate for macOS and iOS?
- Should Keychain items be device-only or eligible for iCloud Keychain?
- How should backup and restore behave when the encrypted vault file is restored
  without the Keychain item?
- What retry limits are appropriate for wrong passphrase and recovery-key
  attempts?
- What recovery UX is needed before users can rely on the vault?
- Should the app support an "always require passphrase" mode?
- Should unlock state expose private record counts, or are counts themselves
  private?
- How should account identity and future cloud identity relate to local vault
  identity?

## 15. Recommended Next Implementation Loop

Recommended next loop: Phase 2D-6 review pass, then Phase 2D-7 Keychain
abstraction protocol plus mock tests only.

Phase 2D-7 should not read or write real vault files. It should define the
Keychain abstraction, a mock store, access-policy enums, and tests proving that
mock unlock behavior does not hydrate private state on wrong passphrase,
corrupt vault, missing key, or user-presence failure.
