# Phase 2D-36 AtlasVault Application Lifecycle Design

## 1. Purpose

Phase 2D-36 defines how a future application host should translate platform
lifecycle events into calls on `AtlasVaultRuntimeFacading`. It establishes
lock, cancellation, and teardown policy before any platform or UI wiring is
implemented.

## 2. Design-Only Scope

This phase adds documentation only. It adds no lifecycle coordinator,
notification subscription, SwiftUI or app-entry integration, vault operation,
filesystem access, migration, cloud sync, recovery flow, onboarding, or key
rotation. It does not claim production readiness.

## 3. Runtime Facade Boundary

The runtime facade remains the only lifecycle-facing vault capability. A
future lifecycle coordinator may query its redacted status and request lock;
it must not access the activation controller, key store, path locator,
persistence services, record crypto, or private-state store directly.

## 4. App Host Boundary

The app host owns translation from platform-specific events into neutral
`AtlasVaultLifecycleEvent` values. It forwards those values to one injected
coordinator and does not contain vault policy, key handling, private-state
cleanup, or save logic.

The initial neutral event set is `didBecomeActive`, `willResignActive`,
`didEnterBackground`, `willTerminate`, `protectedDataBecameUnavailable`, and
`protectedDataBecameAvailable`. Platform adapters may map callbacks to these
values but must not add platform objects or private data to them.

## 5. Construction At Launch

Launch may construct the side-effect-free runtime composition, facade, and a
future lifecycle coordinator. Construction must not retrieve a key, resolve a
root, inspect the filesystem, activate a vault, hydrate records, or mutate
public or private state.

## 6. No Automatic Unlock At Launch

Stored vault identifiers, Keychain availability, prior navigation, or public
cache state must not trigger activation. Relaunch starts locked and requires a
new explicit user action.

## 7. Explicit User-Driven Activation

Only a future reviewed presentation boundary may submit an activation request
after user intent. Lifecycle foregrounding may make that action available but
must never synthesize it or reuse previously supplied passphrase, recovery, or
raw-key material.

## 8. Foreground Event

Foregrounding cancels a pending background grace-period lock when policy
allows and the scheduled lock still belongs to the same runtime generation.
It does not activate, restore private state, reload a key, or change a locked
failure into an unlocked state.

## 9. Background Event

Entering background cancels pending activation and applies the injected
background lock policy. Under the recommended initial policy it requests lock
immediately; an explicitly configured grace policy may schedule a bounded
lock instead.

## 10. Inactive Event

Inactive is treated as a transient visibility signal, not proof that the
process entered background. The initial policy cancels pending activation but
does not independently extend a background grace period or restore state.
Hosts may choose immediate lock for stricter deployments.

## 11. Termination Event

Termination requests best-effort immediate lock and cancels pending lifecycle
work. The design does not rely on termination callbacks completing; durable
privacy must come from encrypted storage and the absence of plaintext state
restoration.

## 12. Protected-Data-Unavailable Event

Protected-data unavailability overrides every grace period and requests lock
immediately. It cancels pending activation or save work through the facade and
waits for fail-closed teardown as far as process lifetime permits.

## 13. Protected-Data-Available Event

Protected-data availability only records that a future explicit activation is
eligible to proceed. It does not load a stored key, inspect the local store,
cancel an already required lock, or activate automatically.

## 14. macOS Active/Inactive Considerations

macOS application inactivity does not necessarily mean background execution
or loss of screen visibility. A future host should emit neutral active and
inactive events while the injected policy decides whether inactivity locks
immediately, starts a grace period, or waits for an explicit security event.

## 15. iOS Scene Lifecycle Considerations

An iOS host should translate scene changes without passing `ScenePhase`,
UIKit, or SwiftUI types into the coordinator. Scene backgrounding,
protected-data loss, and process termination remain distinct events with
protected-data loss taking highest priority.

## 16. Multiple-Window Considerations

One runtime facade represents one process-wide unlocked vault session. A
future host should aggregate window activity so one active scene may cancel a
scheduled grace lock, while protected-data loss or explicit lock from any
scene locks all windows. Window identifiers must not become vault metadata.

## 17. Lock-On-Background Policy Options

The policy may be `immediate` or `afterGracePeriod(duration)`. A future
explicitly reviewed `manual` option would carry greater privacy risk and is
not recommended as the initial behavior. Protected-data loss and termination
ignore the selected background policy and request immediate lock.

## 18. Recommended Initial Lock Policy

Use immediate lock on background, protected-data loss, and termination. This
is deterministic, minimizes private-state lifetime, and avoids implying that
background execution or a timer can be relied upon.

## 19. Configurable Grace Period

If product review requires a grace period, inject a monotonic clock and
cancellable sleeper. Keep the duration bounded and non-persistent. A grace
token must identify the runtime generation so stale timer completion cannot
lock a later activation.

## 20. Grace-Period Privacy Implications

During grace, the key and hydrated private state remain in memory and may be
reachable by a resumed process. The UI must obscure private content while
backgrounded even though this phase adds no UI. A grace period never permits
plaintext persistence or restoration.

## 21. Cancellation Of Pending Activation

Background, inactive when configured, protected-data loss, termination, and
explicit lock invalidate pending activation by calling the facade's lock
boundary. The coordinator does not retain or inspect the activation request or
key material.

## 22. Cancellation Of Pending Save

Immediate-lock policy asks the facade to lock, which cancels pre-commit save
work and waits for controller cleanup. A grace policy may allow an in-flight
save a bounded opportunity to finish before lock, but must not inspect its
mutations or encrypted envelopes.

## 23. Save Completion Before Lock

If encrypted commit has already started, cancellation cannot assert that no
write occurred. The facade owns commit-aware reconciliation: it either
installs consistent private state or clears and locks. The lifecycle layer
waits only for a redacted completion/lock boundary.

## 24. Forced Lock After Timeout

When a configured save or grace deadline expires, the coordinator invalidates
the timer token and calls `lock()` regardless of the prior operation. Repeated
or late timeout callbacks are harmless and cannot act on a newer generation.

## 25. State Clearing

All hydrated private state is cleared by the facade and activation controller
as part of lock. The lifecycle layer does not cache a snapshot, clear
collections itself, or publish unlocked state before teardown completes.

## 26. Key Lifetime

The lifecycle layer never receives or stores a vault key. The canonical key
owner remains inside the activation controller and is wiped or released by
the facade lock path, subject to documented Swift memory-clearing limits.

## 27. No Private State Restoration

After relaunch or a completed lock, private state is reconstructed only by a
new explicit activation and encrypted-store hydration. Lifecycle state must
not be used to reconstruct saved searches, saved jobs, notes, snippets, draft
metadata, record membership, or private counts.

## 28. No Private State In Scene Storage

Decrypted state, vault identifiers associated with user meaning, activation
requests, passphrases, recovery material, and private navigation selections
must not enter `SceneStorage` or platform restoration payloads.

## 29. No UserDefaults Private State

`UserDefaults` must not store private payloads, private counts, saved-only job
keys, lock bypass flags, raw key data, or activation credentials. A future
non-sensitive policy preference requires separate privacy review.

## 30. No Public Snapshot Mutation

Lifecycle events and lock policy never modify `AtlasPublicLocalSnapshot` or
public job cache data. Public search remains independent, and saved-only
membership must not be inferred from lifecycle state.

## 31. Error Handling

Lifecycle coordination exposes only stable categories such as invalid event,
cancelled timer, lock timeout, or facade unavailable. It does not include
vault IDs, paths, record metadata, private counts, operation payloads, or
underlying error descriptions.

## 32. Diagnostic Redaction

Event, policy, coordinator state, timeout, error, and debug descriptions must
be fixed and non-sensitive. No diagnostic may contain keys, activation input,
private payload values, paths, record IDs, search text, job keys, notes,
snippets, or document references.

## 33. Runtime Facade Interaction

The future coordinator depends on a narrow injected facade capability:
redacted `status()` and idempotent `lock()`. It does not call `activate` on a
lifecycle event. If operation-aware waiting is needed, add a redacted facade
completion seam only after review rather than polling private internals.

## 34. Future Lifecycle Coordinator

Phase 2D-37 should implement an actor that accepts neutral events, an injected
lock policy, clock, sleeper, and redacted facade controls. It owns only event
ordering and cancellable timer tokens, never vault keys or private state.

## 35. Test Event Injection

Tests should deliver lifecycle events directly and use fake time. Coverage
must include immediate and grace-period lock, foreground cancellation,
protected-data override, termination, repeated events, activation
cancellation, save timeout, stale timer generations, and redacted output.

## 36. No NotificationCenter Subscription Yet

This phase and Phase 2D-37 do not subscribe to `NotificationCenter`, scene
notifications, or application delegates. Platform adapters remain a later,
separately reviewed boundary.

## 37. No UIKit, AppKit, Or SwiftUI Code Yet

No platform framework, scene phase, observable object, property wrapper, app
entry point, view model, or view is added. This design uses neutral lifecycle
terms only.

## 38. Migration Deferred

Lifecycle integration does not migrate legacy plaintext data, create vaults,
delete old snapshots, or alter encrypted-store versions.

## 39. Cloud Deferred

Cloud sync, remote conflict behavior, server sessions, cross-device lock, and
network reachability are outside this local Apple vault phase.

## 40. Recovery And Rotation Deferred

Recovery UX, passphrase unwrapping, user-presence policy, device onboarding,
key rotation, and Keychain item lifecycle remain separate reviewed phases.

## 41. Recommended Phase 2D-37

Implement a runtime-neutral, event-injected lifecycle coordinator with fake
clock/sleeper tests. Keep it disconnected from platform notifications,
SwiftUI, app launch, public cache, migration, and cloud sync.
