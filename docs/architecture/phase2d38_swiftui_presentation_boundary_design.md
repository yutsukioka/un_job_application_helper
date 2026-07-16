# Phase 2D-38 AtlasVault SwiftUI Presentation Boundary Design

## 1. Purpose

Phase 2D-38 defines how future presentation code may consume AtlasVault
runtime capabilities without exposing keys, decrypted payloads, or storage and
cryptographic services to views.

## 2. Design-Only Status

This phase adds documentation only. It adds no SwiftUI source, observable
model, app-entry wiring, vault operation, platform lifecycle subscription,
migration, cloud sync, recovery flow, onboarding, or key rotation. It does not
claim production readiness.

## 3. Runtime Facade As Sole Vault-Facing UI Boundary

All UI-originated vault commands must pass through
`AtlasVaultRuntimeFacading`. A future presentation adapter may use the
module-internal private-state reading capability implemented by that same
facade, but it must not reach around the facade to the activation controller,
private-state store, persistence coordinator, or runtime composition graph.

## 4. No Direct Keychain Access From Views

Views and presentation models must not instantiate or call
`AtlasVaultKeyStore`, `AtlasKeychainVaultKeyStore`, `SecItem`, or related
clients. Key availability is represented only by a non-sensitive presentation
state returned from the reviewed runtime boundary.

## 5. No Direct Filesystem Access From Views

Views must not resolve roots, construct vault paths, prepare directories, read
local stores, or invoke atomic writers. Paths and filenames never enter view
state, navigation, errors, accessibility output, or previews.

## 6. No Direct Crypto Calls From Views

Views and presentation adapters must not derive keys, select nonces, seal or
open records, or inspect encrypted envelopes. Cryptographic operations remain
inside the runtime service graph.

## 7. No Direct Hydrator Or Saver Access From Views

Record hydrators, record savers, local-store mergers, and persistence
coordinators are not UI dependencies. The facade owns complete activation and
save transactions and returns only redacted status or save outcomes.

## 8. Proposed Presentation Model

A future `AtlasVaultPresentationModel` should be a host-injected `@MainActor`
adapter over the facade. It owns UI-safe status, command task handles, and at
most one temporary private display snapshot while unlocked; it is not a
service locator or global singleton.

## 9. Proposed UI-Safe Public Status

Use a presentation enum with stable categories only: no-vault, locked,
activating, unlocked, saving, locking, and redacted failure categories.
Transient command outcomes such as cancellation do not become stable status.
The enum must contain no vault ID, key state detail, path, record ID, record
type, private count, saved membership, user text, or underlying error.

## 10. Locked State

Locked state exposes public job-search functionality and an explicit activate
command. It exposes no private collections, prior private navigation target,
or indication that a particular public job was saved.

## 11. No-Vault State

An explicit activation result equivalent to `storeMissing` may map to a
non-sensitive no-vault state. It is distinct from a missing key and must not
create a store, start migration, or imply that a vault exists at a disclosed
path.

## 12. Activating State

Activating disables duplicate activation and mutation commands and presents a
generic cancellable progress state. No partially hydrated record, selected key
source, vault path, or activation stage is observable.

## 13. Unlocked State

Unlocked is published only after activation and complete private-state
installation succeed. A future adapter may then obtain one generation-checked
private snapshot for display; it must reject stale results after lock or a
newer operation.

## 14. Non-Sensitive Failure State

Failures map to a finite UI category and generic recovery action. Underlying
errors, identifiers, paths, payload types, record counts, private values, and
operation details are never retained or interpolated.

## 15. Key-Unavailable State

`keyUnavailable`, key-store failure, and invalid supplied-key results may map
to reviewed, non-sensitive key-unavailable variants. They must not reveal
whether a particular Keychain account exists or which credential bytes failed.

## 16. Corrupt-Vault State

Authentication failure, corrupt store, and unsupported version remain
distinct internal categories but may share a generic corrupt-or-unreadable UI
state. No ciphertext, decoded fragment, schema detail, path, or partial private
state is exposed.

## 17. Cancellation State

User cancellation is a transient command outcome, not persisted status. It
must discard credential input, reject late task results, and settle into the
latest redacted facade status without displaying an underlying cancellation
error.

## 18. Save-In-Progress State

Saving disables overlapping private mutations and exposes only generic
progress. The presentation layer must not retain the mutation payload for
diagnostics, analytics, restoration, or retries after the command completes.

## 19. Save-Failed State

Pre-commit save failure leaves the prior displayed generation intact only when
the facade remains unlocked. Commit-aware failure clears private display state
and follows the facade to locked; the UI never guesses whether plaintext or
encrypted data changed.

## 20. Lock Action

Lock is explicit, idempotent, and available from every screen. The adapter
first obscures and clears its private display copy, cancels presentation tasks,
then awaits `facade.lock()`, performs a follow-up `facade.status()` read, and
publishes only that redacted post-lock status.

## 21. Activate Action

Activation is initiated only by a deliberate user action. The adapter creates
a one-shot request, submits it through `facade.activate(_:)`, discards transient
input, and reads the redacted facade status. Only after that status reports
successful unlock may it request a private-state snapshot from the facade.

## 22. User-Entered Passphrase Handling

A future credential component may accept a passphrase through a transient
secure-input binding and hand it to a reviewed unwrap operation. It must not
place the passphrase in the presentation status, model description, task name,
error, log, analytics event, pasteboard, or navigation value.

## 23. Passphrase Must Not Be Retained In Observable State

The observable presentation model must never store a passphrase. A secure
input owner should clear its binding immediately after submission or
cancellation and acknowledge that Swift `String` storage cannot provide a
guaranteed memory wipe.

## 24. Recovery-Key Handling

Recovery material follows the same one-shot, non-observable handling as a
passphrase and must never be formatted into diagnostic output. Recovery UX,
validation, persistence, and account policy remain separately reviewed future
work.

## 25. No Automatic Unlock

Construction, launch, foregrounding, protected-data availability, preview
creation, and public navigation must not call `activate`. Keychain presence is
not permission to unlock automatically.

## 26. No Private Data In Public Status

Public presentation status contains no private record values or counts and no
saved-only membership. Even boolean hints such as has-saved-jobs or has-notes
are private and belong only to the unlocked display boundary.

## 27. Private State Display Boundary

Private display data enters presentation memory only through a
generation-checked, module-internal facade capability after unlock. It is
never added to the public facade protocol, public status, public snapshot, or a
general application environment value.

## 28. Saved-Search Display

Saved-search names, query text, filters, revisions, and identifiers are shown
only in unlocked private views. List rows receive the minimum fields needed for
the current render and clear them on lock.

## 29. Saved-Job Display

Saved-job membership and private job keys remain private even when the public
job itself is cached. A private view may join unlocked membership to public
job data in memory, but that join must not warm or annotate the public cache.

## 30. Notes, Snippet, And Draft Display

Application notes, profile snippets, draft metadata, statuses, and generated
document references are private display values. They must not enter public
search models, persistent navigation, previews derived from real state, or
background restoration.

## 31. Private Data Lifetime In View Models

Keep one process-scoped presentation copy only while unlocked and only for the
current generation. Child views receive narrow values rather than retaining
independent caches; lock, activation replacement, teardown, and security
events clear every presentation-owned copy.

## 32. Screen Capture And Background Privacy Considerations

Future platform wiring should obscure private content before background
snapshots and evaluate platform capture-detection APIs separately. Obscuring
is required even during a runtime grace period and does not replace locking or
state clearing.

## 33. Navigation Away And Lock Behavior

Ordinary navigation may preserve private state only while the same unlocked
generation remains active. Explicit lock or lifecycle-required lock clears
private routes and selection before the locked UI appears; no private route is
restored after reactivation.

## 34. Multiple-Window Behavior

All windows observing one facade share one process-level lock state and must
clear private presentation copies together. Window IDs, selected records, and
activity must not become vault metadata; duplicate per-window private caches
should be avoided.

## 35. Public Search Remains Usable While Locked

Public search, public job details, and public cache refresh remain available
while the vault is locked. Their behavior must not trigger activation or infer
private membership.

## 36. Public Snapshot Remains Separate

`AtlasPublicLocalSnapshot` remains a public-only persistence boundary. A
presentation adapter must never serialize hydrated state, private UI state,
private navigation, credentials, or vault status into it.

## 37. No Saved-Only Membership In Public Cache

Public cache entries must not gain saved flags, saved-only keys, private
counts, note presence, or warmup behavior derived solely from unlocked state.
Joining public and private values is ephemeral and in memory only.

## 38. Error Redaction

Presentation errors use fixed categories and fixed user-facing copy. They do
not include `localizedDescription`, reflected requests, underlying errors,
paths, identifiers, payload values, record types, or private counts.

## 39. Accessibility Labels Must Not Expose Secrets

Accessibility labels, hints, values, announcements, and identifiers must not
repeat credentials, notes, snippets, search text, generated-document
references, or hidden private content. Private visible content requires an
explicit accessibility privacy review before implementation.

## 40. Analytics Must Not Contain Private Payloads

Analytics may record only separately approved coarse events and redacted
categories. It must not include private payloads, vault IDs, record IDs,
saved-job membership, private counts, credentials, paths, or user-entered
text.

## 41. Crash Diagnostics Must Not Contain Private Payloads

Crash breadcrumbs, signposts, task labels, assertions, and reflected model
output must use fixed redacted values. Private state and credentials must not
be attached to crash reports or diagnostic context.

## 42. Future View-Model Protocol

A future module-internal `AtlasVaultPresentationModeling` protocol should
expose UI-safe status plus explicit activate, cancel, lock, refresh private
display, and apply-mutation commands. It must not expose service objects,
sessions, keys, paths, envelopes, or a Codable private snapshot.

## 43. Future Observable Adapter

An observable adapter may translate asynchronous facade status into UI-safe
state and hold the temporary private display projection. Observation must not
write to `AppStorage`, `SceneStorage`, `UserDefaults`, public cache, persistent
navigation state, analytics events, or any other restoration mechanism.

## 44. MainActor Boundary

Presentation mutation and publication belong on `MainActor`. Facade calls
cross to actor-isolated runtime code with `Sendable` request and result values;
no key-bearing or private-state reference may escape through an unstructured
closure.

## 45. Cancellation And Task Lifetime

The adapter owns explicit task handles per UI command, rejects overlapping
commands according to facade policy, and cancels them on lock or teardown.
Every continuation rechecks an adapter generation before publishing status or
private display data.

## 46. Lifecycle Coordinator Interaction

The platform host, not a view, will eventually translate lifecycle events for
`AtlasVaultLifecycleCoordinator`. Presentation code observes redacted facade
and lifecycle status, immediately obscures private content when backgrounded,
and clears its copy when lock wins; it never cancels a grace timer directly.

## 47. Test Strategy

Phase 2D-39 tests should inject fake facade, private-reader, lifecycle, and
clock seams. Cover state mapping, explicit activation, no automatic unlock,
credential disposal, stale-task rejection, lock clearing, commit-aware save
failure, multiple observers, MainActor publication, redaction, public-cache
isolation, and source guards for persistence and crypto coupling.

## 48. Preview Strategy Using Fake Data Only

Previews may construct local fake presentation states with unmistakably fake
values. They must not activate a vault, read host storage or Keychain, import
real fixtures, use production keys, or persist preview state.

## 49. No Production SwiftUI Implementation Yet

No view, property wrapper, environment key, observable adapter, app entry
point, platform lifecycle bridge, or production call site is added in this
phase. This document requires review before any such implementation.

## 50. Deferred Work

Deferred work includes SwiftUI implementation, app-launch wiring, UI prompts,
screen-capture hardening, migration and plaintext cleanup, cloud sync,
conflict UI, recovery UX, device onboarding, key rotation, and production
readiness validation.

## 51. Recommended Phase 2D-39

Implement a test-only presentation adapter protocol and fake-state tests under
`MainActor`, still without production SwiftUI views, app launch integration,
platform lifecycle subscriptions, migration, or cloud sync.
