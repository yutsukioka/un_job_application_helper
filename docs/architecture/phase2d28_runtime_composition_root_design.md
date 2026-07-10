# Phase 2D-28 AtlasVault Runtime Composition Root Design

## 1. Purpose

Phase 2D-28 defines how the isolated Apple AtlasVault services should be
constructed and activated in a future runtime. The composition root must keep
private records encrypted at rest, keep public cache state separate, and make
every side effect an explicit operation.

## 2. Design-Only Scope

This phase is documentation only. It adds no factory, runtime call site, file or
Keychain access, unlock behavior, SwiftUI state, migration execution, cloud
sync, device onboarding, recovery flow, or key rotation. It does not claim
production readiness.

## 3. Existing Service Inventory

The current isolated building blocks are:

- `AtlasVaultRootDirectoryProviding` and
  `AtlasApplicationSupportVaultRootProvider` for generic root discovery;
- `AtlasVaultPathLocator` and `AtlasInjectedRootVaultPathLocator` for a
  non-semantic per-vault store URL;
- `AtlasVaultDirectoryPreparer` for explicit parent preparation;
- `AtlasVaultAtomicStoreWriting` and `AtlasVaultAtomicStoreWriter` for complete
  encrypted-envelope commits;
- `AtlasVaultLocalStoreIO` and `AtlasVaultLocalStoreProviding` for encrypted
  local-store encoding and IO;
- `AtlasVaultLocalStoreMerging` for encrypted record-envelope merge semantics;
- `AtlasVaultPersistenceCoordinator` for path, preparation, load, merge, and
  atomic-save coordination;
- `AtlasVaultKeyStore`, `AtlasKeychainVaultKeyStore`, `AtlasKeychainClient`, and
  `SecItemAtlasKeychainClient` for isolated vault-key storage;
- `AtlasVaultRecordCrypto` for per-record encryption and authentication;
- `AtlasVaultRecordSaving` for private mutation-to-encrypted-record conversion;
- `AtlasVaultRecordHydrating` for encrypted-record-to-memory conversion.

`AtlasPublicLocalSnapshot` remains the separate public cache model.

## 4. Proposed Composition-Root Type

Use an `AtlasVaultRuntimeFactory` as the side-effect-free construction boundary.
It should create an `AtlasVaultLockedServices` scope and, only after explicit
activation succeeds, an `AtlasVaultUnlockedServices` scope. A small
`AtlasVaultRuntimeServices` value may hold shared immutable adapters and
factories, but it must not hold an unlocked key.

The exact generic/type-erasure strategy should be chosen in Phase 2D-29 tests.
The factory must expose dependencies explicitly rather than use service locators
or global singletons.

## 5. Construction Versus Activation

Constructing the factory or locked scope must not call the root provider, access
Keychain, inspect the filesystem, read or write a store, or unlock a vault. It
may construct value-type adapters whose initializers are side-effect-free.

Activation is a separate explicit operation with a caller-supplied random
`vaultID`. Only activation may request the root, retrieve key material, construct
the per-vault path/coordinator, read the encrypted store, and hydrate private
state. Saving is another explicit operation.

## 6. Locked Dependency Graph

The locked graph contains factories or inactive adapters for root discovery,
key storage, path-location construction, directory preparation, local-store IO,
atomic writing, merging, saving, and hydration. It contains no vault key,
unlocked session, decrypted record, or private count.

Public search and `AtlasPublicLocalSnapshot` may remain usable while this graph
is locked, but they are not dependencies of the vault graph.

## 7. Unlocked Dependency Graph

An explicit unlock operation produces a per-vault scope containing one session
owner, the coordinator built for that vault's root/path, the saver, hydrator,
and in-memory `AtlasVaultHydratedState`. The scope must never be cached in the
public cache or a process-global registry.

Once activation has loaded any key material, failure during root lookup, path or
coordinator construction, encrypted-store load, or hydration must invoke the
selected key-wipe/release boundary, discard the entire provisional scope, and
expose no session or partial hydrated state.

## 8. Key Retrieval Boundary

The current `AtlasVaultUnlockService.unlockWithStoredKey` collapses both a
missing key and every `AtlasVaultKeyStore` load error into `keyUnavailable`.
Runtime composition must not claim those outcomes are distinct while using that
method unchanged. Before runtime wiring, extend the unlock state/service or add
an injected key-loading coordinator that preserves separate non-sensitive
`keyUnavailable` and `keyStoreFailure` outcomes.

The production adapter may compose
`AtlasKeychainVaultKeyStore<SecItemAtlasKeychainClient>`, but constructing it
does not call SecItem APIs. Invalid key length and corrupt-vault outcomes remain
separate from the key-loading outcome.

Passphrase/recovery unwrapping, user-presence policy, LocalAuthentication, and
unlock UI remain outside this composition root.

## 9. Root, Path, And Filesystem Boundary

After key retrieval succeeds, activation explicitly calls the root provider,
then injects that URL into `AtlasInjectedRootVaultPathLocator`. The path locator
alone appends `Atlas/Vaults/<vaultID>/atlasvault-local-store.json`.

The root provider creates nothing. The coordinator invokes the directory
preparer only through an explicit load/save operation, and the atomic writer
receives an explicit destination. Private payloads and record types never enter
paths.

## 10. Atomic Save Boundary

Production composition should expose the coordinator's atomic record-save path,
not silently select the legacy direct writer. The flow is encrypted records,
merger validation, parent preparation, complete in-memory store encoding, and
same-directory atomic commit.

The result must preserve the distinction between `committed` and
`committedDurabilityUnconfirmed`. Atomic replacement is not a claim of complete
power-loss durability on every Apple filesystem.

## 11. Hydration Boundary

After an encrypted store is loaded, `AtlasVaultRecordHydrator` receives only the
encrypted records and the active session. Decryption and payload-type dispatch
occur inside that boundary, producing private in-memory state or a fail-closed,
non-sensitive error.

No partially hydrated state should be published after authentication,
corruption, or schema failure. Tombstones remain metadata for merge/save and
are excluded from active private UI models.

## 12. Saver And Write-Back Boundary

Private edits become an `AtlasVaultMutationSet`. `AtlasVaultRecordSaver`
validates payload type/schema, generates non-semantic IDs/revisions and fresh
nonces, and returns encrypted record envelopes. The coordinator then merges and
atomically writes only those encrypted envelopes.

Decrypted payloads must never be passed to local-store IO, the merger, the
atomic writer, the public snapshot, or diagnostics.

## 13. Actor Isolation And Thread Safety

A future `AtlasVaultRuntimeController` actor should own the active session,
hydrated state, and state transitions for one vault. The existing service values
are `Sendable`, but that does not itself serialize read-modify-write sequences.

No SwiftUI object should directly own mutable key bytes. UI-facing state should
later receive redacted, immutable projections from the actor.

## 14. In-Process Save Serialization

The per-vault actor should serialize mutation planning, saver output, current
store load, revision merge, and atomic commit as one logical operation. A later
save must observe the result of the prior save before deriving parent revisions.

This addresses in-process lost updates only. Cross-process locking and
compare-and-swap against an expected store generation remain open production
requirements.

## 15. Multiple-Vault Support

The generic Application Support root may host multiple random vault IDs, each
with a separate path, actor, session, and save queue. A key or hydrated record
from one scope must never be accepted by another scope.

The first runtime should keep at most the explicitly active vault unlocked.
Concurrent multi-vault activation needs dedicated lifecycle and memory-pressure
tests before adoption.

## 16. Session Lifecycle

Recommended states are locked, unlocking, loading, unlocked, saving, and a
small set of non-sensitive failure states. Lock, account/vault switch,
background-expiry policy, failed hydration, and explicit teardown must clear
private state and release the session.

Construction is not unlocking. A missing encrypted store may become an explicit
empty/new-vault state only after a reviewed creation policy, not by silently
creating a store during composition.

## 17. Vault-Key In-Memory Lifetime

The code currently has both wipeable `AtlasVaultSession` and redacted
`AtlasVaultUnlockedSession`. Runtime work must select one canonical key owner or
an explicit bridge that avoids long-lived duplicate `Data` copies. The actor
should minimize key copies, keep them scoped to crypto closures, and invoke the
available wipe path on lock.

Swift and `Data` cannot guarantee elimination of every historical memory copy;
the implementation must document that limitation rather than claim secure
zeroization.

## 18. Key Redaction And Debug Policy

Session, service-container, and controller descriptions must never synthesize or
reflect key bytes. Debug output should use fixed redaction text and may expose
only non-sensitive state classes and key byte count when necessary.

No error, assertion, metric, crash annotation, or test failure should include
base64/hex key material, ciphertext, decrypted JSON, or a full user-specific
path.

## 19. Non-Sensitive Runtime Error Mapping

After the key-loading seam is extended as required above, the controller should
map root, Keychain, persistence, merge, atomic-write, save, and hydration errors
into stable categories such as key unavailable, key-store failure, invalid key,
vault unavailable, corrupt/unsupported vault, conflict, save failed, and
committed durability unconfirmed.

Underlying paths, OSStatus descriptions, record IDs, payload types, and private
values stay below the mapping boundary. A committed result must never be
reported as an ordinary pre-commit failure.

## 20. Public Snapshot Separation

`AtlasPublicLocalSnapshot` remains outside the vault dependency graph. Neither
factory nor controller writes hydrated state, private record counts, record IDs,
saved-only job keys, or unlock state into that snapshot.

Public detail-cache warmup must not be driven by saved membership in a way that
creates filenames, counts, or diagnostics revealing private behavior.

## 21. No Private Data In Diagnostics

Permitted diagnostics are coarse operation names, elapsed time, success/failure
class, and aggregate counts only after a privacy review. Avoid record type when
it is not operationally necessary.

Never log saved-search names/text/filters, job keys or membership, statuses,
notes, snippets, draft/document references, decrypted payloads, keys, nonces,
ciphertext, or user-specific paths.

## 22. Test Environment Composition

Phase 2D-29 should inject fake root and key providers, fake or temporary-root
filesystem dependencies, deterministic clocks/ID sources where already
supported, and spies for every side-effect seam. Merely constructing the graph
must leave every spy untouched and create no path.

Activation tests should cover success and every fail-closed boundary without
using the host Application Support directory, real Keychain, real personal
data, or committed `.atlasvault` artifacts.

## 23. Production Environment Composition

A later production factory may construct the Foundation root locator,
`AtlasApplicationSupportVaultRootProvider`, injected-root path locator factory,
file-manager preparer, local-store IO adapter, Foundation atomic filesystem
client/writer, merger, saver, hydrator, and SecItem-backed key store.

It must still defer every operation until activation. Backup exclusion,
file-protection class, app entitlements, durability, cross-process concurrency,
and lifecycle policy require separate production review.

## 24. No Runtime Call Site Yet

Phase 2D-28 does not instantiate these services in app launch, scenes,
delegates, commands, background tasks, or tests outside a future composition
suite. Existing runtime behavior remains unchanged.

## 25. No SwiftUI Integration Yet

No environment object, observable model, view modifier, screen, prompt, or
locked/unlocked UI state is introduced. UI hydration and mutation remain after
the factory and lifecycle contracts have executable tests.

## 26. Migration Remains Separate

Runtime composition must not read legacy plaintext saved-search or tracker
files. Migration remains additive, explicit, user-confirmed, and separately
audited; old plaintext snapshots are not deleted or cleaned in this phase.

## 27. Cloud Sync Remains Separate

The graph has no network client, account transport, upload/download path, or
conflict server. Local encrypted persistence is not a cloud-sync or
production-readiness claim.

## 28. Recovery And Key Rotation Remain Separate

Recovery key handling, passphrase UX, device onboarding, key wrapping,
re-encryption, key rotation, and multi-device recovery are not responsibilities
of the runtime composition root designed here.

## 29. Future Tests

Future composition tests should verify:

- construction invokes no root, Keychain, filesystem, crypto, or network seam;
- locked scope contains no session or hydrated state;
- missing key and key-store load failure remain distinct through the extended
  key-loading seam;
- explicit activation orders key retrieval, root lookup, path construction,
  encrypted load, and hydration;
- root lookup, path/coordinator construction, encrypted-store load, and hydration
  failures after key retrieval each invoke the key-wipe/release boundary;
- every activation failure publishes no session or partial private state;
- lock clears session/private state and invokes the selected wipe boundary;
- atomic saves serialize per vault and propagate both commit states;
- vault IDs isolate paths, sessions, and save queues;
- diagnostics/errors reveal no keys, paths, record types, or private sentinels;
- public snapshots remain byte-for-byte independent of vault activation;
- production adapters can be constructed without being called.

## 30. Recommended Phase 2D-29

Add a test-only runtime composition factory and dependency-graph tests. Extend
or wrap the current unlock helper so missing-key and key-store-failure outcomes
remain distinct, reconcile the two session representations, prove construction
is side-effect-free, and exercise locked/unlocked scopes entirely with mocks and
temporary roots before any app-launch or SwiftUI integration.
