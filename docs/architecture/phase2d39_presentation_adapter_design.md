# Phase 2D-39 AtlasVault Presentation Adapter Design

## 1. Purpose

Phase 2D-39 defines a presentation-adapter boundary that can translate the
reviewed runtime facade, lifecycle status, transient command progress, and an
unlocked in-memory private-state snapshot into UI-safe presentation values.

## 2. Design-Only Scope

This phase adds documentation only. It adds no adapter implementation,
framework import, observable model, property wrapper, app-entry call site,
vault operation, unlock prompt, persistence, migration, cloud behavior, or
production-readiness claim.

## 3. Existing Runtime Facade

`AtlasVaultRuntimeFacading` is the sole command boundary. It exposes redacted
status plus explicit activation, lock, and mutation operations. Its
module-internal `AtlasVaultPrivateStateReading` capability is the only source
from which a future adapter may request an unlocked private snapshot.

## 4. Existing Lifecycle Coordinator

`AtlasVaultLifecycleCoordinator` accepts neutral lifecycle events and controls
the facade through redacted status, activation cancellation, and lock. The
presentation adapter may consume its non-sensitive status, but it must not
schedule grace locks or subscribe to platform lifecycle notifications.

## 5. Existing Private State Store

`AtlasVaultPrivateStateStore` owns complete hydrated state behind an internal,
generation-checked actor boundary. Presentation code receives an immutable
snapshot only after the facade confirms the same unlocked generation. It never
accesses the store directly.

## 6. Why Presentation Adaptation Is Separate

Runtime code owns security-sensitive operations and canonical state;
presentation code owns only ephemeral display values. Keeping the adapter
separate prevents views from becoming service locators and makes status
redaction, lock clearing, stale-result rejection, and public-cache isolation
independently testable.

## 7. Proposed Presentation Adapter Protocol

A future `AtlasVaultPresentationAdapting: Sendable` protocol should expose a
deterministic projection operation. Public requirements should use UI-safe
inputs and outputs only; module-internal plumbing may supply the
generation-checked private snapshot. The adapter must not expose facade,
lifecycle, store, crypto, Keychain, or filesystem services through its result.

## 8. Proposed Presentation Snapshot

`AtlasVaultPresentationSnapshot: Equatable, Sendable` should contain one
`AtlasVaultPresentationStatus` and an optional in-memory private projection.
The private projection is present only for an unlocked, current generation.
The snapshot is not `Codable`, persistable, or a public-cache model.

## 9. UI-Safe Status Enum

`AtlasVaultPresentationStatus: Equatable, Sendable` should use a closed set of
fixed cases such as `locked`, `noVault`, `activating`, `locking`, `unlocked`,
`keyUnavailable`, `corruptStore`, `unsupportedVersion`, `saveInProgress`,
`saveFailed`, `cancelled`, and `failed`. Cases carry no associated private or
diagnostic values.

## 10. Locked Status

`locked` contains no private projection, private count, saved-membership hint,
prior selection, or vault-existence detail. Public job search remains usable
through its independent public-cache boundary.

## 11. No-Vault Status

`noVault` represents the reviewed non-sensitive missing-store result. It does
not disclose a path, create a file, imply Keychain state, or distinguish why a
vault was never created.

## 12. Activating Status

`activating` contains no prior private projection and no activation stage,
vault ID, key source, path, record count, or partial hydration. Duplicate
activation and private mutation controls remain unavailable.

## 13. Unlocked Status

`unlocked` may include a private presentation projection only when the facade
is currently unlocked and the supplied private snapshot was read for that
same operation generation. Status itself contains no private values or counts.

## 14. Key-Unavailable Status

`keyUnavailable` groups reviewed key-unavailable outcomes without revealing
whether a Keychain item exists, which source was attempted, or which bytes
failed. It contains no prior private projection.

## 15. Corrupt-Store Status

`corruptStore` represents authentication failure or malformed encrypted-store
content using fixed user-facing language. It exposes no path, ciphertext,
schema, record metadata, or partial plaintext and contains no private
projection.

### Unsupported-Version Status

`unsupportedVersion` is distinct from corruption. It permits fixed upgrade
guidance without revealing the encountered version, schema details, record
types, or payload values, and it contains no private projection.

## 16. Save-In-Progress Status

`saveInProgress` is a transient, non-sensitive status derived from the facade's
`saving` state. A current unlocked projection may remain visible only under a
separately reviewed interaction policy; the adapter never stores the mutation
payload or serializes retry state.

## 17. Save-Failed Status

`saveFailed` carries no underlying error. A pre-commit failure may retain the
previous current-generation projection only if a fresh facade status remains
unlocked. A commit-aware failure that locks the facade clears it immediately.

## 18. Cancelled Status

`cancelled` is a bounded presentation transition, not durable runtime state.
It contains no request details and no prior projection unless a fresh unlocked
facade status and current private snapshot independently justify one.

## 19. Non-Sensitive Failure Status

`failed` is a fixed fallback for reviewed failures that do not map to a more
specific UI category. It carries no `Error`, `localizedDescription`, path,
identifier, type, count, payload, or operation detail.

The first implementation should exhaustively map every existing
`AtlasVaultActivationFailure` case:

| Runtime activation failure | Presentation status |
| --- | --- |
| `keyUnavailable`, `keyStoreFailure`, `invalidVaultKey` | `keyUnavailable` |
| `storeMissing` | `noVault` |
| `authenticationFailed`, `corruptStore` | `corruptStore` |
| `unsupportedVersion` | `unsupportedVersion` |
| `cancelled` | `cancelled` |
| `invalidVaultID`, `vaultUnavailable`, `activationInProgress`, `alreadyUnlocked` | `failed` |

The switch has no permissive default, so a future runtime failure case requires
an explicit privacy review and mapping before compilation succeeds.

## 20. Public Versus Private Presentation Models

Public presentation status is safe to distribute across locked UI surfaces.
Private presentation values are a separate in-memory projection available only
while unlocked. They must never be copied into public runtime status,
`AtlasPublicLocalSnapshot`, shared restoration state, or analytics.

## 21. Private State Projection

Projection is a one-way, deterministic mapping from a generation-checked
`AtlasVaultHydratedState` into purpose-specific display values. The adapter
does not retain the hydrated input, tombstones, encrypted envelopes, keys, or
storage metadata after producing the current projection.

## 22. Saved-Search Projection

`AtlasVaultSavedSearchPresentation` should expose only fields required by an
unlocked saved-search view, such as a presentation ID, name, query, and reviewed
filter values. These values are private and must not enter locked status,
public search state, diagnostics, or previews derived from real data.

## 23. Saved-Job Projection

`AtlasVaultSavedJobPresentation` may expose an opaque presentation ID and the
private job reference needed for an unlocked in-memory join. That join must not
set saved flags, warm saved-only job keys, or otherwise mutate public cache.

## 24. Application-Note Projection

`AtlasVaultApplicationNotePresentation` contains only the note fields required
for the current unlocked screen. Notes, statuses, and job associations remain
private and are excluded from descriptions, accessibility metadata by default,
public snapshots, and persistent navigation.

## 25. Profile-Snippet Projection

`AtlasVaultProfileSnippetPresentation` is an unlocked in-memory value. Snippet
labels and content are private, are not logged or restored, and clear on lock
or generation replacement.

## 26. Draft-Metadata Projection

`AtlasVaultDraftMetadataPresentation` may expose only reviewed fields required
by an unlocked draft screen. Draft context and generated-document references
must not enter public cache, paths, logs, restoration, or locked presentation.

## 27. IDs Exposed To Presentation

Presentation IDs should be opaque and stable only for the current unlocked
generation and UI diffing needs. Views do not receive vault IDs, key IDs,
revisions, parent revisions, or semantic IDs derived from search names or job
keys unless a later mutation design explicitly requires and reviews them.

## 28. Avoid Record Envelopes In Presentation

Neither encrypted record envelopes nor local-store envelopes enter adapter
outputs. Presentation code does not inspect envelope metadata, ciphertext,
nonces, tags, tombstones, record versions, or plaintext payload headers.

## 29. Avoid Keys And Filesystem URLs

Vault keys, supplied keys, Keychain references, root URLs, vault URLs, and file
names are not adapter inputs or outputs. They are never retained, described,
used as IDs, or passed to views.

## 30. Avoid Record Types In Public Status

Record type remains inside encrypted payloads and may guide only the internal
hydration/projection path after unlock. Public status, failure categories,
metrics, and accessibility output do not disclose record types.

## 31. Avoid Private Counts In Locked State

Locked, no-vault, activating, locking, cancelled, and failure states expose no
private collection counts or booleans such as `hasSavedJobs`. Collection
counts are private even when zero.

## 32. Redacted Error Messages

The adapter maps known categories to fixed copy selected by the host. It never
interpolates underlying error text, requests, identifiers, types, paths,
private fields, counts, keys, or encrypted bytes.

## 33. No Direct Keychain Access

The adapter has no `AtlasVaultKeyStore`, `AtlasKeychainVaultKeyStore`, `SecItem`,
or Keychain-client dependency. It sees only reviewed runtime status categories.

## 34. No Direct Filesystem Access

The adapter does not resolve roots, locate paths, prepare directories, read or
write local stores, perform atomic replacement, or inspect file existence.

## 35. No Direct Cryptographic Access

The adapter does not derive keys, unwrap keys, select nonces, seal records,
open records, decode ciphertext, or call record crypto helpers.

## 36. No Runtime Mutation Logic

Projection is not activation, lock, save, or cancellation orchestration. A
future command owner may invoke the facade, but the presentation adapter itself
does not mutate runtime or private store state.

## 37. MainActor Boundary For A Future Adapter

A later observable owner should publish presentation snapshots on `MainActor`.
The Phase 2D-40 adapter should remain runtime-neutral and independently
testable; a future MainActor wrapper may own it without adding UI framework
imports to the projection layer.

## 38. Actor And Runtime Crossing

The command owner awaits facade and lifecycle actors, obtains a
generation-checked private snapshot through the internal facade seam, then
passes immutable `Sendable` values to the projector. No key-bearing reference
or service object crosses into presentation.

## 39. Snapshot Consistency

Each published snapshot represents one coherent observation: a redacted status
and, only when unlocked, the corresponding current-generation private
projection. The host must not combine status from one operation with private
state from another.

## 40. Stale Snapshot Prevention

The future command owner maintains an operation epoch. After every await it
rejects results whose epoch is no longer current. The facade's own
generation-checked private read is necessary but does not replace the
presentation epoch check before publication.

## 41. Lock-Transition Clearing

On explicit or lifecycle-required lock, the presentation owner first publishes
`locking` with no private projection, invalidates pending projection work, and
then awaits runtime lock. No late result may repopulate private state.

## 42. Activation-Transition Behavior

Activation starts by publishing `activating` with no private projection.
Private projection is produced only after successful activation, a fresh
unlocked status read, and a successful current-generation private-state read.
Every failure and cancellation path stays cleared.

## 43. Save-Progress Behavior

Saving exposes generic progress and never retains the mutation for display,
retry, analytics, or restoration. Completion requires a fresh status and, when
the facade remains unlocked, a fresh private snapshot before projection.

## 44. Cancellation Behavior

Cancellation invalidates the presentation epoch and drops transient input and
pending results. A fixed cancelled transition may be shown before settling to
a newly read redacted runtime status; cancellation never restores an older
private projection.

## 45. Lifecycle-Event Behavior

The platform host, not the adapter, delivers neutral lifecycle events to the
lifecycle coordinator. Presentation should obscure private content immediately
on backgrounding and clear it whenever lock wins, without managing grace timers
or subscribing to platform notifications in this phase.

## 46. No Persistent Presentation Cache

Presentation snapshots live only in process memory for the current adapter
generation. They are not written to disk, encoded, restored after launch, or
used as an alternate private-state source.

## 47. No AppStorage

No presentation status, private projection, credential, selection, or command
input is stored through `AppStorage`. Phase 2D-39 adds no property wrappers.

## 48. No SceneStorage

No private route, selection, projection, status, or command input is stored
through `SceneStorage`. A future scene policy must clear private routes on lock.

## 49. No UserDefaults

The adapter has no `UserDefaults` dependency and does not persist even
apparently non-sensitive vault status, because existence and usage patterns may
themselves reveal private behavior.

## 50. No Analytics Private Payload

Presentation events must not carry vault IDs, record IDs or types, private
counts, saved membership, search text, filters, job keys, notes, snippets,
draft references, credentials, paths, or underlying errors.

## 51. Accessibility Privacy

Public statuses use fixed non-sensitive labels. Private visible values require
a later accessibility review; hidden or obscured content must not remain in
labels, hints, values, announcements, identifiers, or focus state after lock.

## 52. Fake Preview And Test Data

Tests and future previews use clearly fake, non-personal sentinels constructed
in memory. They do not load repository-private inputs, production vaults,
exports, local stores, generated documents, or user histories.

## 53. Future Adapter Tests

Phase 2D-40 should test every status mapping, all five private projections,
side-effect-free construction, locked and failure clearing, stale-input
rejection, save and cancellation transitions, redacted descriptions, absence
of persistence and service calls, public-cache immutability, fake-only data,
strict concurrency, and source guards.

## 54. No SwiftUI Implementation

This design adds no `SwiftUI`, `ObservableObject`, property wrapper, view,
environment key, app host, scene adapter, platform subscription, or app-entry
integration. Those remain behind later review gates.

## 55. Recommended Phase 2D-40

After review and merge, Phase 2D-40 should implement the runtime-neutral,
non-observable projector and fake-state tests. The MainActor observable wrapper,
real views, app-host composition, and unlock UI remain deferred.
