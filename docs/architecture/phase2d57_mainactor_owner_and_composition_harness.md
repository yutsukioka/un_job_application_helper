# Phase 2D-57 MainActor Owner and Composition Harness

## 1. Purpose

Phase 2D-57 adds the first concrete MainActor owner and an unwired,
production-like composition boundary above the runtime-neutral Phase 2D-56
host. It makes the reviewed public locked-shell journey composable without
changing the application entry point.

## 2. Phase Scope

This phase implements the presentation owner, a neutral lifecycle source
contract, a retained lifecycle forwarder, explicit composition configuration,
the concrete production-like graph, a process harness, and a thin SwiftUI root.
It does not implement a platform lifecycle source or production launch wiring.

## 3. Reconstructed Phase 2D-56 Baseline

Phase 2D-56 and the terminal-generation follow-up are merged on master. The
host, private-free pipeline, public adapters, lazy selector, shared unlock
controller, local-key-only admission, terminal search drain, and authoritative
lock barrier are reviewed and tested. Terminal teardown now supersedes and
first resets the owner with the same exact host generation.

## 4. Existing Host-Owner Contract

The owner receives only `AtlasLockedShellUnlockFlowState` and an opaque
`AtlasVaultProductionHostGeneration`. It receives no runtime, selected-vault
identifier, key, path, or service. Reset returns an acknowledgement that the
host includes in its private-free barrier.

## 5. MainActor Owner Responsibility

`AtlasVaultProductionPresentationOwner` is the single MainActor-observable
holder of the current sanitized locked-shell flow. It acknowledges only a
generation-authorized reset and publishes the flow through one `@Published`
property.

## 6. MainActor Owner Non-Responsibilities

The owner does not start the host, select a vault, activate a runtime, perform
navigation, render private records, access persistence, or retain services.

## 7. Initial Private-Free State

Construction installs an empty locked public shell with unavailable cache and
service state, local-key production capability, no selected method, no panel,
and `canRequestUnlock == false`. Construction invokes no hook and starts no
task.

## 8. Generation Authority

The private authority is `none`, `established`, or `superseded`. A private
revision invalidates suspended work. Neither generation nor revision is
observable, serializable, or included in diagnostics.

## 9. Ordinary Generation Establishment

With no pending supersede fence, an accepted reset may establish its supplied
generation. The same generation can update again, and a later ordinary reset
can establish a different generation. A later accepted reset invalidates an
older suspended ordinary reset.

## 10. Explicit Supersede Fencing

`supersedePresentationGeneration(_:)` increments the owner revision and makes
the supplied exact generation mandatory. A reset with any other generation is
rejected while that fence is pending. Supersede itself does not mutate flow.

## 11. Suspended Stale-Reset Rejection

Every reset captures its revision and authority before the optional test hook,
then revalidates both afterward. A pre-supersede or otherwise superseded reset
cannot commit after resumption.

## 12. Concurrent Reset Behavior

MainActor serialization establishes each candidate before its test-only
suspension. The newest accepted candidate advances the revision, so only one
candidate can commit. No state history or replay queue is retained.

## 13. Published-State Privacy

The published value is the existing public-shell flow only. It has no private
payload field and the owner source contains no vault identifier, key, secret,
path, private model, or dependency object.

## 14. Lifecycle Source Protocol

`AtlasVaultPlatformLifecycleEventSourcing` supplies an explicit
`AtlasVaultPlatformLifecycleEventSubscription`. Each subscription contains an
ordered array of safety bootstrap events and a stream of subsequent live
event deliveries. It also provides an explicit readiness-boundary request.
Every source must declare that contract, including an explicit empty array
when no bootstrap is required. There is no stream-only compatibility default
that could silently classify platform bootstrap as live traffic.

A concrete platform source installs observation, captures its initial
lifecycle snapshot, and returns the resulting bootstrap array together with a
live stream that buffers signals arriving after the snapshot. When the
forwarder requests a readiness boundary, the source inserts the matching
control delivery into the same serialized production channel as live events.
Every live event observed before that request must precede the marker.
The live stream must use a lossless policy for its low-frequency,
safety-relevant lifecycle deliveries; a source may not use a dropping or
nonbuffering policy. The forwarder establishes its stream iterator before
requesting the marker, so the requested control delivery is observable through
the same retained subscription path.
Subscription is deferred until explicit forwarder start.

## 15. Lifecycle Forwarder

`AtlasVaultProductionLifecycleForwarder` is an actor that owns one source, one
host, one bootstrap-and-stream consumer task, and terminal state. Its
descriptions expose no source, host, event, or task detail.

## 16. Explicit Lifecycle Start

Construction subscribes to nothing. `start()` registers its initial readiness
continuation before it creates and retains the forwarding task, so an
immediately available subscription cannot race past the waiter. The retained
task obtains one subscription and serially forwards every explicitly declared
bootstrap event, awaiting each host callback. It then requests a unique live
readiness boundary after establishing the live-stream iterator, and drains
every live event ahead of the matching marker. Only after the full bootstrap
array and that bounded catch-up have been handled does the forwarder become
active and resume all start waiters with `true`. Bootstrap and catch-up counts
are variable; readiness uses explicit boundaries, not an event-name or
fixed-count heuristic.

Protected-data-first bootstrap order is preserved. Initial active, inactive,
or background phase is therefore handled before composition host startup can
begin. A live event that arrives during bootstrap stays buffered in the
subscription stream, then is handled before readiness because it precedes the
requested marker. Events after the marker remain live traffic. Concurrent
starts share the same subscription and bootstrap handshake.

## 17. Event Ordering

The forwarding task iterates bootstrap events, live catch-up through the exact
readiness marker, and then subsequent live traffic. It awaits each host
lifecycle call before taking the next value. No bootstrap or pre-boundary live
event is suppressed or reordered, and no live event can overtake bootstrap.
The readiness marker is control-only and is never sent to the host. No
per-event child task exists, so serial host delivery is preserved.

## 18. Lifecycle Stop and Task Drain

`stop()` marks the forwarder terminal, cancels its retained task, and awaits
that exact task before resolving any pending start waiter with `false`. A
bootstrap callback already executing may delay stop; it cannot outlive
completed stop, and later bootstrap or live events are not forwarded. Natural
stream completion and `.willTerminate` mark forwarding terminal but retain the
completed task handle until explicit stop joins and clears it. This closes the
completion window in which terminal state could otherwise become visible
before task completion. Restart is rejected.

## 19. Will-Terminate Behavior

The forwarder records terminal intent before awaiting one `.willTerminate`
host call and then ends consumption. When termination appears in bootstrap or
pre-readiness live catch-up, the forwarder never becomes ready, all start
waiters receive `false`, and normal host startup is prevented. It does not
synthesize a second stop event. Later harness stop joins the host's
already-terminal policy idempotently.

## 20. Composition Configuration

`AtlasVaultProductionCompositionConfiguration` contains the explicit API
origin, fixed search limit, positive unlock timeout, lifecycle lock policy, and
inactive-lock policy. Invalid input produces fixed errors.

## 21. Explicit API Base URL

Only HTTP and HTTPS origins with a non-empty host are accepted. Credentials,
query, fragment, and non-root paths are rejected. A root trailing slash is
normalized; no `UserDefaults` or zero-argument API-client path is used.

## 22. Search Limit and Unlock Timeout

The search limit must be within `AtlasPublicJobSearchRequest.maximumLimit` and
defaults explicitly to 50 in configuration. The unlock timeout defaults
explicitly to 30 seconds and must be positive.

## 23. Production Lifecycle Timebase

`AtlasContinuousVaultLifecycleTimebase` uses one `ContinuousClock` and one
captured origin for both `now()` and `sleep(until:)`. It uses no wall clock,
global mutable state, or detached task.

## 24. Concrete Public API Adapter Assembly

The factory constructs `AtlasAPIClient(baseURL:)` with the validated explicit
origin and wraps it in `AtlasAPIClientPublicJobAdapter`. Assembly makes no
health or search request.

## 25. Public Snapshot-Restorer Assembly

One application-support root provider is constructed from the injected or
Foundation locator and passed to
`AtlasApplicationSupportPublicSnapshotRestorer`. No root is resolved until
explicit host start.

## 26. Vault-Selection Registry Assembly

`AtlasKeychainVaultSelectionRegistry` receives the same injected or SecItem
client used by runtime composition. It performs no Keychain operation until an
explicit panel request triggers lazy selection.

## 27. Runtime-Services Assembly

`AtlasVaultRuntimeFactory.production(directoryLocator:keychainClient:
atomicFileSystemClient:)` assembles runtime values without resolving a root,
loading a key, creating a directory, or reading a store.

## 28. Runtime-Facade Assembly

`AtlasVaultRuntimeFacade.runtimeServices(_:)` creates the concrete facade over
those services. No activation occurs during graph construction or host start.

## 29. Lifecycle-Coordinator Assembly

The concrete facade, explicit policy, shared clock, shared sleeper, and
inactive policy construct one `AtlasVaultLifecycleCoordinator`.

## 30. Production-Pipeline Assembly

One `AtlasVaultProductionPresentationPipeline` is constructed but remains
inactive until explicit host start.

## 31. Unlock-Coordinator Assembly

One `AtlasVaultUnlockRequestCoordinator` delegates activation to the concrete
runtime facade. It is passed to the reviewed concrete controller builder.

## 32. Passphrase/Recovery Unavailable Closures

The required passphrase and recovery derivation closures throw one fixed,
non-sensitive unavailable error. They perform no derivation, unwrap, parsing,
or provider access.

## 33. Host-Dependency Assembly

The public jobs adapter, snapshot restorer, selector, runtime, lifecycle
coordinator, pipeline, owner, unlock coordinator, and controller builder are
placed in one `AtlasVaultProductionHostDependencies` value.

## 34. Host Factory and One-Process Host

The concrete `AtlasVaultProductionHostBuilder` and
`AtlasVaultProductionHostFactory` create exactly one inactive host. One
lifecycle forwarder and one harness own that process authority.

## 35. Construction Side Effects

Factory and harness construction create values only. They do not resolve a
directory, touch Keychain or filesystem, call the network, restore a snapshot,
start lifecycle observation, start presentation, start the host, select a
vault, create a controller, or activate runtime.

## 36. Composition-Harness Ownership

`AtlasVaultProductionCompositionHarness` is MainActor-isolated and owns one
host reference, one presentation owner, one lifecycle forwarder, one pair of
action values, and retained start and stop operations. Low-level services are
not exposed.

## 37. Explicit Start

`start()` first starts lifecycle forwarding and waits until every declared
bootstrap event has been handled, then invokes host start. This means a
protected-data event followed by an inactive or background phase cannot expose
the host's default active assumptions between subscription and startup.
Concurrent callers share one retained operation. A successful repeated start
is idempotent, and caller cancellation does not transfer ownership of the
retained operation. After every retained-task await, a successful outcome is
accepted only while startup still owns the lifetime or has committed the
started lifetime. If terminal stop has moved the harness to stopping or
stopped, both the initiating caller and every joining caller receive the fixed
stopped error instead of observing a stale successful start. Explicit terminal
intent is retained separately from a normal startup failure, so a retained
start that fails because stop won also reports stopped, while an ordinary
startup failure continues to report the fixed start-unavailable error. Before
either retained-start caller path commits a successful outcome, the harness
rechecks lifecycle-forwarder terminal state. A completed stream or
`.willTerminate` winner clears retained-start authority, runs or joins terminal
stop, and returns the fixed stopped error rather than a stale started flow. A
terminal lifecycle intent is recorded before `.willTerminate` awaits the host.
If that event makes the retained host start fail, the private start outcome
preserves the terminal witness through structured failure cleanup, so both
caller paths return stopped rather than relabeling it start-unavailable. The
same terminal-intent witness fences a successful retained host start while the
`.willTerminate` callback is still in flight; startup cannot return an
interactive flow merely because forwarding has not yet reached its completed
terminal state. The already-started idempotent path uses the same witness and
runs or joins terminal stop before returning stopped, so a repeated start
cannot expose current interactive state during that interval. Because fetching
the current host flow is an actor suspension point, that path fences both
before and after the fetch on terminal intent, explicit-stop authority, and
started lifetime. Explicit stop or later lifecycle intent therefore cannot win
during the await and still be followed by stale success.

## 38. Start Failure

A host-start failure begins host terminal stop and lifecycle-forwarder stop as
structured concurrent work, then awaits both before returning. The retained
start outcome carries the host's private-free terminal state, so a later
explicit harness stop is idempotent rather than starting a second teardown.
The owner remains private-free, and the public error is fixed and redacted.

## 39. Explicit Terminal Stop

`stop()` is explicit, retained, coalesced, idempotent, and terminal. It returns
the host's private-free terminal flow, rejects a later start, and wins over a
successful host-start result that completes after terminal intent.

## 40. Lifecycle and Stop Concurrency

The terminal operation starts host stop and lifecycle-forwarder stop as
structured child operations before awaiting either result. A current callback
cannot prevent host teardown from beginning, and both owned paths are drained
before harness stop returns.

## 41. Public-Shell Actions

One action value converts submitted text into a validated request using the
configured limit and offset zero, invokes the host once, and discards its fixed
error after host publication. The unlock action invokes panel request once.

## 42. Explicit-Unlock Actions

Selection, submission, cancellation, and disappearance delegate once to the
host. The actions neither expose the host nor inspect or retain secret input.

## 43. Submit-Status Mapping

`unlockedTransition` maps to `.unlocked`; an existing panel maps to its exact
status; absence of either maps to fixed `.failed`. The configured positive
timeout is passed to host submission.

## 44. Shared Multi-Window Authority

Repeated `makeRootView()` calls create view values over the same owner and the
same action values. They create no host, runtime, selector, or controller, and
view destruction does not stop process authority.

## 45. Unwired Root View

`AtlasVaultProductionRootView` observes the injected owner with
`@ObservedObject` and renders only `AtlasLockedShellUnlockFlowView` using the
prebuilt public and unlock actions.

## 46. No Automatic Start or Stop

The root has no task, appearance, disappearance, scene-phase, or notification
hook. Future process integration must call harness start and stop explicitly.

## 47. No App-Entry or Navigation Wiring

App-entry non-modification is a historical Phase 2D-57 scope property. The
commit that introduced `AtlasVaultProductionRootView.swift` left
`AtlasIOSHostApp`, reference-capture routing, `AtlasRootView`, and production
navigation unchanged, so the new root was not reachable from normal launch in
that phase.

The root test discovers that introduction commit dynamically and verifies that
its first-parent diff contains exactly the seven reviewed Phase 2D-57 files.
The app entry and `AtlasReferenceCaptureView.swift` are explicitly absent from
that historical diff. The test does not constrain the current repository to
retain `AtlasRootView`, pin an app-entry blob or structure, or compare a
current feature branch with the historical Phase 2D-57 file set. A later
reviewed integration phase is expected to replace the normal route.

## 48. No Private Rendering

The root renders only the reviewed public locked-shell flow and the existing
non-sensitive unlocked transition. No runtime private state or private record
projection enters the owner, actions, harness, or root.

## 49. Reference-Capture Isolation Design

A future entry integration must branch on `ATLAS_REFERENCE_CAPTURE` before
constructing production composition. Reference capture must create no harness,
source, host, Keychain access, application-support lookup, or network request.
Phase 2D-57 did not change that entry path, as proven by the historical
introduction diff. The permanent test does not freeze the current
reference-capture implementation; a later reviewed integration remains
responsible for preserving this isolation.

## 50. Future iOS Lifecycle Adapter

A later iOS adapter will map aggregate active, inactive, background,
protected-data unavailable/available, and process-termination inputs to the
neutral lifecycle enum. No iOS framework or notification implementation is
present here.

## 51. Multi-Scene Aggregation Design

The future process source must track active-scene count, inactive-only state,
and the no-active-scene/background state. One window entering background must
not imply process background while another is active. It must suppress
duplicates and document event ordering.

## 52. macOS Lifecycle Boundary

There is no production macOS app target. Any future macOS source must be a
separate implementation of the same neutral protocol; an iOS adapter does not
complete macOS support.

## 53. Error and Diagnostic Redaction

Configuration, owner, forwarder, timebase, and harness descriptions are fixed.
They omit URL, host, limits, timeout, generations, event state, query, job
identifier, vault identifier, keys, paths, dependency errors, and service
identities.

## 54. TDD Evidence

The merge-stability follow-up first added a structural regression that failed
while the root test still named the obsolete current-app assertion, required
current `AtlasRootView()`, required direct current reference-capture routing,
and loaded the current app-entry source. The implementation replaced that
assertion with a dynamic first-addition and first-parent historical scope
check. No runtime source changed.

Three compile-valid red files were committed before production source. Their
focused builds failed on absent Phase 2D-57 types. Green tests then exercised
the strict owner fence, actual Phase 2D-56 terminal behavior, retained lifecycle
work, harness ownership, injected concrete graph, actions, and source bounds.
Exact-head review added a red structural regression for readiness registration
before task launch and replaced a tautological shared-root assertion with
observable owner-state and delegated-action evidence. Later cycles proved that
startup failure drains both host and lifecycle work, and that terminal stop
wins over late successful results for both initiating and joining start
callers. The final review cycle additionally proved that stop-induced failed
start outcomes preserve terminal semantics and that a naturally completed
lifecycle task remains retained until explicit stop drains it. Subsequent
review added a deterministic terminal-lifecycle/start race, canonicalized the
integration-test temporary root, and made owner test-gate waiter removal use a
copied key array rather than mutating a dictionary during key iteration. The
next exact-head cycle also proved that an in-flight terminal lifecycle event
which makes retained host start fail remains a terminal stopped outcome. A
test-only stopping waiter treats both stopping and already-stopped lifetimes as
terminal, preventing deterministic race probes from suspending after teardown
has already completed. A retained-start race also holds `.willTerminate`
handling open and proves terminal intent begins stop before either successful
start caller can return. A separate already-started regression proves a
repeated start joins that same terminal drain rather than returning current
state. Additional reentrancy regressions suspend current-flow retrieval and
prove both explicit stop and a later terminal lifecycle intent win before the
repeated start can return.

The lifecycle-readiness follow-up added deterministic red evidence showing
that the old stream-only handshake returned before protected-data and initial
phase callbacks completed. Green regressions now cover empty, one-event,
two-event, and three-event bootstrap arrays; protected-data-first active,
inactive, and background startup; concurrent start callers; buffered live
traffic; bootstrap termination; and stop while a bootstrap callback is
suspended.

Exact-head review then exposed a queued-live race between bootstrap completion
and waiter scheduling. A deterministic red regression buffered a closing live
event while bootstrap handling was suspended and proved host startup could
win. The correction adds an exact live-delivery marker, drains every delivery
ahead of it before readiness, and leaves events after it on the normal live
path. A later review regression proved the live-stream iterator is established
before the marker request; the source contract also requires lossless
lifecycle buffering so the marker and preceding safety events cannot be
dropped.

## 55. Test Coverage

Coverage includes initial privacy, ordinary and exact fenced generations,
stale suspended resets, observer publication, real host start/stop/termination,
serial lifecycle order, race-free subscription readiness, stream completion,
stop drain including the task-completion boundary, concurrent start/stop for
both successful and failed retained starts, failure cleanup, URL and policy
validation, lifecycle termination during retained host start, zero-call
assembly, no-vault integration from a canonical temporary root, shared
owner/action roots, terminal lifecycle failures during retained start, safe
deterministic suspension gates including the already-stopped waiter boundary,
in-flight terminal-intent fencing before forwarder completion, and
repeated-start terminal-intent fencing across current-flow suspension,
explicit-stop winner preservation, explicit bootstrap-boundary ordering,
variable bootstrap length, bootstrap-sensitive host startup, buffered live
events, exact live-boundary catch-up, closing-event startup fencing, bootstrap
termination, stop-during-bootstrap drain, and app-entry/source guards.
The root suite additionally covers dynamic Phase 2D-57 introduction discovery,
complete-history handling, exact historical seven-file scope, explicit
historical app-entry/reference-capture exclusion, absence of current-app and
current-branch pins, and the permanent public-only, service-free,
lifecycle-free root-source boundary.

## 56. Go/No-Go Update

- Runtime-neutral production host: implemented.
- Production presentation pipeline: implemented.
- MainActor production presentation owner: implemented.
- Owner generation fencing: implemented.
- Neutral lifecycle source protocol: implemented.
- Explicit bootstrap-plus-live lifecycle subscription: implemented.
- Bootstrap-and-catch-up-complete lifecycle readiness: implemented.
- Lifecycle forwarder: implemented.
- Production-like composition factory: implemented.
- Production-like composition harness: implemented.
- Unwired production root view: implemented.
- Concrete iOS lifecycle event source: not implemented.
- Multi-scene aggregation: design only.
- App-entry wiring: blocked.
- Reference-capture enforcement in a new entry path: not implemented.
- Private rendering: blocked.
- Passphrase/recovery: blocked.
- Production launch: blocked.

## 57. Deferred Work

Concrete iOS lifecycle observation, process-wide scene aggregation,
protected-data and termination delivery, entry integration enforcement,
production navigation, private rendering, passphrase/recovery providers,
migration, and cloud behavior remain deferred.

## 58. Next Product Gate

Phase 2D-58 must implement the concrete iOS process lifecycle event source,
multi-scene lifecycle aggregation, protected-data and termination delivery, and
an app-entry integration design/test harness that preserves
`ATLAS_REFERENCE_CAPTURE` isolation. It must not modify `AtlasIOSHostApp`;
only a later reviewed phase may change the normal application route. Its
concrete source must map its existing ordered bootstrap array to
`bootstrapEvents`, wrap its subsequent buffered values as event deliveries,
and insert a requested readiness marker into the same ordered system-signal
production channel. The open Phase 2D-58 PR must rebase onto this
lifecycle-readiness follow-up before its readiness finding can be resolved.

After the reviewed Phase 2D-58 lifecycle and route-plan work, a later
app-entry integration may replace the current normal route while preserving
reference-capture isolation. The merge-stable Phase 2D-57 tests permit that
reviewed change without weakening the historical Phase 2D-57 scope evidence.

## 59. Root-Test Merge-Stability Follow-Up

Phase 2D-57F2 changes only the root test and this architecture record. The
introduction commit is discovered from the first addition of
`AtlasVaultProductionRootView.swift`; no commit or app-entry blob SHA is
hard-coded. Its first-parent diff must remain exactly the seven reviewed
Phase 2D-57 files, with the iOS app entry and reference-capture view explicitly
excluded. Current app-entry contents and current feature-branch diffs are not
pinned.

The production root's own boundary remains permanent: it is MainActor-owned,
observes one injected presentation owner, renders only the reviewed locked
shell/unlock flow, and owns no service, lifecycle, navigation, automatic
start/stop, or private-state behavior. This follow-up makes that invariant
compatible with Phase 2D-59 without implementing Phase 2D-59.
