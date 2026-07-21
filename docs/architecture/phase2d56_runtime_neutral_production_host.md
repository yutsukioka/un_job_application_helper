# Phase 2D-56 Runtime-Neutral Production Host

## 1. Purpose

Phase 2D-56 implements the first runtime-neutral production host for
AtlasVault. The host owns public-shell state, public task lifetime, unlock
admission, authoritative reconciliation, and private-free presentation
barriers without wiring those capabilities into an application entry point.

The first production journey stops at
`AtlasLockedShellUnlockFlowMode.unlockedTransition`. It does not read or
render private records.

## 2. Phase Scope

This phase adds:

- a host-owned sanitized presentation pipeline;
- the `AtlasVaultProductionHost` actor;
- concrete host and unlock-controller builders;
- one process-global unlock admission domain per host actor;
- lazy vault selection and lazy shared-controller creation;
- explicit start, lock, lifecycle, and terminal stop operations;
- host-owned public-search and unlock tasks;
- authoritative private-free reconciliation;
- an injected MainActor owner-reset acknowledgement seam.

It adds no production composition root, platform adapter, app-entry wiring,
navigation, private rendering, passphrase provider, or recovery provider.

## 3. Reconstructed Phase 2D-55 Baseline

Implementation began from `origin/master` at
`028dd4616b711228a369a0b72c9a2aef53f8fb62`. That commit is the squash merge
of PR #71, `Add AtlasVault production adapter tests`, whose reviewed feature
head was `29572fe2793d23e557b61232c72897b7e18177aa`.

The merged head had 23 review threads and zero unresolved threads. Python and
GitGuardian checks were successful. The Phase 2D-55 merge was verified as an
ancestor of `origin/master`, and no later change affected the reviewed host
contracts, public adapters, snapshot restorer, selection registry, or Phase
2D-53 through Phase 2D-55 architecture documents.

The seven relevant merged baseline test classes passed 257 tests with zero
failures before Phase 2D-56 work began.

## 4. Existing Host Contract

The Phase 2D-54 command surface remains the boundary:

- explicit `start()` and `stop()`;
- current sanitized flow state;
- public search;
- explicit unlock-panel request;
- unlock method selection and submission;
- cancellation and panel disappearance;
- explicit lock;
- neutral lifecycle events.

Phase 2D-56 adds only the coordination values required to implement that
surface: fixed host errors, an opaque host generation, a validated private-free
snapshot wrapper, a production presentation coordinator, and a MainActor
owner-reset acknowledgement protocol.

## 5. Concrete Host Actor

`AtlasVaultProductionHost` is a public actor conforming to
`AtlasVaultProductionHosting`. Its lifetime distinguishes inactive, starting,
started, reconciling, stopping, and stopped states. It owns the current public
shell, non-sensitive unlock flow state, operation generations, selected vault
identifier, shared controller, and all in-flight operations.

The actor is runtime-neutral. It depends only on the narrow protocols assembled
in `AtlasVaultProductionHostDependencies`.

## 6. Process Ownership

One host actor owns one unlock-admission domain. Windows and future presentation
owners observe the host; they do not create independent selectors,
controllers, or submit tasks. The selected vault identifier and controller
never leave the actor through public flow state or diagnostics.

## 7. Construction Side Effects

Dependency-bundle, pipeline, builder, factory, and host construction assign
values only. Construction does not:

- create a task or subscription;
- restore a snapshot;
- select a vault;
- create an unlock controller;
- call runtime or lifecycle services;
- publish presentation state;
- access Keychain, files, or network services.

The concrete host builder returns an inactive host. The concrete controller
builder constructs a controller only when explicitly invoked.

## 8. Explicit Start

`start()` is explicit, coalesced, and idempotent after success. It starts the
presentation pipeline, restores the optional public snapshot once, publishes a
locked private-free snapshot, and awaits the owner reset. Unlock admission
remains closed throughout every await.

The pipeline source has its own activation gate. A subscription requested
before `start()` may observe only the adapter's initial private-free value; it
cannot begin source observation until explicit start activates the source.
Concurrent start callers share the same starting handshake and return only
after the one source observation is active.

If terminal stop arrives during that handshake, stop first marks lifetime as
stopping and then joins the retained start operation. A successfully started
pipeline is finished through the terminal barrier; if the handshake never
started or failed, stop reaches the conservative locked terminal state without
publishing through an inactive pipeline.

The intended ready shell is sent to the owner while actor admission is still
closed. Only after the owner acknowledgement and generation check succeed does
the actor atomically enter `started` and open admission. Start after terminal
stop fails with the fixed `stopped` host error.

## 9. Public Snapshot Restore

Snapshot restoration occurs only during explicit start and at most once per
host. A valid snapshot contributes reviewed public health and public jobs only.
The host does not restore a query, private membership, vault identity, or
private records.

No snapshot produces an empty shell with checking service status and unavailable
cache freshness. A restore error fails open to an empty public shell with fixed
unavailable public status. Under either outcome the shell remains private-free.

## 10. Conservative Cache Policy

A valid restored snapshot is marked `stale`, not `current`. Only a successful
live public search advances cache freshness to `current`. This avoids claiming
freshness merely because a public cache was readable.

## 11. Public-Search Task Ownership

The host creates one unstructured host-owned public-search task through
`AtlasPublicJobSearching`. A superseding search cancels the prior task. Stop
cancels and awaits the active search. Search never selects a vault or invokes
runtime, lifecycle, unlock, or private compatibility services.

## 12. Search Generation And Stale-Result Rejection

Each search receives a host-local monotonically advancing token and operation
identifier. Completion must match the current operation, generation, and host
lifetime before it can update the shell. A late cancelled or superseded result
cannot overwrite a newer result or a terminal host. This public-search token is
separate from the unlock-admission generation: a valid public-only completion
does not invalidate an unrelated vault selection or unlock submission.

Queries remain public shell inputs but are absent from host and error
descriptions.

## 13. Public Search During Unlock And Lock

Public search is independent of panel presentation and vault locking. Opening
the panel does not cancel a search. Explicit lock preserves public query and
results and does not cancel an unrelated public-search task. Terminal stop is
the operation that cancels all remaining public work. Host publications are
serialized, so a search update carries the current non-sensitive unlock state
without racing the presentation-owner acknowledgement for selection, submit,
lock, or reconciliation.

## 14. Lazy Vault Selection

`requestUnlockPanel()` is the only initial selection trigger. It reserves the
selection operation and closes admission before awaiting the selector.
Concurrent requests join one host-owned completion and invoke the selector once.
The initiating request and every concurrent request wait on the same actor-owned
continuation set. Lock or stop cancels and abandons the selector task, then
releases every caller without waiting for a platform selector that ignores task
cancellation. A late selector result is discarded. Selection is generation
checked after every suspension.

## 15. No-Vault Behavior

A selector result of `none` creates no controller, performs no runtime call,
keeps the panel hidden, and publishes the fixed public `noVault` shell state.
Admission remains closed until a later reviewed vault-creation or selection
journey exists. If publication or owner acknowledgement fails, reconciliation
preserves `noVault`; a successful barrier cannot convert it to ordinary locked
state or reopen selection admission.

## 16. Selection Failure

A selection error creates no controller and exposes no underlying error. The
shell uses a fixed non-sensitive unavailable state. Its ready-state publication
and owner installation are acknowledged before admission can reopen.

## 17. Lazy Shared Unlock Controller

A validated selected identifier creates exactly one controller through
`AtlasVaultUnlockPresentationControllerBuilding`. Controller creation is lazy,
and repeated panel requests reuse the retained controller for the current host
lifetime. Controller state is revalidated after awaits so lock or stop cannot be
overwritten by late selection completion.

## 18. Local-Key-Only Production Capability

The host always passes `AtlasVaultUnlockCapabilities.currentProduction` to the
controller builder. That capability exposes local key only. Passphrase and
recovery methods remain unavailable, and the host adds no credential provider.

## 19. Process-Global Unlock Admission

Admission is actor-owned and closed before selection, method changes, submit,
cancel, disappearance, reconciliation, lock, lifecycle lock events, and stop.
Ready presentation state may be proposed to the owner while admission remains
closed. The host opens actual admission only after pipeline acknowledgement,
owner acknowledgement, generation revalidation, and safe lifecycle checks.

## 20. Host-Owned Submit Task

One unstructured host-owned task calls the shared controller. The host retains
that task through terminal completion. A concurrent duplicate cannot dispatch a
second request. A duplicate secret-bearing submission is cleared through the
reviewed secret-buffer `clear()` operation without the host reading secret
bytes.

## 21. Caller Cancellation

Caller cancellation does not cancel or orphan the host-owned submit task. The
actor continues to terminal completion and generation-checks the result.
Explicit host cancel, panel disappearance, lock, lifecycle, or stop controls
the task through the shared controller and reconciliation policy.

## 22. Unlock Success Verification

A controller result of `unlocked` is insufficient by itself. The host queries
authoritative runtime status and requires exactly `unlocked` on the same host
generation. It then publishes only a private-free unlocked status and projects
`unlockedTransition`. It never requests runtime private state.

## 23. Ordinary Failure Handling

Locked, failed, cancelled, timed-out, and unavailable outcomes are reconciled
against runtime status. Admission can reopen only when runtime is proven locked
or a fixed failed status has been explicitly locked and rechecked. Activating,
locking, saving, unlocked, or otherwise uncertain runtime status enters the
private-free reconciliation barrier.

## 24. Cancel And Disappearance

Without an active submit, cancel and disappearance forward to the shared
controller, hide the panel, and acknowledge a private-free ready state before
reopening admission. With an active submit, the host closes admission, advances
generation, requests controller cancellation or disappearance, and awaits the
retained task. A late success or uncertain terminal state enters reconciliation
and cannot publish stale unlocked state.

Panel callbacks are accepted only while host lifetime is started. A callback
arriving during reconciliation cannot advance generation or invalidate the
in-flight barrier. After any controller or submit suspension, the callback
rechecks lifetime: terminal stop is joined as terminal, existing reconciliation
is joined as nonterminal, and an already stopped host remains stopped.

## 25. Host Reconciliation

Reconciliation closes admission and advances generation before suspension. It
publishes a fixed non-interactive locking state, waits for active submit work,
checks runtime, commands lock unless runtime is already stably locked, notifies
the controller with `hostDidLock()`, publishes locked private-free state, awaits
owner reset, verifies observable private freedom, and verifies final runtime
status is exactly locked.

Only a complete barrier discards the controller and selected identifier and
returns ordinary locked state.

## 26. Reconciliation Retry

Pipeline, owner, or runtime verification failure leaves the host in a
non-interactive reconciliation state. No unlock is admitted. A later explicit
`lock()` retries the same barrier. A successful retry installs and acknowledges
the ready shell while admission is still closed, then atomically reopens it.

## 27. Explicit Lock

`lock()` is explicit and coalesces with an in-flight barrier. It closes
admission first, contains active unlock work, commands runtime lock as needed,
notifies the controller, and completes both presentation acknowledgements.
Public search state is preserved. Controller and selected identifier are cleared
only after barrier success.

## 28. Lifecycle Event Handling

Every lifecycle event is forwarded to the neutral lifecycle coordinator. The
host adds no platform notification framework. `didBecomeActive` and
`protectedDataBecameAvailable` never unlock; they may reopen admission only when
runtime is locked, no grace lock is pending, and host lifecycle state is safe.

Close events are recorded and forwarded while the host is inactive or starting.
They invalidate admission before suspension, so an event received during
snapshot restore or owner acknowledgement cannot be lost. Start performs no
runtime or lifecycle query. With no prior close event, safe initial lifecycle
flags permit admission only after both start acknowledgements. A close event
before or during start keeps admission closed until a later post-start safe
lifecycle event proves admission may reopen.

`willResignActive` closes admission and contains active submit work.
Lock-producing events complete host-owned private-free barriers before return.

## 29. Background Fail-Safe Policy

`didEnterBackground` closes admission, forwards the event, and immediately
completes a private-free lock barrier. The first production host does not depend
on an unobserved future grace callback to remove unlocked presentation state.

## 30. Protected-Data Policy

`protectedDataBecameUnavailable` immediately completes the lock barrier.
`protectedDataBecameAvailable` never unlocks and cannot reopen admission unless
runtime and lifecycle checks are safe. No LocalAuthentication or platform
protected-data API is imported by the host.

## 31. Explicit Stop

`stop()` is explicit and coalesced. It closes admission, cancels public search
and selection work, contains active unlock work, runs a terminal runtime and
presentation barrier, finishes the presentation pipeline, and leaves the host
terminal. A pipeline that started before a failed start is still finished by a
later stop. Cancellation completion also clears `isSearching` before the
terminal private-free state is published.

## 32. Terminal Stop Policy

Stop before start performs no dependency call and becomes terminal. Stop after
start commands runtime lock even if runtime initially reports locked. A terminal
stop request upgrades an in-flight nonterminal barrier without allowing that
barrier to reopen admission. Failed acknowledgement remains a terminal,
non-interactive reconciliation flow; restart is not supported. A late cancel or
disappearance completion cannot replace stopped lifetime with nonterminal
reconciliation. Stop also joins a retained start operation before deciding
whether an active presentation pipeline requires the terminal barrier.

## 33. Host Generation

`AtlasVaultProductionHostGeneration` is Hashable, Sendable, process-local, and
opaque. Its token is private and its descriptions are redacted. The host
advances generation to reject late start, selection, submit, presentation,
owner, lock, lifecycle, and stop completions. Public search has an independent
token and does not advance this unlock-admission generation.

## 34. Host-Owned Presentation Source

`AtlasVaultProductionPresentationPipeline` owns a private sequenced update
source, one existing observable presentation adapter, and one retained anchor
subscription. Construction starts none of them. Explicit `start()` establishes
and acknowledges the observation handshake exactly once.

The host cannot publish arbitrary snapshots through the contract. It must first
construct `AtlasVaultPrivateFreePresentationSnapshot`, whose initializer rejects
non-nil private state.

## 35. Presentation Sequencing

Accepted publications receive monotonically increasing private sequence values.
The source retains at most the newest undelivered update. Multiple subscribers
use the existing observable adapter's newest-value buffering. Subscriber
cancellation removes only that subscriber. The host grants one FIFO publication
permit at a time across the pipeline and MainActor owner reset. This prevents
reentrant public-shell updates from overtaking owner acknowledgements while
allowing public search and unlock admission to keep independent generations.

Sequence values, subscriber counts, and current status are absent from public
descriptions.

## 36. Pipeline Acknowledgement

Publication waits until the observable adapter has consumed the sequence. The
pipeline then verifies that the adapter's current snapshot equals the intended
private-free snapshot. Rejected private payloads are not sanitized and emitted;
they fail at wrapper construction and leave current state unchanged.

Terminal source finish drains activation, observation-start, sequence, delivery,
and acknowledgement waiters exactly once. Wait helpers called after finish
return immediately, so teardown cannot strand a start or test-maintenance
continuation.

## 37. Presentation-Owner Reset Acknowledgement

`AtlasVaultProductionPresentationOwnerResetting` is MainActor-isolated and
receives only sanitized locked-shell flow plus an opaque host generation. It
receives no runtime, vault identifier, key, path, or service object.

The host revalidates generation after the owner await. Tests suspend the
MainActor fake deterministically and prove that admission remains closed until
acknowledgement. Phase 2D-56 does not implement the concrete owner.

## 38. Private-Free Barrier

The barrier order is:

1. close admission and invalidate generation;
2. publish non-interactive reconciliation state;
3. retain and await active submit work;
4. inspect and, when required, lock runtime;
5. notify the shared controller;
6. publish and acknowledge locked private-free presentation;
7. await owner reset for a new generation;
8. verify observable private freedom;
9. verify runtime is exactly locked;
10. acknowledge the intended ready shell while admission remains closed;
11. atomically clear private-operation ownership and reopen admission.

Failure at any gate remains non-interactive and retryable, or terminal for
stop. A no-vault shell remains no-vault with admission closed across both
successful and failed barriers.

## 39. No Private-State Access

Production host source does not reference or call runtime `privateState()`,
`AtlasVaultPrivateStateSnapshot`, `AtlasVaultHydratedState`, or
`AtlasVaultPrivatePresentationState`. The only presentation snapshots it creates
contain `privateState: nil`.

## 40. No Mutation Or Save Behavior

The host does not call runtime `apply`, mutation, save, or private compatibility
operations. It does not reference saved searches, saved jobs, application notes,
profile snippets, drafts, or generated-document references.

## 41. No App-Entry Or Navigation Wiring

This phase adds no SwiftUI, app entry, root view, navigation, search view model,
production call site, or platform notification subscription. The implementation
remains an unwired library surface.

## 42. Error And Diagnostic Redaction

Host errors are fixed `stopped` and `presentationUnavailable` classes. Public
descriptions expose no query, job identifier, vault identifier, path, key,
sequence, generation, subscriber count, dependency identity, or underlying
localized error.

## 43. TDD Evidence

The red checkpoint referenced the intended Phase 2D-56 types before they
existed and failed compilation for those missing types only. Production source
was added after that checkpoint. Deterministic actors and checked continuations,
not sleeps, drive pipeline delivery, snapshot restore, public search, selection,
submit, owner acknowledgement, runtime lock, and barrier races.

## 44. Test Coverage

Focused tests cover construction, concrete builders, explicit start, optional
snapshot restore, public search supersession, search independence, lazy
selection, shared controller creation, local-key capability, duplicate submit,
caller cancellation, runtime success validation, late completion containment,
barrier failure and retry, lock coalescing, lifecycle policy, terminal stop,
owner suspension, failed-start teardown, presentation sequencing,
subscriptions, private-state rejection, terminal finish, redaction, source
guards, the six-file allowlist, forbidden paths, and artifact absence. Search
completion during suspended selection and submit proves that public-only
publication does not invalidate unlock admission. Additional regressions cover
pre-start source activation, lifecycle close events before and during start,
terminal search-indicator cleanup, and no-vault preservation across a failed
publication barrier. Concurrent pipeline starts coalesce behind one completed
observation handshake. Panel callbacks during reconciliation and late callback
completion after terminal stop use deterministic suspension-gate regressions.
Additional teardown regressions cover unfulfilled pipeline waiter release,
terminal stop during the presentation-start handshake, and immediate release of
all callers when a suspended selector is abandoned.

## 45. Go/No-Go Update

- Public adapters: implemented.
- Vault selector: implemented.
- Production-host contract: implemented.
- Production presentation pipeline: implemented.
- Runtime-neutral production host actor: implemented.
- Process-global unlock admission: implemented in one host actor.
- Authoritative reconciliation: implemented.
- Owner-reset acknowledgement seam: implemented and testable.
- Concrete MainActor presentation owner: not implemented.
- Production composition root: not implemented.
- Platform lifecycle adapter: not implemented.
- App-entry wiring: blocked.
- Private rendering: blocked.
- Passphrase/recovery: blocked.

## 46. Deferred Work

The following remain outside Phase 2D-56:

- a concrete MainActor production presentation owner;
- production-like composition of public adapters, runtime, lifecycle,
  presentation, unlock coordinator, and host builder;
- platform lifecycle adapter design and injected test seams;
- actual app-entry and navigation wiring;
- private rendering and post-unlock private journeys;
- passphrase and recovery providers;
- migration and cloud behavior.

## 47. Next Product Gate

Phase 2D-57 must implement a reviewed MainActor production presentation owner
and an isolated, production-like but still unwired composition harness/root. It
must assemble the Phase 2D-55 concrete public adapters, runtime composition,
lifecycle coordinator, production presentation pipeline, unlock request
coordinator, and concrete production-host builder. It must also define platform
lifecycle adapter design and injected test seams. It must not modify the actual
app entry, replace production navigation, render private state, add
passphrase/recovery providers, or implement migration or cloud sync.
