<!-- Phase 2D-56 repository boundary. -->

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

An explicit nonterminal lock requested during that handshake records one
host-private deferred lock intent and closes transient admission synchronously.
It does not advance host generation, change `starting` lifetime, call runtime,
publish teardown state, or invoke the owner. The lock caller joins the retained
start operation. A successful start includes the pending intent in its final
admission calculation, acknowledges and returns a private-free non-interactive
flow, and only then lets one ordinary barrier consume all concurrent startup
lock requests. No panel selection can enter between start completion and that
barrier because actor admission remains closed. Caller cancellation does not
erase the host-owned intent. A failed start clears the intent without running a
barrier against an inactive pipeline.

If terminal stop arrives during that handshake, stop first marks lifetime as
stopping and then joins the retained start operation. A successfully started
pipeline is finished through the terminal barrier; if the handshake never
started or failed, stop reaches the conservative locked terminal state without
publishing through an inactive pipeline. Terminal intent also clears and
dominates any deferred nonterminal lock, whose callers join terminal teardown
instead of starting a later ordinary barrier.

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

Starting a new explicit search commits its query immediately, clears the prior
query's jobs, and sets freshness to `unavailable` before service work begins.
The searching shell therefore never presents one query with another query's
result set. A failed search retains the new query but finishes with no jobs,
unavailable freshness, unavailable service status, and no searching indicator.
The host does not retain a hidden previous-result cache or roll results back.

## 11. Public-Search Task Ownership

The host creates unstructured host-owned public-search service tasks through
`AtlasPublicJobSearching`. Every created task is retained until terminal
completion. The current-operation marker controls result authority separately
from the private retained-operation collection: supersession cancels the prior
task and immediately starts the new task, but does not abandon ownership of the
older task. A superseded completion removes its retained handle before current
operation and generation checks, then returns the fixed unavailable result
without mutating public state.

Caller cancellation does not transfer ownership away from the host. Completed
tasks are removed rather than accumulated, and retention stores no previous
query, result, search history, or rollback state. Search never selects a vault
or invokes runtime, lifecycle, unlock, or private compatibility services.

## 12. Search Generation And Stale-Result Rejection

Each search receives a host-local monotonically advancing token and operation
identifier. Completion must match the current operation, generation, and host
lifetime before it can update the shell. A late cancelled or superseded result
cannot overwrite a newer result or a terminal host. This public-search token is
separate from the unlock-admission generation: a valid public-only completion
does not invalidate an unrelated vault selection or unlock submission.

Once those checks pass, success installs only the current query's jobs with
available service status and current freshness. Failure keeps the current query
with an empty result set and unavailable service/cache status. Superseded
completion cannot restore either an earlier result set or its freshness.

Queries remain public shell inputs but are absent from host and error
descriptions.

## 13. Public Search During Unlock And Lock

Public search is independent of panel presentation and vault locking. Opening
the panel does not cancel a search. Explicit lock preserves public query and
results and does not cancel an unrelated public-search task. Terminal stop is
the operation that drains all remaining public work. It captures current and
superseded unfinished tasks, cancels every captured task before awaiting any
one, and awaits every task before terminal completion. A service that ignores
cancellation can therefore delay stop; this is preferred to allowing work
created by the host to outlive the stopped host. No detached cleanup monitor or
timeout abandons that work. Host publications are serialized, so a search
update carries the current non-sensitive unlock state without racing the
presentation-owner acknowledgement for selection, submit, lock, or
reconciliation.

## 14. Lazy Vault Selection

`requestUnlockPanel()` is the only initial selection trigger. It reserves the
selection operation and closes admission before awaiting the selector.
Concurrent requests join one host-owned completion and invoke the selector once.
The initiating request and every concurrent request wait on the same actor-owned
continuation set. Lock or stop cancels and abandons the selector task, then
captures the current private-free flow and releases every caller before the
barrier performs its first await. Callers therefore do not wait for either a
platform selector that ignores task cancellation or a slow presentation/runtime
barrier. A late selector result is discarded. Selection is generation checked
after every suspension.

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
lifetime. A retained controller left in `cancelled` by an ordinary panel cancel
is reset through its existing `select(nil)` path before the next panel is shown;
selection and controller construction are not repeated. Controller state is
revalidated after awaits so lock or stop cannot be overwritten by late
completion.

## 18. Local-Key-Only Production Capability

The host always passes `AtlasVaultUnlockCapabilities.currentProduction` to the
controller builder. That capability exposes local key only. Passphrase and
recovery methods remain unavailable, and the host adds no credential provider.

## 19. Process-Global Unlock Admission

Admission is actor-owned and closed before selection, method changes, submit,
cancel, disappearance, reconciliation, lock, every lifecycle safety check, and
stop. Foreground and protected-data-availability events synchronously close
both actor admission and the public shell before their first suspension. Ready
presentation state may be proposed to the owner while admission remains closed.
The host opens actual admission only after pipeline acknowledgement, owner
acknowledgement, generation revalidation, and safe lifecycle checks.

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
controller. Ordinary pre-success terminal states hide the panel and acknowledge
a private-free ready state before reopening admission. A callback may also
arrive after submit completion and successful activation. The controller maps
that post-success callback to `hostReconciliationRequired`; the host treats
either `unlocked` or `hostReconciliationRequired` as uncertain and bypasses the
ordinary reopen path for both cancel and disappearance. It retains a
non-interactive reconciliation flow and runs the complete private-free barrier.

With an active submit, the host closes admission, advances generation, requests
controller cancellation or disappearance, and awaits the retained task. A late
success or uncertain terminal state enters reconciliation and cannot publish
stale unlocked state. Post-success callback reconciliation has no automatic
retry. A barrier failure remains non-interactive with admission closed, and a
later explicit `lock()` may retry it.

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
returns ordinary locked state. This includes barriers entered from a
no-active-submit post-success cancel or disappearance: runtime is
authoritatively locked, `hostDidLock()` is delivered, pipeline and owner
acknowledgements complete, observable private freedom is verified, and final
runtime status is exactly locked before admission may reopen.

## 26. Reconciliation Retry

Pipeline, owner, or runtime verification failure leaves the host in a
non-interactive reconciliation state. No unlock is admitted. A later explicit
`lock()` retries the same barrier. A successful retry installs and acknowledges
the ready shell while admission is still closed, then atomically reopens it.

## 27. Explicit Lock

`lock()` is explicit and coalesces with an in-flight barrier. It closes
admission before its first await. During `started` or `reconciling` lifetime it
retains the reviewed generation advance and private-free barrier behavior:
active unlock work is contained, runtime is locked as needed, the controller is
notified, and both presentation acknowledgements complete. During `starting`,
the deferred-intent policy in section 8 applies instead; no generation or
barrier work begins until successful startup. It has no admission-open
runtime-status fast path; an already-locked runtime follows the same
private-free barrier without an unnecessary lock command. Public search state
is preserved. Controller and selected identifier are cleared only after
barrier success.

## 28. Lifecycle Event Handling

Every lifecycle event is forwarded to the neutral lifecycle coordinator. The
host adds no platform notification framework. `didBecomeActive` and
`protectedDataBecameAvailable` never unlock. Before forwarding either event, the
host synchronously closes transient public unlock admission and captures, but
does not advance, the current host generation. It does not clear persistent
lifecycle eligibility merely because a safety check started. This prevents the
safe event itself from invalidating valid selection or submit work.

Each lifecycle event receives a private monotonic revision. A newer safe or
close event makes an older completion stale, and only the current safe revision
may commit lifecycle eligibility or clear the current in-progress marker. A
close event synchronously persists lifecycle ineligibility and invalidates the
older check. The revision is process-local, private, and absent from public
state and diagnostics.

Lifecycle handling and lifecycle status are awaited before persistent
eligibility is derived solely from active state, protected-data availability,
termination, pending grace lock, and lifecycle failure. Runtime state, host
lifetime, selection, submit, barrier, stop, and presentation acknowledgement do
not alter that persistent fact. An inactive or starting host records the fact
without calling runtime or publishing through an unready pipeline. Start waits
for an in-progress safe check before selecting and acknowledging its final
public target, so a safe event during restore or owner reset needs no second
lifecycle event.

For a started host, runtime status is then awaited and generation, lifetime, and
lifecycle revision are revalidated after every suspension. Actual admission may
reopen only while lifecycle eligibility is true, runtime is exactly locked, no
safe check, selection, submit, barrier, or stop is active, vault status is not
`noVault`, and reconciliation is absent. The host publishes one private-free
interactive or closed target while actor admission remains closed. Actor shell
and admission state commit only after pipeline and owner acknowledgement and a
final lifecycle-revision check. If a selection or submit reaches its terminal
state during acknowledgement, the host republishes the now-current target
before clearing the safe-check marker. Ordinary publications also capture that
marker and cannot commit a target calculated on the other side of the marker's
transition. Upgrading a previously closed target requires a fresh authoritative
runtime-status read, and activating or unlocked controller state never permits
admission. Acknowledgement failure enters the existing private-free barrier.

A later generation-winning lock, lock-producing lifecycle event, or stop makes
the safe-reopen completion stale, so it cannot publish or reopen over the newer
state. A safe event arriving while selection or submit is active keeps admission
closed without invalidating that operation merely by changing generation. When
the lifecycle result is safe, the persistent eligibility remains true; the
operation's reviewed terminal path can reopen admission without another
lifecycle event. A temporarily activating, unlocked, or saving runtime likewise
keeps actual admission closed without converting lifecycle eligibility into a
persistent denial.

Close events are recorded and forwarded while the host is inactive or starting.
They invalidate admission before suspension, so an event received during
snapshot restore or owner acknowledgement cannot be lost. Start performs no
runtime or lifecycle query. With no prior close event, safe initial lifecycle
flags permit admission only after both start acknowledgements. A close event
before or during start keeps admission closed. A safe event before or during
start records authoritative eligibility without runtime, selection, controller,
or presentation work, and successful start uses that result after its normal
acknowledgements.

`willResignActive` closes admission and contains active submit work.
Lock-producing events complete host-owned private-free barriers before return.

## 29. Background Fail-Safe Policy

`didEnterBackground` closes admission, forwards the event, and immediately
completes a private-free lock barrier. The first production host does not depend
on an unobserved future grace callback to remove unlocked presentation state.

## 30. Protected-Data Policy

`protectedDataBecameUnavailable` immediately completes the lock barrier.
`protectedDataBecameAvailable` never unlocks and cannot reopen admission unless
the complete lifecycle/runtime predicate is current and both private-free
presentation acknowledgements succeed. An unsafe result is itself published and
owner-acknowledged as closed. No LocalAuthentication or platform protected-data
API is imported by the host.

## 31. Explicit Stop

`stop()` is explicit and coalesced. It closes admission, cancels public search
and selection work, contains active unlock work, runs a terminal runtime and
presentation barrier, finishes the presentation pipeline, and leaves the host
terminal. Its search drain invalidates current result authority, captures every
retained current or superseded task, issues cancellation to all of them, and
only then awaits each terminal completion. The local capture preserves host
ownership while the drain runs, and completion cleanup is idempotent with the
search caller's own cleanup. A pipeline that started before a failed start is
still finished by a later stop. Search-drain completion also clears
`isSearching` before the terminal private-free state is published.

Stop advances host generation synchronously with entering stopping lifetime and
closing admission, before it creates or awaits teardown work. It also abandons
selection and resumes every panel caller before awaiting either a retained start
operation or a cancellation-ignoring public search. Any later request for a
nonterminal barrier inherits this terminal intent rather than replacing stopping
or stopped lifetime with reconciliation.

## 32. Terminal Stop Policy

Stop before start performs no dependency call and becomes terminal. Stop after
start commands runtime lock even if runtime initially reports locked. A terminal
stop request upgrades an in-flight nonterminal barrier without allowing that
barrier to reopen admission. The terminal request revokes any stale publication
permit only after the MainActor owner has acknowledged a newer generation
fence. It cancels and replaces the nonterminal barrier operation and starts the
terminal barrier without awaiting the stale operation. The replaced operation
checks its operation identity after every suspension, while the owner rejects
the fenced stale generation, so neither can mutate terminal state when a late
dependency returns. Failed acknowledgement remains a terminal,
non-interactive reconciliation flow; restart is not supported. A late cancel or
disappearance completion cannot replace stopped lifetime with nonterminal
reconciliation. Stop also joins a retained start operation before deciding
whether an active presentation pipeline requires the terminal barrier. During
terminal teardown, the barrier generation supplied to
`supersedePresentationGeneration(_:)` is also the exact generation used by the
first reconciliation reset. The host does not advance between those two owner
calls. Only after that reset commits the superseded generation may later
ordinary generations install the locked and final terminal owner states. The
owner remains on reconciliation flow through pipeline finish and a final
authoritative runtime `.locked` check. Only then does a new generation install
and acknowledge terminal locked owner flow. A finish or runtime-verification
failure therefore cannot leave the owner falsely locked.
Repeated stop callers and `willTerminate` join this same terminal drain. An
explicit nonterminal vault lock remains independent and neither cancels nor
awaits public-search service work solely because the vault is locking.

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
The source retains at most the newest undelivered update. When a newer update
replaces an occupied one-slot buffer, the source removes every acknowledgement
waiter for the overwritten sequence and resumes each exactly once with `false`.
The overwritten update is never delivered later; the new buffered update
remains eligible for delivery. Because `sendAndWait()` and `enqueue(_:)` run on
the same source actor, the earlier call registers its acknowledgement
continuation before a later enqueue can supersede it. No failed-sequence set,
pending-update array, or unbounded queue is needed. Multiple subscribers use
the existing observable adapter's newest-value buffering. Subscriber
cancellation removes only that subscriber. The host grants one FIFO publication
permit at a time across the pipeline and MainActor owner reset. This prevents
reentrant public-shell updates from overtaking owner acknowledgements while
allowing public search and unlock admission to keep independent generations.
Each logical publication captures its intended locked-shell owner flow before
awaiting the permit or pipeline. Actor reentrancy may mutate later host state,
but that mutation cannot be substituted into the already-started publication's
owner acknowledgement.
An interactive captured flow also carries the current lifecycle revision and
safe-check marker as an admission fence. The host revalidates that fence after
acquiring the FIFO permit and again after pipeline acknowledgement, before
calling the owner. It revalidates once more after owner acknowledgement before
treating the logical publication as accepted. A safe lifecycle check that starts
while an interactive publication is queued or suspended therefore makes that
publication stale instead of allowing callers to commit its obsolete admission
decision. Closed flows remain eligible to carry current public-shell data while
lifecycle validation proceeds.
Each permit has a host-private identity. Terminal stop can revoke a stale permit
and fail its queued waiters closed after installing the owner-generation fence,
allowing terminal publication to proceed without waiting for an abandoned
nonterminal owner acknowledgement. A late holder cannot release or replace the
terminal permit.

Sequence values, subscriber counts, and current status are absent from public
descriptions.

## 36. Pipeline Acknowledgement

Publication waits until the observable adapter has consumed the sequence. The
pipeline then verifies that the adapter's current snapshot equals the intended
private-free snapshot. Rejected private payloads are not sanitized and emitted;
they fail at wrapper construction and leave current state unchanged.

Intentional one-slot supersession is a negative acknowledgement, not an
acknowledgement timeout. Overwrite removes old waiters before resuming them
`false` and does not advance `acknowledgedSequence`; that value advances only
when the observation requests its next update after actual delivery. Successive
overwrites therefore terminate every older publisher while preserving the
latest buffered publisher until delivery, finish, or observation invalidation.

Terminal source finish drains activation, observation-start, sequence, delivery,
and acknowledgement waiters exactly once. Wait helpers called after finish
return immediately, so teardown cannot strand a start or test-maintenance
continuation.

Finish does not enqueue and await one more sequenced publication. It terminally
finishes the source first, which causes the existing observable adapter to emit
its fixed locked private-free terminal snapshot and close subscribers. The
pipeline then joins its retained observation task and verifies that observable
current state is exactly that locked snapshot. A previously suspended publish
is resumed as unacknowledged instead of blocking teardown.

## 37. Presentation-Owner Reset Acknowledgement

`AtlasVaultProductionPresentationOwnerResetting` is MainActor-isolated and
receives only sanitized locked-shell flow plus an opaque host generation. It
receives no runtime, vault identifier, key, path, or service object.

The owner contract exposes `supersedePresentationGeneration(_:)`. Before
revoking a stale permit, terminal teardown awaits this MainActor generation
fence. An owner must reject a reset whose generation is no longer current and
must recheck after every suspension before mutating presentation. Supersede is a
stale-work fence, not permanent exclusivity for one token. Its first accepted
reset must use the exact superseding generation. A successful commit of that
generation establishes the fence; only then may a later ordinary reset establish
its supplied opaque generation, and every reset may commit only while its exact
generation remains current. The host also revalidates generation after every
owner await. Pipeline and owner acknowledgement are one logical publication: the
owner receives the flow captured before the pipeline await, never a reentrant
replacement assembled after pipeline acknowledgement. Tests suspend both seams
deterministically and prove that admission remains closed until acknowledgement,
that a stale reset cannot reinstall pre-stop flow after terminal finish, and
that public-shell reentrancy cannot change an in-flight publication's owner
flow. Phase 2D-56 does not implement the concrete owner.

## 38. Private-Free Barrier

The barrier order is:

1. close admission and invalidate generation;
2. publish non-interactive reconciliation state;
3. retain and await active submit work;
4. inspect and, when required, lock runtime;
5. notify the shared controller;
6. publish and acknowledge locked private-free pipeline presentation;
7. keep a nonterminal presentation owner in reconciliation for the new
   generation;
8. verify observable private freedom;
9. verify runtime is exactly locked;
10. capture lifecycle revision and safe-check ownership, then acknowledge the
    intended ready shell while admission remains closed;
11. when the lifecycle fence changed during acknowledgement, acknowledge a
    reconciliation owner state and repeat authoritative runtime status, lock,
    and final locked-status verification;
12. acknowledge the current ordinary target, then atomically clear
    private-operation ownership and reopen admission.

Every barrier operation has a private identity that is revalidated after each
await. Terminal stop replaces an in-flight nonterminal identity immediately,
establishes a newer owner-generation fence, revokes the stale publication permit,
and begins terminal publication with an exact-generation reconciliation reset.
The nonterminal barrier retains its existing pre-publication generation advance;
the terminal barrier deliberately does not advance until the exact superseded
generation has committed. Late stale runtime, controller, pipeline, or owner
completion therefore returns the current private-free flow without changing
terminal state.

For a nonterminal barrier, advancing from step 10 to the ordinary commit in
step 12 requires the captured lifecycle revision and safe-check marker to remain
unchanged after owner acknowledgement. If a safe lifecycle check begins or
completes during that await, the barrier keeps
admission closed and republishes a reconciliation owner target under the same
host generation with a matching private-free locking pipeline status. Before it
can publish an ordinary target again, it obtains a
fresh authoritative runtime status, commands lock when that status is not
locked, and verifies a final locked status. Every one of those awaits is fenced
by the barrier identity, host generation, lifecycle revision, and safe-check
marker. Failed relock or verification leaves host and owner in reconciliation;
successful proof permits only a newly acknowledged current target. A stale
pre-check decision therefore cannot reopen selection while lifecycle safety is
being established or after runtime becomes unsafe during owner acknowledgement.

Failure at any gate remains non-interactive and retryable, or terminal for
stop. A no-vault shell remains no-vault with admission closed across both
successful and failed barriers. A nonterminal owner receives ordinary locked
flow only in step 10, after observable and authoritative runtime verification;
a late runtime-lock failure therefore leaves both host and owner visibly in
reconciliation.

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

Cycle 11 first captured deterministic red evidence for both remaining
invariants. Safe lifecycle tests suspended independently in lifecycle handling,
lifecycle status, and runtime status and proved admission remained exposed;
owner-gated cases proved unsafe targets were not acknowledged as closed.
Separate post-success cancel and disappearance tests proved uncertain callback
states bypassed reconciliation. Production behavior changed only after both red
states were recorded.

Cycle 12 captured a second deterministic red checkpoint for the distinction
between lifecycle eligibility and transient admission. The red suite proved
safe events before and during start skipped authoritative lifecycle status,
selection and submit blockers persisted a false lifecycle permission, a
temporary runtime state poisoned a later lock barrier, and concurrent safe
events both published. A separate factory regression proved the Git helper's
`Foundation.Process` use lacked a macOS compile boundary.

Cycle 13 captured deterministic red evidence for two acknowledgement races. A
suspended pipeline publish allowed a second public search to replace the owner
flow of the first logical publication. Separately, a safe lifecycle event begun
during the ordinary barrier owner acknowledgement allowed the stale barrier
decision to reopen admission and invoke vault selection. Both tests failed on
the prior exact head without timing delays.

Cycle 14 captured deterministic red evidence for the remaining lifecycle-fence
windows. A safe event started while an interactive publication was queued behind
the FIFO permit, and the stale captured flow was delivered to the owner. A
separate barrier test completed the safe event during ordinary owner
acknowledgement, changed runtime to unlocked, and proved the barrier reopened
from its earlier status result. The tests use checked-continuation publication,
lifecycle, and owner gates rather than timing delays.

Cycle 17 captured deterministic red evidence with a cancellation-ignoring
superseded search. The prior exact head completed terminal stop while that
service call remained active. The permanent regressions use independent
checked-continuation gates to retain current and multiple superseded tasks,
prove current-result authority remains separate, and verify all cancellation
requests precede terminal draining without sleeps.

Cycle 18 captured both final exact-head findings before production changes. A
presentation-start gate proved that explicit lock changed lifetime/generation
and made an otherwise successful start return `.stopped`. A real pipeline with
delivery suspended proved that replacing the buffered update left the older
publisher pending until the deadlock watchdog. Permanent regressions suspend
presentation start, snapshot restore, initial publication, and owner reset;
cover concurrent locks, caller cancellation, start failure, and terminal-stop
dominance; and prove no selector or runtime work enters early. Pipeline
regressions cover two and many successive overwrites, subscriber delivery of
only the newest buffered snapshot, monotonic sequence accounting, normal
delivery, and finish-time waiter release while preserving the one-slot bound.

The post-merge owner-generation follow-up captured deterministic red evidence
against an owner enforcing the concrete Phase 2D-57 fence. Terminal stop first
returned reconciliation after rejecting two post-supersede reset generations;
after a successful unlock, the same mismatch left the owner on
`unlockedTransition`. The permanent regressions require the first terminal reset
to commit the exact superseded generation before any later generation is
accepted, including terminal lifecycle handling and replacement of a suspended
nonterminal owner reset.

The Phase 2D-56F merge-stability follow-up captured deterministic red evidence
that the historical six-file assertion still read current working-tree and
untracked paths. A later reviewed phase therefore appeared as an invalid
Phase 2D-56 file. The repaired regression discovers the squash-introduction
commit dynamically, compares only its first-parent diff, and keeps current
artifact scanning separate. No production source or runtime behavior changed.

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
all callers when a suspended selector is abandoned. Terminal finish while a
publish is unacknowledged, selector release before a suspended owner barrier,
and immediate terminal generation invalidation are also covered. Final teardown
regressions hold a cancellation-ignoring public search while selection is
abandoned and hold a stale nonterminal owner acknowledgement while terminal stop
completes through a replacement barrier. The owner fake commits only the newest
generation after suspension, explicit lock closes admission before runtime
status, and a failed runtime verification keeps the owner in reconciliation.
Retained-controller coverage cancels and reopens the panel without reselection,
and terminal finish/runtime failures keep the owner in reconciliation.
Cycle 11 adds table-driven suspension checks for both safe-reopen events at all
three safety awaits, owner-gated closed and interactive targets, unsafe
acknowledgement failure and explicit-lock retry, stale reopen after a winning
lock, and non-invalidation of suspended selection. Callback regressions cover
post-success disappearance and cancellation, a defensive direct `unlocked`
result, full barrier effects, ordinary pre-success behavior, and failed-barrier
retry.
Cycle 12 adds safe events before start and during snapshot/owner suspension,
selection and submit terminal reopening without another lifecycle event,
temporary-runtime recovery, newest-safe-event ownership, and closed transient
admission while a safe check is in progress. Source coverage rejects assigning
temporary `mayReopen` results back into persistent lifecycle eligibility. The
publication fence ensures a target computed while a safe marker was active
cannot commit after that marker changes. A deterministic submit gate also proves
a locked runtime status read is not reused after the submit clears its transient
blocker while runtime becomes unlocked during owner acknowledgement.
Cycle 13 adds a pipeline suspension regression proving owner flow is captured
before publication awaits, plus a targeted interactive-owner gate proving a
nonterminal barrier cannot reopen admission across a newer lifecycle revision or
active safe-check marker. A structural guard enforces both capture and
post-acknowledgement fence shapes without comparing whole functions.
Cycle 14 adds a queued-publication regression that rejects every stale
interactive owner flow after a safe-check start. Barrier regressions cover both
fresh runtime outcomes after a completed safe event: failed relock remains
non-interactive reconciliation, while successful relock may reopen only after
the new status, lock, and final-status proof. The structural guard requires two
interactive lifecycle-fence checks before owner reset and the late runtime-proof
path.
Cycle 15 adds the matching post-owner fence: an interactive publication is not
reported as acknowledged when its lifecycle revision or safe-check ownership
changes while the owner reset is suspended. The source guard requires the two
pre-owner checks and the final post-owner check, with no further await before
the publication result is returned.
Cycle 16 adds deterministic query/result coherence coverage. A successful
search A is followed by a gated search B to prove that B clears A before its
service suspension and remains empty with unavailable freshness after failure.
The supersession regression also begins from a successful result and proves
that neither a cancelled search nor its former freshness can overwrite the
newer query. A source guard rejects hidden previous-result caches.
Cycle 17 adds cancellation-ignoring service coverage for current plus
superseded tasks, three-operation cancellation and drain ordering, completed
handle removal, responsive supersession, caller cancellation, late success and
failure containment, coalesced repeated stop, `willTerminate`, and nonterminal
vault-lock independence. Source guards require a private retained-operation
registry, distinct current marker, cancel-all-before-await loops, and reject
detached cleanup, timeout abandonment, and hidden result storage. Cycle 16
query/result/cache coherence remains unchanged.
Cycle 18 adds gated startup-lock coverage at every start suspension seam. It
proves the deferred intent closes admission without changing start generation
or lifetime, successful start returns non-interactive, concurrent callers share
one post-start barrier, terminal stop wins, failed start invokes no inactive
barrier, and caller cancellation cannot create an admission window. Real
pipeline coverage proves overwritten publishers return `false`, waiter entries
are removed before resumption, multiple overwrites all terminate, the newest
buffer remains deliverable to current state and subscribers, finish releases
the final publisher, acknowledged sequence advances only through delivery, and
no update queue replaces bounded newest-value buffering.
The owner-generation follow-up adds strict-owner terminal stop,
`willTerminate`, prior-unlocked-state replacement, and suspended stale-reset
coverage. A structural guard requires the terminal reconciliation generation to
be the barrier generation, while the strict fake rejects every different reset
until that exact generation commits.
The exact six-file assertion is a historical Phase 2D-56 invariant. It finds
the first commit that added
`AtlasVaultProductionHostTests.swift` by using an unbounded, reverse
path-introduction log, compares its merge base with `HEAD` to the introduction
commit so an ancestry failure reports both values, resolves its first parent
dynamically, and requires that parent-to-introduction diff to equal the reviewed
six files. It pins no commit SHA, branch, current-base diff, or commit subject.

Current working-tree and untracked paths are not unioned into that historical
set. The dedicated recursive artifact test remains the current-tree safety gate
for `.atlasvault` files and `.venv-review` directories. A shallow repository
cannot prove the historical first-parent scope, so the scope assertion throws
the fixed skip reason
`Historical Phase 2D-56 scope assertions require complete Git history`.
Marker-based shallow enumeration, active multi-commit branch expansion, and
phase-end expansion through `HEAD` are removed.

The Git inspection implementation, including `Foundation.Process`, remains
compiled only on macOS; unsupported Apple platforms throw a fixed `XCTSkip`
without an empty-output fallback. This historical assertion remains valid
after squash merge, on clean master, on Phase 2D-59 with its seven reviewed
files present, and on later reviewed phases. Factory, host, contract, private
state, service-access, side-effect, and redaction behavior tests are unchanged.

## 45. Go/No-Go Update

- Public adapters: implemented.
- Vault selector: implemented.
- Production-host contract: implemented.
- Production presentation pipeline: implemented.
- Runtime-neutral production host actor: implemented.
- Process-global unlock admission: implemented in one host actor.
- Authoritative reconciliation: implemented.
- Acknowledged lifecycle safe reopen: implemented with separate persistent
  lifecycle eligibility, transient admission, and per-await revision/generation
  revalidation.
- Cross-platform factory tests: Git/`Process` inspection is macOS-only and
  explicitly skipped elsewhere.
- Post-success cancel/disappearance reconciliation: implemented symmetrically.
- Explicit lock during startup: deferred, admission-closing, start-preserving,
  coalesced, and terminal-stop dominated.
- Buffered publication acknowledgements: overwritten sequences fail `false`
  while one-slot newest-value delivery remains bounded.
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
