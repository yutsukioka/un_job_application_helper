# Phase 2D-58: iOS Lifecycle and Entry Integration Plan

## 1. Purpose

Phase 2D-58 supplies the concrete iOS lifecycle boundary needed by the
runtime-neutral production composition while keeping actual application entry
unchanged.

## 2. Phase Scope

This phase implements a pure process lifecycle reducer, a concrete iOS event
source, and a fail-closed lazy entry-integration plan. It does not wire the
plan into the application, alter navigation, or render private state.

## 3. Reconstructed Phase 2D-57 Baseline

Phase 2D-57 merged one neutral lifecycle source protocol, retained serial
forwarder, MainActor presentation owner, production-like composition harness,
and unwired public root. Its reviewed merge is the direct baseline for this
phase. The Phase 2D-57 lifecycle-readiness follow-up then replaced stream-only
readiness with an explicit bootstrap-plus-live subscription and a serialized
readiness boundary.

## 4. Existing Neutral Lifecycle Protocol

`AtlasVaultPlatformLifecycleEventSourcing.subscription()` returns
`AtlasVaultPlatformLifecycleEventSubscription`. The subscription separates an
ordered `bootstrapEvents` array from a lossless stream of
`AtlasVaultPlatformLifecycleEventDelivery` values and exposes an explicit
readiness-boundary request. There is no stream-only compatibility API or
protocol default that silently declares an empty bootstrap.

## 5. Concrete iOS Process Source

`AtlasIOSProcessLifecycleEventSource` is one process-scoped, single-use event
source. It translates system observation through the pure reducer and exposes
only neutral host lifecycle events. Snapshot-derived bootstrap events are
returned separately; subsequent events and readiness boundaries use the live
delivery stream.

## 6. Construction Side Effects

Source construction stores an observation driver only. It installs no
observer, reads no application or scene state, and starts no task.

## 7. UIKit Observation Boundary

UIKit access is isolated to a MainActor observation driver. The reducer and
entry route have no UIKit dependency.

## 8. SDK and Availability Policy

The build uses Xcode 26.6 and iOS SDK 26.5, while the package minimum remains
iOS 18. Typed lifecycle messages in this SDK require iOS 26, so the driver uses
notification-name APIs available at the deployment minimum. No availability
crash, target increase, or unsafe concurrency annotation is introduced.

The iOS 18 fallback uses retained selector-registration objects whose callback
methods are MainActor isolated. UIKit lifecycle delivery remains synchronous
and ordered without a non-Sendable block transfer, unchecked wrapper, or
untracked task per notification.

## 9. Initial Observer-Registration Ordering

The driver registers every notification observer before capturing initial
state. Registration and capture occur synchronously in one MainActor turn, so
callbacks cannot interleave with capture and any queued live signal follows
the captured snapshot. Notification callbacks and readiness-boundary requests
then enter the same MainActor-owned internal delivery continuation.

## 10. Initial Scene Snapshot

At explicit subscription, the driver maps all connected scenes to opaque
session identifiers and neutral activation states.

## 11. Application Fallback Snapshot

The initial application state is captured for use only when no connected
scene record exists.

## 12. Protected-Data Bootstrap

The driver captures protected-data availability during the same explicit
begin operation. No protected-data state is read at source construction.

## 13. Bootstrap Event Ordering

The reducer emits protected-data availability first and process phase second.
The source returns that array without repeating it in the live stream. The
Phase 2D-57 forwarder awaits both events before requesting and draining the
readiness boundary, so host startup cannot overtake an initial inactive or
background phase.

## 14. Scene Identity Privacy

Only `UISceneSession.persistentIdentifier` is retained as an opaque dictionary
key. Identifiers, roles, titles, and scene counts are absent from diagnostics
and public output.

## 15. Scene-State Mapping

Foreground-active, foreground-inactive, background, and unattached UIKit
states map to matching neutral reducer states. Unattached is treated as
background-safe for aggregate decisions.

## 16. Multi-Scene Aggregate Model

The reducer derives exactly one process phase from the complete scene map.
Individual notifications mutate scene state; they do not directly declare a
process transition.

## 17. Active-Scene Policy

At least one foreground-active scene makes the process active. Additional
active scenes and duplicate notifications emit no duplicate active event.

## 18. Inactive-Scene Policy

The process becomes inactive only when no active scene remains and at least
one foreground-inactive scene remains. Active-to-inactive emits
`willResignActive` once.

## 19. Background Policy

The process becomes background only when tracked scenes contain neither an
active nor an inactive foreground scene. One window cannot background the
process while another remains foreground.

## 20. Foreground-Entry Policy

Background-to-foreground-inactive emits no false resign-active event. A later
activation emits `didBecomeActive`.

## 21. Scene Connection

A connection records the scene's current mapped state and recomputes the
aggregate. Repeated connection notifications are idempotent.

## 22. Scene Disconnection

A disconnection removes only that scene, then derives phase from remaining
scenes or the application fallback when none remain.

## 23. Application Fallback Behavior

Application active, inactive, and background signals continuously update the
fallback. They cannot override a nonempty scene aggregate.

## 24. Duplicate Suppression

The reducer emits only aggregate or protected-data transitions. Repeated raw
signals that leave effective state unchanged produce no neutral event.

## 25. Protected-Data Events

Available and unavailable transitions are emitted once per state change and
remain independent of scene aggregation.

## 26. Termination Event

The first termination signal emits `willTerminate`, makes the reducer
terminal, and causes the source to finish. All later signals are ignored.

## 27. Source Single-Subscription Policy

The first `subscription()` call starts the one process observation. Every
later call explicitly returns empty bootstrap events, an immediately finished
delivery stream, and a no-op boundary request; it cannot install another
observer or producer.

## 28. Source Task Ownership

The source retains one producer task from creation through terminal cleanup.
That task serially consumes one internal stream containing both raw system
signals and readiness-boundary markers. It reduces signals to `.event` values
and forwards boundary UUIDs unchanged. Consumer cancellation synchronously
cancels that retained producer, whose own terminal path completes cleanup.

## 29. Observation-Token Cleanup

The MainActor driver retains every observer token. Producer cancellation,
input completion, and termination all call the idempotent stop path, remove
all tokens, and finish the ordered internal delivery continuation exactly
once.

## 30. Lossless Lifecycle Buffering Policy

Both internal and public streams use lossless unbounded buffering for one
low-frequency, process-lifetime subscription. Raw UIKit signals and readiness
markers share the internal stream: a signal inserted before the MainActor
boundary request is reduced and delivered before the marker, while a later
signal remains after it. The public request closure delegates to that observer
channel and never yields a marker directly, so a marker cannot overtake
already-buffered system input. No one-slot buffer may discard termination or
protected-data events; terminal cleanup bounds the buffer lifetime.

## 31. App-Entry Route Model

`AtlasIOSAppEntryRoute` distinguishes recognized reference capture, invalid
reference capture, and production.

## 32. Valid Reference Capture

Every recognized `AtlasReferenceCaptureMode` selects the reference route and
makes the production factory unreachable.

## 33. Invalid Reference Capture

A present but empty or unknown capture value selects an invalid route. It
fails closed and cannot fall through to production construction.

## 34. Production Route

Only absence of the capture environment key selects production. Route parsing
uses the explicitly supplied environment dictionary.

## 35. Fail-Closed Environment Policy

The plan never reads process-global environment state. Its caller must supply
the environment after deciding the future process boundary.

## 36. Lazy Production Construction

Plan initialization stores a closure but invokes nothing. Production source
and harness construction occur only through an explicit production request.

## 37. One Process Harness

The plan caches one successful harness. Repeated requests return the same
identity; a failed request caches one fixed redacted terminal failure rather
than retrying automatically.

## 38. Multi-Window Shared Authority

Future windows must ask the one harness for roots. They must not construct
per-window lifecycle sources, hosts, runtimes, or owners.

## 39. Historical App-Entry Non-Modification

The Phase 2D-58 introduction commit did not modify `AtlasIOSHostApp.swift` or
`AtlasReferenceCaptureView.swift`. That scoped historical claim is verified
from the first commit that added the lifecycle aggregator and its first parent;
the tests do not constrain later reviewed app-entry phases to the old source.

## 40. Future App-Entry Integration

A later phase will evaluate the route first, create one process owner for the
production route, and render roots from its shared harness.

## 41. Reference-Capture Isolation

Recognized and invalid capture routes construct no lifecycle source,
composition, Keychain adapter, filesystem adapter, or network adapter.

## 42. Future Harness Start Ownership

The future process-level app owner must explicitly start the retained harness.
No window or root appearance may start it automatically.

## 43. Future Harness Terminal Stop Ownership

The same process owner must invoke terminal stop and await the existing host
and lifecycle-forwarder drain policy.

## 44. No Private Rendering

This phase adds no view and exposes no private hydrated state. The future
production route remains limited to the public locked-shell flow.

## 45. No Navigation Change

No navigation stack, route, app root, or existing search interface is changed.

## 46. No macOS Lifecycle Implementation

The concrete observation driver is iOS-only. Any future macOS implementation
must separately conform to the same neutral source protocol.

## 47. Error and Diagnostic Redaction

Reducer, source, route, and plan descriptions are fixed. Production failures
map to one fixed error without environment values, scene state, identifiers,
paths, URLs, or dependency details.

## 48. TDD Evidence

The three focused suites were committed in a valid red state before production
source existed. Their failures were missing reducer, source, and entry-plan
types rather than malformed tests. After the readiness follow-up merged, the
rebased source and fake conformers produced a second valid red state because
the removed `events()` API no longer satisfied `subscription()`. The
adaptation tests were written before the concrete source change.

## 49. Test Coverage

Deterministic tests cover bootstrap order, multi-window transitions,
connection and disconnection, fallback behavior, protected data, termination,
single subscription, cancellation drain, observer boundaries, fail-closed
route selection, lazy identity reuse, and historical app-entry isolation.
Source integration tests additionally prove separate bootstrap delivery,
protected-data-first active/inactive/background readiness, immediate and burst
signals before a matching boundary, later signals after it, pre-boundary
termination, and explicit finished second subscriptions.

The merge-stability follow-up dynamically finds the first commit that added
`AtlasIOSLifecycleAggregation.swift`, resolves its first parent, and verifies
that the historical parent-to-introduction diff contains exactly the seven
reviewed Phase 2D-58 files. It separately verifies that app entry and reference
capture were absent from that diff. Permanent source-boundary checks remain on
the app-entry plan itself, while current worktree checks continue to reject
`.atlasvault` and `.venv-review` artifacts. No current-branch allowlist,
current app-entry shape, or hard-coded blob identity is treated as a permanent
Phase 2D-58 invariant. Git-backed checks throw a fixed skip on unsupported
platforms rather than returning fabricated output. A shallow checkout also
uses a fixed skip because the introduction commit's parent is unavailable;
with complete history, a missing introduction commit or parent remains a hard
failure.

## 50. Go/No-Go Update

- Neutral lifecycle source protocol: implemented previously and strengthened
  by the merged Phase 2D-57 readiness follow-up.
- Pure iOS scene aggregator: implemented.
- Concrete iOS lifecycle source: implemented with explicit bootstrap and
  ordered live-delivery readiness boundaries.
- UIKit notification observation: implemented.
- Multi-scene aggregation: implemented.
- Protected-data delivery: implemented.
- Termination delivery: implemented.
- App-entry route plan: implemented.
- Reference-capture construction guard: implemented and tested.
- One-process lazy harness plan: implemented.
- Actual app-entry wiring: not implemented.
- Actual normal-route replacement: blocked.
- Private rendering: blocked.
- Passphrase/recovery: blocked.
- Production launch: blocked.

## 51. Deferred Work

Actual app-entry ownership, route rendering, explicit process start/stop, and
multi-window root distribution remain deferred. No private rendering,
passphrase/recovery provider, migration, cloud behavior, or production
navigation belongs to this phase. The prior Phase 2D-57 readiness blocker is
resolved by the merged follow-up, this concrete-source adaptation, and the
direct source-forwarder integration regressions.

## 52. Next Product Gate

Phase 2D-59 must implement actual iOS app-entry integration. It must evaluate
the route before production construction, preserve recognized capture modes,
fail closed for invalid capture values, retain one production harness, render
`AtlasVaultProductionRootView` for production, explicitly own start and stop,
share authority across windows, keep private rendering absent, and retain
local key as the only production unlock method.
