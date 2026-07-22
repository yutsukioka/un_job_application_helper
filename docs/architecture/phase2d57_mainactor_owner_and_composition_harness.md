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

`AtlasVaultPlatformLifecycleEventSourcing` supplies an asynchronous stream of
neutral `AtlasVaultLifecycleEvent` values. Calling `events()` is deferred until
explicit forwarder start.

## 15. Lifecycle Forwarder

`AtlasVaultProductionLifecycleForwarder` is an actor that owns one source, one
host, one stream-consumer task, and terminal state. Its descriptions expose no
source, host, event, or task detail.

## 16. Explicit Lifecycle Start

Construction subscribes to nothing. `start()` creates and retains one task,
waits for the source-subscription handshake, and is idempotent while active.
This lets the composition prove forwarding is ready before host startup.

## 17. Event Ordering

The forwarding task iterates one stream and awaits each host lifecycle call
before reading the next event. No per-event child task exists, so source order
and serial host delivery are preserved.

## 18. Lifecycle Stop and Task Drain

`stop()` marks the forwarder terminal, cancels its retained task, and awaits
that exact task. A callback already executing may delay stop; it cannot outlive
completed stop. Restart is rejected.

## 19. Will-Terminate Behavior

The forwarder awaits one `.willTerminate` host call and then ends consumption.
It does not synthesize a second stop event. Later harness stop joins the host's
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

`start()` first starts and handshakes lifecycle forwarding, then invokes host
start. Concurrent callers share one retained operation. A successful repeated
start is idempotent, and caller cancellation does not transfer ownership of
the retained operation.

## 38. Start Failure

A host-start failure stops and drains lifecycle forwarding, leaves the owner
in its initial private-free state when no host reset committed, and makes this
first harness terminal. The public error is fixed and redacted.

## 39. Explicit Terminal Stop

`stop()` is explicit, retained, coalesced, idempotent, and terminal. It returns
the host's private-free terminal flow and rejects a later start.

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

`AtlasIOSHostApp`, `WindowGroup`, reference-capture routing, `AtlasRootView`,
and production navigation are unchanged. The new root is not reachable from
normal launch in this phase.

## 48. No Private Rendering

The root renders only the reviewed public locked-shell flow and the existing
non-sensitive unlocked transition. No runtime private state or private record
projection enters the owner, actions, harness, or root.

## 49. Reference-Capture Isolation Design

A future entry integration must branch on `ATLAS_REFERENCE_CAPTURE` before
constructing production composition. Reference capture must create no harness,
source, host, Keychain access, application-support lookup, or network request.
This phase does not change or enforce that entry path.

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

Three compile-valid red files were committed before production source. Their
focused builds failed on absent Phase 2D-57 types. Green tests then exercised
the strict owner fence, actual Phase 2D-56 terminal behavior, retained lifecycle
work, harness ownership, injected concrete graph, actions, and source bounds.

## 55. Test Coverage

Coverage includes initial privacy, ordinary and exact fenced generations,
stale suspended resets, observer publication, real host start/stop/termination,
serial lifecycle order, stream completion, stop drain, concurrent start/stop,
failure cleanup, URL and policy validation, zero-call assembly, no-vault
integration, shared roots, and app-entry/source guards.

## 56. Go/No-Go Update

- Runtime-neutral production host: implemented.
- Production presentation pipeline: implemented.
- MainActor production presentation owner: implemented.
- Owner generation fencing: implemented.
- Neutral lifecycle source protocol: implemented.
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
only a later reviewed phase may change the normal application route.
