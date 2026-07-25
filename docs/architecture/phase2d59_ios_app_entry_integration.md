# Phase 2D-59: Actual iOS App-Entry Integration

## 1. Purpose

Phase 2D-59 connects the reviewed AtlasVault iOS route plan, lifecycle source,
composition harness, and public production root to the real SwiftUI `@main`
entry point.

## 2. Scope

This phase adds process ownership, delegate-driven start and stop
orchestration, fail-closed route rendering, and the actual app-entry
connection. It does not add private rendering, vault creation, migration,
cloud behavior, passphrase support, recovery support, or navigation changes.

## 3. Reconstructed Phase 2D-58 and Merge-Stability Baseline

Phase 2D-58 was squash-merged by PR #75 as
`96954a5c928e29e547eeb6334cb4f124a6c3f8b2`. It supplied the route plan,
concrete iOS lifecycle source, protected-data-first bootstrap ordering,
multi-scene aggregation, and readiness boundary.

Phase 2D-58F was squash-merged by PR #77 as
`2083f23bd74adbbd43077e8152fbfe11a2201c80`. It made the route-plan scope
tests historical and merge-stable.

Phase 2D-57F2 was squash-merged by PR #78 as
`fab436464f0d9ec371826ad71dba5cb9d3c03b4b`. It made the production-root
scope tests historical and merge-stable without weakening the root's
permanent public-only boundary.

Phase 2D-56F was squash-merged by PR #79 as
`5fff34326ef19b8549ea344e58140468da4df250`. It made the production-host
factory scope test historical and merge-stable while retaining separate
current-tree artifact scanning.

## 4. Recovery and Master Integration

The existing red commit and dirty seven-file implementation were preserved
before integrating Phase 2D-56F. Recovery evidence includes a verified Git
bundle, tracked and staged binary patches, a byte-for-byte seven-file
snapshot, and a SHA-256 manifest. Both the original recovery patch and the
new patch-plus-snapshot path independently reproduced the saved manifest from
the old red head.

The implementation was then stashed without dropping its recovery record, the
single red commit was rebased onto current `origin/master`, and all three
repaired historical suites passed before the implementation was restored.
The rebased red tests still failed only for the intended missing process
owner, integrated root, and app-entry wiring. Applying the retained stash
produced no conflict, and every restored file matched its pre-rebase hash.

## 5. Previous Legacy App-Entry Route

Before this phase, `AtlasIOSHostApp` read the reference-capture environment in
its body. Recognized capture values rendered `AtlasReferenceCaptureView`, but
absent, empty, and unknown values all reached `AtlasRootView`. The app owned no
production lifecycle source, harness, or process-level start and stop
authority.

## 6. Route-Plan Reuse

`AtlasIOSAppEntryIntegrationPlan` remains the only parser for
`ATLAS_REFERENCE_CAPTURE`. The process owner consumes its route but owns a
separate one-shot production factory. It does not duplicate capture-mode
parsing, and it does not call the plan's caching harness accessor. This keeps
an independently retained route plan from becoming a second harness owner.

## 7. Process Delegate

One private, MainActor-isolated `UIApplicationDelegate` is defined beside the
SwiftUI app. It owns the single `AtlasIOSAppProcessOwner` for the process
lifetime.

## 8. Process Owner

`AtlasIOSAppProcessOwner` publishes only a redacted presentation state. It
retains the lazy production authority, one harness, one startup operation, and
one terminal stop operation.

## 9. Process-Owner Non-Responsibilities

The owner does not render views, select or unlock a vault automatically,
perform public searches, expose private models, or replace existing host,
runtime, lifecycle, or presentation responsibilities.

## 10. Process Presentation States

The public presentation states are valid reference capture, invalid reference
capture, production pending, production starting, production ready,
production unavailable, production stopping, and stopped. No state carries an
environment value, URL, error, harness, task identifier, vault identifier,
key, path, or private payload.

## 11. Valid Reference Route

A recognized capture mode is observable immediately. Start and stop entry
points do not invoke the production factory for this route.

## 12. Invalid Reference Route

An empty or unknown capture value produces a fixed invalid-reference state.
The raw value is discarded, and the route cannot fall through to production.

## 13. Production Route

Only absence of `ATLAS_REFERENCE_CAPTURE` selects production. Production
begins pending and moves through the retained startup operation.

## 14. Fail-Closed Route Ordering

The delegate captures the environment once, the route plan resolves the route,
and only then does `didFinishLaunching` request startup. Valid and invalid
capture routes never invoke the lazy production closure.

## 15. Lazy Production Construction

Process-owner initialization constructs no lifecycle source, configuration,
adapter, restorer, selection registry, runtime, coordinator, Keychain client,
filesystem client, host, or harness. The production closure constructs those
objects only when explicit startup reaches the production route.

## 16. API Environment Key

The optional production API origin is read from `ATLAS_API_BASE_URL` inside
the lazy production closure.

## 17. Loopback Default

When the API environment key is absent, the explicit origin is
`http://127.0.0.1:8765`.

## 18. Physical-Device Limitation

Loopback public search is suitable for simulator and local-service
development. It does not reach a service running on another machine from a
physical iOS device. A reviewed user-facing endpoint configuration channel is
deferred.

## 19. No UserDefaults Configuration

The integration does not read `UserDefaults`, call
`AtlasAPIClient.defaultBaseURL()`, construct a zero-argument API client, or
restore the former physical-device LAN fallback.

## 20. Production Configuration

The lazy closure creates
`AtlasVaultProductionCompositionConfiguration` with the resolved URL, public
search limit 50, unlock timeout 30 seconds, immediate lifecycle locking, and
lock-on-inactive enabled. Existing configuration validation remains the final
origin authority.

## 21. Lifecycle Policy

The process uses `AtlasIOSProcessLifecycleEventSource`, preserving
protected-data-first bootstrap, aggregate active/inactive/background state,
the readiness boundary, lossless live delivery, and termination handling.

## 22. Begin-Start

`beginStart()` is synchronous and returns immediately. For production it
installs one owner-retained task; for reference routes it performs no
production work.

## 23. Retained Startup

The owner retains startup independently of any caller waiting for `start()`.
Caller cancellation therefore cannot orphan the process operation. The lazy
factory is consumed exactly once and released after transferring the harness.
The route plan is not used as a harness cache, so retaining it independently
cannot retain or later reissue the process harness. Stop-before-start releases
the unused owner factory without invoking it.

## 24. Startup Coalescing

Concurrent and repeated callers await the same retained startup task. The
factory and harness start are each invoked at most once.

## 25. Startup Failure

Factory and harness-start failures publish one fixed production-unavailable
state. Underlying errors are not retained in presentation or diagnostics, and
startup is not retried automatically.

## 26. Begin-Terminal-Stop

`beginTerminalStop()` sets terminal intent synchronously and installs one
owner-retained stop task. It never creates production solely to stop it.

## 27. Retained Stop

The retained stop task outlives any cancelled waiter. Concurrent and repeated
`stop()` calls join the same terminal operation. A harness whose start fails
remains retained until this explicit teardown can stop any partially started
resources. The owner releases its harness reference only after harness stop
completes.

## 28. Start/Stop Race

Terminal intent wins before factory execution. If a harness already exists,
stop invokes its coalesced terminal stop and drains the retained startup task.
An operation identifier and terminal lifetime check prevent late ready
publication.

## 29. Lifecycle Termination Interaction

The concrete lifecycle source may deliver `willTerminate` through the existing
harness and host lifecycle path. That path uses the reviewed terminal,
coalesced harness stop.

## 30. Delegate Termination Interaction

`applicationWillTerminate` requests owner terminal stop. If lifecycle delivery
has already initiated harness stop, the reviewed harness coalesces the two
requests.

## 31. Stop Coalescing

The process owner retains one stop operation, and the composition harness
retains its existing coalesced stop. Neither layer constructs a second
harness.

## 32. Shared Multi-Window Authority

`@UIApplicationDelegateAdaptor` stores one delegate. Every `WindowGroup`
window receives that delegate's process owner, so all roots share one harness,
host, lifecycle source, and presentation owner.

## 33. Integrated Root

`AtlasIOSIntegratedAppRootView` receives the owner through `@ObservedObject`.
It constructs no owner, plan, source, harness, or service.

## 34. Valid Reference Rendering

The valid reference state renders exactly
`AtlasReferenceCaptureView(mode: mode)` without production navigation.

## 35. Invalid Reference Rendering

The invalid route displays fixed, non-sensitive unavailable text. It never
renders the raw environment value and has no production fallback.

## 36. Loading Rendering

Pending and starting production states render fixed, non-sensitive loading
content.

## 37. Production-Ready Rendering

Ready state requests a root from the retained process harness and renders
`AtlasVaultProductionRootView`. If the invariant is unexpectedly unavailable,
the view fails closed.

## 38. Unavailable Rendering

Production failure renders fixed unavailable content with no underlying error
or configuration detail.

## 39. Terminal Rendering

Stopping and stopped states render fixed process-terminal content without
private state.

## 40. Removal of Legacy Normal Route

`AtlasRootView` is no longer referenced by the actual app entry or integrated
root. The normal route ends at the reviewed production root.

## 41. No Window-Owned Lifecycle

Neither the SwiftUI app body nor the integrated root uses `.task`,
`.onAppear`, `.onDisappear`, scene-phase observation, or notifications for
process startup or stop.

## 42. No Navigation Redesign

This phase adds no `NavigationStack`, `NavigationLink`, or replacement route
graph. It reuses the reviewed production root.

## 43. No Private Rendering

The integrated route exposes no saved searches, tracker records, saved-job
membership, notes, profile snippets, draft metadata, generated-document
references, decrypted state, or encrypted-record internals.

## 44. Local-Key-Only Unlock

Production composition continues to use
`AtlasVaultUnlockCapabilities.currentProduction`. Local key remains the only
available production unlock method; passphrase and recovery remain
unavailable.

## 45. Error Redaction

Owner, presentation, and production-configuration failures use fixed
descriptions. They disclose no environment values, URLs, errors, identities,
keys, paths, or private payloads.

## 46. Merge-Stable Test Policy

The Phase 2D-59 tests verify durable security boundaries and behavior. They do
not pin `origin/master...HEAD`, a current branch name, a historical app-entry
blob, or the exact current phase diff. Exact seven-file scope remains an
external staging, PR, review, and archive gate.

## 47. TDD Evidence

The red checkpoint failed only because the process owner and integrated root
were absent and the app entry still used its legacy route. The red tests were
committed before production implementation. Deterministic continuation gates,
not sleeps or locks, prove retained and coalesced startup and terminal stop.

## 48. Test Coverage

Focused tests cover route construction, production isolation, lazy startup,
concurrent callers, cancellation, fixed failure, stop-before-start,
stop-during-start, terminal restart rejection, configuration policy, shared
root ownership, retained-plan/harness lifetime separation, rendering
boundaries, and actual app source wiring. Existing Phase 2D-57 and Phase 2D-58
suites cover production-root boundaries, composition, host behavior, route
planning, and lifecycle delivery.

## 49. iOS Build and Smoke Evidence

The package-level `AtlasApple` scheme and the actual
`AtlasIOSHost/AtlasIOSHost.xcodeproj` `AtlasIOSHost` app scheme both build for
a generic iOS Simulator destination. The direct host build compiles and links
the changed `@main` target. No compatible simulator was booted during
verification, so runtime smoke launches are not claimed.

## 50. Go/No-Go

- Actual app-entry integration: implemented.
- Process delegate: implemented.
- Process owner: implemented.
- Valid capture isolation: implemented.
- Invalid capture fail closed: implemented.
- Lazy one-process production harness: implemented.
- Public production root: implemented.
- Local-key explicit unlock: implemented.
- Private rendering: not implemented.
- Fresh-install vault creation: not implemented.
- User-facing API endpoint configuration: not implemented.
- Passphrase/recovery: not implemented.
- Migration/cloud: not implemented.
- Production readiness: not claimed.

## 51. Deferred Work

Fresh-install vault creation and selection, a user-facing API endpoint
configuration channel, private rendering, passphrase and recovery providers,
migration, cloud sync, and broader end-to-end launch validation remain outside
this phase.

## 52. Next Product Gate

Phase 2D-60 must implement explicit local-vault creation and selection so a
fresh installation can create an encrypted local vault and reach the reviewed
local-key unlock flow.
