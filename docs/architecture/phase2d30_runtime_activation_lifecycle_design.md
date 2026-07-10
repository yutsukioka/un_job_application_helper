# Phase 2D-30 AtlasVault Runtime Activation Lifecycle Design

## 1. Purpose

Phase 2D-30 defines how a future runtime-neutral controller will explicitly
activate, lock, cancel, and tear down the side-effect-free service graph added
in Phase 2D-29.

## 2. Explicit Design-Only Scope

This phase adds documentation only. It adds no activation controller, key
retrieval, filesystem access, encrypted-store read/write, hydration, private
state, app-launch call site, SwiftUI integration, migration, cloud sync,
recovery flow, onboarding, or key rotation. It does not claim production
readiness.

## 3. Construction Versus Activation

`AtlasVaultRuntimeFactory` construction remains side-effect-free and locked.
Activation is a separate explicit operation. Only activation may validate a
vault ID, select key material, resolve the root, create a per-vault scope, load
an encrypted store, and hydrate private in-memory state.

## 4. Existing Runtime Service Graph

The locked graph contains inactive root, key-store, directory, local-store,
atomic-writer, merger, saver, hydrator, and per-vault factory seams. The
per-vault factory binds a coordinator to one validated vault ID and one safe
explicit root. No graph value contains an unlocked key or hydrated state.

## 5. Proposed Activation-Controller Boundary

Introduce a runtime-neutral `AtlasVaultActivationController` actor in a later
phase. It owns lifecycle serialization and coordinates injected protocols. It
must not be a SwiftUI observable object, service locator, global singleton, or
public-cache dependency.

The controller accepts a locked runtime graph plus explicit activation input.
It alone may publish a completed private activated scope.

## 6. Proposed Public Non-Sensitive Activation State

Expose a small state projection such as:

- `locked`;
- `activating`;
- `unlocked`;
- `failed(AtlasVaultActivationFailure)`.

Failure values are stable categories without paths, key data, record IDs,
payload types, private counts, or underlying error descriptions. Do not expose
partially completed steps as public state.

## 7. Proposed Private Activated-Session Object

Keep a private controller-owned object containing the canonical wipeable vault
session, the bound per-vault services, and hydrated private state. It is
installed only after all activation steps succeed. It must not conform to
`Codable`, enter diagnostics, or be stored in the public snapshot.

Before implementation, reconcile `AtlasVaultSession` and
`AtlasVaultUnlockedSession` so one canonical key owner supplies short-lived
crypto access without a second long-lived `Data` copy.

## 8. Vault-ID Validation

Validate the caller-supplied ID with the existing non-semantic vault-ID policy
before retrieving key material. Invalid, whitespace, path-like, oversized, or
reserved semantic IDs fail without root, Keychain, filesystem, or crypto calls.

## 9. Key-Source Priority

For a valid vault ID, key selection order is:

1. an explicitly supplied, already unwrapped passphrase/recovery key;
2. the local `AtlasVaultKeyStore` item;
3. a non-sensitive key-unavailable result.

The controller does not obtain passphrases, unwrap recovery material, prompt
for user presence, or persist an explicitly supplied key unless a separate
reviewed policy requests that operation.

## 10. No Key Retrieval During Composition Construction

Factory creation must never call `loadVaultKey`, `saveVaultKey`, or SecItem.
Key selection occurs only after the caller invokes activation and vault-ID
validation succeeds.

Use an injected key-loading seam that preserves missing-item and key-store
failure as distinct outcomes. Do not use the current collapsing
`unlockWithStoredKey` behavior unchanged.

## 11. Root-Directory Resolution During Explicit Activation Only

After key selection succeeds, activation calls the injected root provider. A
root failure triggers key release/wipe and leaves the controller locked with no
per-vault scope.

The resolved root must retain the existing safe local, non-filesystem-root
policy. It is never written to logs or public state.

## 12. Path Location

Pass the resolved root and validated vault ID to
`AtlasVaultPerVaultServiceFactory`. The resulting bound locator rejects a
session for any other vault. Path construction must not contain private record
meaning and must not create directories or files.

## 13. Missing Vault-File Behavior

A missing encrypted local store is a distinct `storeMissing` activation
failure for the first implementation. Activation must not silently create an
empty store, prepare directories, or install an unlocked session.

New-vault creation requires a separate explicit creation policy and tests. On
missing store, release/wipe loaded key material and return to a locked private
scope.

## 14. Existing Vault Load Behavior

For an existing vault, call the bound persistence coordinator once. It returns
only an encrypted local-store envelope. Load errors are mapped to non-sensitive
categories; the controller never publishes a partially loaded envelope or
uses legacy plaintext data.

## 15. Hydration Behavior

After a valid encrypted store is loaded, pass only its encrypted records and a
short-lived session view to the injected hydrator. Hydration either returns a
complete `AtlasVaultHydratedState` or fails closed. Decrypted payloads remain in
memory and are never written or logged.

## 16. Success Transition

Construct the private activated-session object locally, then install the
session, bound services, and hydrated state as one actor-isolated transition.
Only after installation may public state become `unlocked`.

No observer may see `unlocked` with missing services, missing key ownership, or
partial hydrated collections.

## 17. Wrong-Key And Authentication Failure

Authentication failure maps to a non-sensitive `authenticationFailed` result.
Discard all provisional plaintext and encrypted working values, invoke the
selected key wipe/release boundary, install no private state, and remain
locked.

Do not retry automatically with another source after a supplied key fails;
that could hide user intent or create a key-source oracle.

## 18. Corrupt-Store Failure

Malformed local-store JSON, invalid encrypted envelopes, or corrupt payloads
map to `corruptStore`. Do not hydrate a subset, repair the file, overwrite it,
or expose decoder details. Wipe/release provisional key ownership.

## 19. Unsupported-Version Failure

Unsupported store, record, or payload schema versions map to
`unsupportedVersion`. Preserve the existing encrypted file unchanged, install
no private state, and wipe/release provisional key ownership.

## 20. Missing-Keychain-Item Failure

When no explicit key is supplied and `loadVaultKey` returns `nil`, report
`keyUnavailable`. Do not resolve the root or inspect the filesystem. This is
distinct from a thrown Keychain/key-store error, which maps to the stable,
non-sensitive `keyStoreFailure` category and likewise performs no root or
filesystem operation.

## 21. Filesystem And Path Failure

Root lookup, root validation, per-vault construction, path binding,
directory-policy failure, or transport/I/O failure while loading an existing
encrypted store maps to `vaultUnavailable` without exposing the path. A missing
store remains `storeMissing`; malformed or unsupported store content retains
the dedicated `corruptStore` or `unsupportedVersion` category. If key material
has already been selected, wipe/release it before publishing failure.

## 22. Cancellation Behavior

Activation receives a monotonically increasing attempt token and checks
cancellation between each boundary. Cancellation invalidates the attempt,
discards provisional services/state, wipes or releases selected key material,
and returns to `locked` without publishing a failure containing private data.

Late completion from a cancelled attempt must not install state. Synchronous
dependencies that cannot be interrupted must have their result discarded when
control returns.

## 23. Concurrent Activation Behavior

Actor isolation serializes state transitions. While one activation is active,
a second activation request should fail with non-sensitive
`activationInProgress`; it must not cancel or replace the first implicitly.

An explicit cancel or lock operation may invalidate the active attempt before
the caller starts another.

## 24. Re-Entrant Activation Behavior

Calling activate while already unlocked must fail with `alreadyUnlocked`, even
for the same vault. Switching vaults requires an explicit lock/teardown followed
by a new activation, preventing key and hydrated-state overlap.

## 25. Per-Vault Serialization

The controller owns at most one active vault and one activation attempt. A
future per-vault save actor may serialize read-modify-write operations, but
activation must not overlap a save or expose services before success.

Cross-process coordination remains outside this lifecycle contract.

## 26. Lock Behavior

Lock invalidates any active attempt, clears hydrated state, releases bound
services, wipes the canonical session key as far as the selected representation
allows, and publishes `locked` only after teardown completes.

Repeated lock while already locked is an idempotent no-op. Lock never deletes a
Keychain item or local-store file.

## 27. Teardown After Every Post-Key Failure

Once key material is selected, every later failure path must execute one common
teardown routine. This includes root lookup, root validation, per-vault scope
creation, cancellation, store load, store validation, hydration, attempt-token
supersession, and state-installation rejection.

Tests must spy on the wipe/release boundary for every path, not merely assert
that public state is locked.

## 28. Vault-Key In-Memory Lifetime

Acquire key bytes as late as possible, keep one canonical owner, and expose
them only through scoped crypto closures. The controller retains key ownership
only while unlocked and releases it on lock, cancellation, failure, vault
switch, or controller teardown.

Do not include keys in `Equatable`, hashing, reflection, task-local values,
notifications, or captured long-lived closures.

## 29. Best-Effort Memory Clearing And Swift/Data Limitations

Invoke `AtlasVaultSession.wipeVaultKey` or an equivalent canonical wipe path
before releasing the session. Swift value semantics, optimizer behavior, and
`Data` copy-on-write mean complete historical zeroization cannot be guaranteed.
Document this limitation and avoid production-strength zeroization claims.

## 30. No Partial Hydrated-State Installation

Hydrate into a provisional local value. Any per-record failure discards the
entire provisional state for the first implementation. The controller never
merges partial collections into a previously locked or unlocked state.

Partial-recovery policy, if ever needed, requires a separate privacy and data
integrity review.

## 31. No Private Values In State, Error, Or Log Output

State, errors, descriptions, metrics, and logs must exclude vault IDs where not
required, keys, paths, ciphertext, record IDs/types, private counts,
saved-search names/text/filters, job keys, statuses, notes, snippets, drafts,
and document references.

Only coarse operation names and stable non-sensitive failure categories are
eligible for future diagnostics.

## 32. Public Snapshot Remains Untouched

Activation does not read, mutate, or serialize `AtlasPublicLocalSnapshot`.
Unlock state, private counts, record IDs, saved-only job membership, and
hydrated values remain outside `AtlasLocalCache` and public detail warmup.

## 33. No SwiftUI Or App-Launch Integration

The activation controller remains runtime-neutral and unreferenced by app
entry points, scenes, views, `SearchViewModel`, and `AtlasLocalCache`. Future UI
receives only a reviewed redacted projection after controller tests are merged.

## 34. Future Test Plan

Phase 2D-31 tests should verify:

- construction and initial locked state perform zero calls;
- invalid vault ID fails before key retrieval;
- supplied-key priority and Keychain fallback ordering;
- missing item and key-store failure remain distinct;
- root/path/load/hydration ordering;
- missing store performs no write or directory creation;
- success installs session/services/state atomically;
- wrong key, corruption, unsupported version, path failure, and cancellation
  install no partial state and invoke wipe/release exactly once;
- concurrent and re-entrant activation policies;
- lock and controller teardown clear private state and key ownership;
- late cancelled results cannot install state;
- descriptions/errors contain no fake private sentinels;
- public snapshot bytes remain unchanged;
- source guards exclude SwiftUI, app launch, cache/view-model integration,
  migration, and networking.

## 35. Deferred

- controller implementation until Phase 2D-31;
- passphrase/recovery unwrapping and UI prompts;
- LocalAuthentication and biometrics;
- new-vault creation and key persistence policy;
- SwiftUI and app-launch integration;
- private-state mutation/save UI;
- migration and cleanup of old plaintext snapshots;
- cloud sync and conflict UI;
- device onboarding, recovery UX, and key rotation;
- cross-process locking and production security review.
