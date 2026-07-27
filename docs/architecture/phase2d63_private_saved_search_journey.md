# Phase 2D-63: Private Saved-Search Journey

## 1. Purpose

Phase 2D-63 introduces the first production private-state presentation after
an authoritative vault unlock. It lists active saved searches and supports
explicit encrypted create and tombstone delete operations.

## 2. Scope

The phase is limited to the exact 14-file repository allowlist. It adds a
separate saved-search feature coordinator and view, a private-session bridge,
host-owned private mutation admission, production composition and root
integration, deterministic tests, and this architecture record.

It does not change Python, shared vectors, encrypted-vault formats, package
manifests, Xcode projects, app entry, process ownership, navigation, runtime
facade, record saver, record hydrator, private payload models, persistence
coordinator, public cache, recovery import, recovery export, or local-vault
creation.

## 3. Reconstructed Phase 2D-62 Baseline

Git and GitHub reconstruction identified Phase 2D-62 as merged through PR #83
at master commit `2bb834581bab5917d496b9eee7fdc566ea956d7f`. Its reviewed head was
`d3581431859c00f40f5db3a1a6c5570fd0ce3057`, its exact 27-file reviewed tree
matches the merged tree, and its Python, Swift, iOS, review, check, and
zero-unresolved-thread gates were clean.

## 4. Existing Hydrated Private State

The runtime already hydrates encrypted records into
`AtlasVaultHydratedState` only while unlocked. The state contains saved
searches, saved jobs, application notes, profile snippets, draft metadata,
and tombstones. Phase 2D-63 reads this already hydrated in-memory state and
does not reopen or decrypt the local store independently.

## 5. Existing Encrypted Mutation Path

`AtlasVaultRuntimeMutationRequest` already applies mutation sets through the
record saver, encrypted local-store merger, and atomic persistence boundary.
A committed save reloads and rehydrates private state before returning. The
new feature delegates to that path and does not implement another saver or
filesystem boundary.

## 6. Existing Private Presentation Types

The reviewed projector types provide
`AtlasVaultPresentationGeneration`, `AtlasVaultPresentationID`,
`AtlasVaultSavedSearchPresentation`, and
`AtlasVaultSavedSearchRequestPresentation`. The phase reuses those UI-safe
types rather than exposing hydrated records.

## 7. Production Private-Free Boundary

`AtlasVaultProductionPresentationPipeline`,
`AtlasVaultProductionPresentationOwner`, `AtlasLockedPublicShellModel`, and
`AtlasPublicLocalSnapshot` remain private-free. No saved-search value is added
to host flow state, public API models, process route state, or the public
cache.

## 8. Separate Private Feature Boundary

One separate `AtlasVaultSavedSearchContext` carries the saved-search owner and
fixed actions. The production root combines this context with the existing
private-free host flow only at `unlockedTransition`.

## 9. Host Private-Session Activation

`AtlasVaultPrivateSessionBoundary` exposes activation, immediate presentation
hide, and stop-and-drain operations. A side-effect-free no-op implementation
preserves compatibility. A one-time attachable bridge lets composition inject
the boundary before host construction and attach the real owner afterward.

## 10. Host Private Mutation Admission

The generic `AtlasVaultProductionHosting` protocol remains unchanged. The
concrete host separately conforms to `AtlasVaultPrivateMutationHosting`.
Admission requires a started, authoritatively unlocked, lifecycle-safe host;
the selected vault must match the request; selection, submit, barrier, stop,
safe-lifecycle checks, and another mutation must all be absent.

## 11. Unlock Ordering

After runtime unlock, the host rechecks runtime status, generation, lifetime,
selection, and lifecycle state, then awaits private-session activation. It
rechecks those fences again before publishing `unlockedTransition`. A failed
or stale activation hides private presentation and enters the existing
private-free reconciliation and runtime-lock barrier.

## 12. Immediate Lock-Time Removal

Every host barrier first calls `hidePrivatePresentation`, invalidates the
private session, cancels and drains an active private mutation, stops the
feature coordinator, and only then invokes runtime lock. Deterministic gates
cover explicit lock, inactivity, background, protected-data loss, terminal
stop, reconciliation, activation supersession, and lock during mutation.

## 13. Fatal Save Containment

Committed-state-unavailable and integrity-unknown mutation failures hide the
presentation and lock fail closed. The host skips externally draining the
currently executing feature task during that exact barrier, preventing the
host from awaiting a task that is awaiting the host. The coordinator
invalidates its mapping and unwinds; harness terminal stop still drains the
owner.

## 14. Presentation Generation

Every successful activation creates a new
`AtlasVaultPresentationGeneration`. Refreshes after a committed mutation reuse
the active generation. A lock or stop invalidates the generation.

## 15. Opaque IDs

Each saved-search presentation ID combines the internal record ID with the
current generation. IDs remain stable within one unlocked session, change
after re-unlock, and cannot be used after their generation is invalidated.

## 16. Saved-Search-Only Projection

The feature iterates only `AtlasVaultHydratedState.savedSearches`, preserves
their active store order, rejects deleted or duplicate active records, and
creates the reviewed saved-search presentation values.

## 17. Other Private Families Excluded

Saved jobs, application notes, profile snippets, draft metadata, and
tombstones are neither copied into the owner nor rendered. A deterministic
mixed-family fixture proves that only the saved search projects.

## 18. Draft Validation

The draft contains only name and search text. Both values trim surrounding
Unicode whitespace. Name requires 1 through 120 Unicode scalars. Search text
permits empty input and is limited to 512 Unicode scalars. Newline and control
characters are rejected, and fixed errors echo no input.

## 19. Canonical Saved-Search Payload

An empty search text becomes `nil`; otherwise the normalized text becomes the
request text and summary. Empty text uses `All open jobs`. The request fixes
status to `open`, all filter arrays to empty, low-confidence inclusion to
false, facets to true, limit to 50, offset to zero, and sort to
`closing_date_asc`. One strict UTC-seconds timestamp is used for all four
initial payload and envelope timestamps.

## 20. Fixed Key Identifier

New saved searches use `primary-local-key-v1`. This fixed nonsemantic key
identifier contains no user value, record type, or vault identity and is never
displayed.

## 21. Create Mutation

Explicit create produces exactly one `AtlasVaultCreateMutation` containing one
saved-search payload. It submits one mutation set through the host-owned
private mutation boundary.

## 22. Delete Mutation

Explicit delete accepts only an opaque presentation ID. The coordinator
resolves the current record ID, revision, and existing key ID from its
internal session mapping and creates exactly one
`AtlasVaultDeleteMutation`.

## 23. Tombstone Behavior

The existing record saver encrypts an empty payload into a deleted envelope.
The resulting ciphertext contains only the authenticated GCM tag and decrypts
to empty data. Hydration returns an internal tombstone, which the feature
requires after commit and never renders.

## 24. No Optimistic UI Mutation

The owner retains the previously committed list while a mutation is active.
It never inserts or removes a row before the host and runtime report a
committed result and the coordinator refreshes runtime state.

## 25. Runtime Refresh After Commit

After committed create or delete, the coordinator reads the already hydrated
runtime state, projects it with the current generation, and verifies the
expected new active record or matching tombstone before publishing.

## 26. Pre-Commit Failure

A fixed pre-commit failure retains the prior committed snapshot and permits a
later explicit retry. No optimistic state needs rollback.

## 27. Durability-Unconfirmed Result

A durability-unconfirmed commit refreshes and publishes the committed list
with a fixed warning. The feature does not repeat the mutation and disables
further create or delete operations for that unlocked session. Explicit lock
remains available.

## 28. Cancellation

Cancellation publishes no optimistic change. Lock and stop cancel retained
feature and host operations, drain them structurally, and fence late
completions by operation identity, host generation, owner revision, and
session revision.

## 29. Stale ID Behavior

An unknown or old-generation presentation ID returns fixed stale-item failure
without calling the host or runtime and without creating a mutation.

## 30. Owner Construction

The `@MainActor` presentation owner is constructed hidden with an empty list.
Construction creates no task and invokes no coordinator dependency.

## 31. Owner Activation

Activation clears prior items, publishes loading, retains one operation, and
awaits coordinator activation. Only a completion matching the current owner
revision may publish ready. Failure publishes no private list and returns
false to the host.

## 32. Owner Mutation Serialization

Only one owner mutation may run at a time. Saving retains committed items and
disables create and delete. Operation tasks are retained independently of the
calling view task.

## 33. Owner Immediate Hide

Immediate hide synchronously increments the owner revision, cancels retained
UI operations, replaces items with an empty array, and publishes hidden or
locking. Stop then awaits retained work, stops the coordinator, and leaves the
owner hidden.

## 34. SwiftUI Saved-Search List

The unlocked view displays `Saved Searches`, a fixed unlocked indicator, the
active list or fixed empty state, save progress and fixed failures, and an
explicit lock action. Rows display only name, summary, optional query text,
and optional safe timestamps.

## 35. Create Sheet

The sheet opens only after explicit Add. Name, search text, and visibility are
scene-local `@State`, not owner state. Save captures a draft, clears local
fields, dismisses the sheet, and then invokes the action. Cancel, lock, owner
hide, and disappearance clear local values.

## 36. Delete Confirmation

Delete starts from an explicit row action and requires a confirmation dialog.
The dialog may display the saved-search name, but the action receives only the
opaque presentation ID.

## 37. Explicit Lock

The view synchronously clears local fields and calls owner locking before
awaiting the host lock action. It does not rely on a later public-flow
observation to remove private values.

## 38. Shared Multi-Window Authority

Production composition creates one coordinator, one owner, and one action
context. Multiple roots share that authority while each SwiftUI scene retains
its own sheet fields and delete confirmation.

## 39. Production Harness Assembly

Composition builds the bridge before the host, injects it, constructs the
host, requires the separate mutation conformance, creates the coordinator and
owner, attaches exactly once, and retains one context. Construction performs
no private-state read, mutation, store write, Keychain operation, filesystem
operation, network request, task creation, or owner activation.

## 40. Production Root Integration

The all-optional designated root initializer accepts an optional saved-search
context. At `unlockedTransition`, a present context renders the saved-search
view. An absent context retains the historical unlock-complete placeholder.
The root constructs no service and performs no private read or mutation.

## 41. Recovery/Export Coexistence

The recovery/export wrapper remains active around the base flow, so its
explicit action remains available while the saved-search view is visible.

## 42. Import/Create Coexistence

Recovery import and local-vault creation wrappers continue to own only the
locked/no-vault experience. Their contexts and compatibility initializers are
preserved, and wrapper order passes the saved-search context through without
replacing the unlocked private view.

## 43. Public Search Preservation

Locked public search remains unchanged. The saved-search feature makes no
network request and does not invoke the compatibility saved-search HTTP
endpoint.

## 44. Public-Cache Immutability

Create and delete never call `AtlasLocalCache`, `UserDefaults`, or a public
snapshot writer. End-to-end coverage compares the public shell before and
after private mutation.

## 45. Encrypted Persistence Verification

The production-like end-to-end test reads canonical local-store bytes and
proves they contain no saved-search name, query, summary, record type, or
private payload field text. It verifies nonempty ciphertext for an active
record and tag-only encrypted empty plaintext for a tombstone.

## 46. Local-Key End-to-End

The journey creates an empty local vault, explicitly unlocks by local key,
waits for an empty ready saved-search owner, explicitly creates one saved
search, refreshes it from runtime state, locks, relaunches, and restores it.

## 47. Recovery-Key End-to-End

The journey configures recovery, creates a saved search, removes the
device-local Keychain key, explicitly unlocks by recovery key, restores the
saved-search list, and performs an encrypted delete in that supplied-key
runtime session.

## 48. Lock/Relaunch Behavior

Lock clears the owner before a gated runtime lock returns. Relaunch and
explicit unlock create a different opaque generation and restore only
committed active saved searches. Deleted records remain absent while their
encrypted tombstones persist.

## 49. Privacy And Redaction

Errors, mutation outcomes, drafts, snapshots, environments, coordinators,
owners, bridges, and contexts expose fixed descriptions or no descriptions.
No private sentinel, identifier, revision, key ID, vault ID, path, or key is
logged.

## 50. No Private Metadata Rendering

Record IDs, revisions, parent revisions, key IDs, tombstones, encrypted
envelopes, vault IDs, local paths, and cryptographic keys remain internal and
cannot be rendered by the saved-search view.

## 51. No Saved-Job/Note/Snippet/Draft Rendering

Saved jobs, application notes, profile snippets, draft metadata, and
generated-document references remain outside the private context and view.
No counts or labels for those families are published.

## 52. No Cloud Or Migration

The feature adds no cloud sync, legacy plaintext migration, passphrase
support, LocalAuthentication, biometrics, or generated-document rendering.

## 53. Merge-Stable Test Policy

Tests assert permanent behavior and source boundaries. They contain no
`origin/master...HEAD` comparison, hard-coded Git SHA, current-branch blob
pin, or current-phase tree requirement.

## 54. TDD Evidence

The red checkpoint
`0025254973197f419bbf164db6dbd74eeb830278` was committed and pushed before
production implementation. It proved the private-session boundary,
host-owned mutation boundary, coordinator, owner, view, production context,
unlocked root route, and end-to-end journey were absent.

## 55. Test Coverage

Deterministic suites cover construction, bridge attachment, activation and
supersession, narrow mixed-family projection, opaque generations, strict
drafts and timestamps, exact create payload, internal delete metadata,
tombstones, all save outcomes, no optimistic mutation, mutation
serialization, fatal containment, immediate explicit/lifecycle/terminal
hide, stale completions, root and harness compatibility, encrypted storage,
local-key relaunch, deletion relaunch, and recovery-only mutation.

## 56. iOS Build And Smoke Evidence

The baseline `AtlasApple` and `AtlasIOSHost` generic iOS Simulator builds
passed before implementation. Both generic iOS Simulator builds also passed
after implementation. No simulator was booted for an app-launch smoke, so the
deterministic in-process end-to-end coverage is authoritative for create and
delete.

## 57. Go/No-Go

- first production private presentation: implemented;
- saved-search list: implemented;
- saved-search encrypted create: implemented;
- saved-search encrypted delete/tombstone: implemented;
- immediate lock-time UI clearing: implemented;
- local-key session: implemented;
- recovery-key session: implemented;
- saved-search update/rename: not implemented;
- saved-search execution: not implemented;
- saved jobs: not rendered;
- notes/snippets/drafts: not rendered;
- cloud sync: not implemented;
- migration: not implemented;
- production readiness: not claimed.

## 58. Deferred Work

Saved-search execution, edit, rename, public-search handoff, saved jobs, notes,
snippets, drafts, generated-document rendering, cloud sync, migration,
passphrase unlock, LocalAuthentication, and biometrics remain deferred.

## 59. Next Product Gate

Phase 2D-64 must implement saved-search execution, edit/rename, and safe
handoff into the public-search surface while preserving the immediate
lock-time private-state boundary. Saved jobs and all other private record
families remain deferred.
