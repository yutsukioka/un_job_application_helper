# AtlasVault Phase 2D-45 App-Host Composition Design

## 1. Purpose

Phase 2D-45 defines how a future app host will assemble public job search,
AtlasVault runtime services, lifecycle coordination, observable presentation,
and a dedicated public-only locked shell. The design preserves the existing
rule that public job data remains outside the vault while private user state is
available only as in-memory presentation after explicit unlock.

## 2. Design-Only Scope

This phase implements no Swift host protocol or other executable host type. It
proposes future host protocols in this document only and adds no app entry
point, SwiftUI view, lifecycle subscription, activation call, local-store
operation, or public-search call. It does not implement migration, plaintext
cleanup, cloud sync, recovery, onboarding, key rotation, or production launch.

## 3. Existing Runtime Composition

`AtlasVaultRuntimeFactory` constructs the root provider, Keychain adapter,
per-vault factory, saver, hydrator, merger, and atomic writer without invoking
them. The future host may construct this graph at process startup, but it must
not resolve Application Support, access Keychain, choose a vault, or create a
per-vault scope until explicit activation requires those operations.

## 4. Runtime Facade

`AtlasVaultRuntimeFacade` is the sole future command boundary for activation,
lock, and encrypted private mutations. The host must not bypass it to call the
activation controller, private-state store, persistence coordinator, or record
crypto directly. Its actor serialization remains authoritative for competing
activation, save, and lock operations.

## 5. Lifecycle Coordinator

`AtlasVaultLifecycleCoordinator` accepts explicit, platform-neutral lifecycle
events and applies the configured lock policy to the runtime facade. It does
not subscribe to platform notifications. A future host-owned platform adapter
must translate process or scene events into `AtlasVaultLifecycleEvent` values.

## 6. Observable Presentation Adapter

`AtlasVaultObservablePresentationAdapter` is the in-memory distribution
boundary for sequenced, sanitized `AtlasVaultPresentationSnapshot` values.
The future host owns one adapter and its source. Views must consume this
presentation boundary rather than runtime actors or private compatibility
models.

The adapter's cancellation-safe stream, monotonic sequence, and fail-closed
source-completion behavior remain in force. Cancelling a subscriber alone is
not host teardown: the adapter can continue observing its source and retain its
latest snapshot without subscribers. Conversely, checking only
`currentSnapshot()` does not prove that a suspended `@MainActor` presentation
owner has replaced its previously published private snapshot.

The future host therefore owns an explicit, awaitable presentation-owner reset
seam. Before `stop()` returns, it must close private-presentation authorization,
invalidate the active presentation generation, cancel active unlock, mutation,
and public-search work, and command the `@MainActor` owner to install a locked,
private-free snapshot for that generation. The owner acknowledgement must
complete before the host cancels its UI-facing subscription. The host also
awaits `AtlasVaultRuntimeFacading.lock()`, requires its host-controlled source
to emit a monotonic private-free control update that remains admissible after
the private generation is invalidated (or to finish private-free), and verifies
the observable adapter is private-free. Stream delivery or adapter inspection
is defense in depth; neither substitutes for the owner acknowledgement.

The generation gate closes before the `@MainActor` hop, so late or buffered
private updates cannot race the reset. The host must not hold an isolation
critical section that can deadlock with the owner while awaiting that hop. A
later `start()` uses a fresh presentation generation and may not replay a prior
private snapshot.

## 7. Public Job-Search Service Boundary

Introduce a future narrow `AtlasPublicJobSearching` protocol or equivalent for
health, public search, public source metadata, public update metadata, and
public job detail requested from a public search result. Any detail-cache
restore, warmup, or write remains blocked until the separate provenance gate
passes. The protocol must not expose saved-search, tracker, note, snippet,
draft, or vault operations.

The locked shell receives this narrow service, never the full
`AtlasAPIClient`. This makes private compatibility endpoint calls
unrepresentable from the locked host path.

## 8. Public Cache Boundary

`AtlasPublicLocalSnapshot` may support locked public search because it excludes
private membership and private record data. Public snapshot ownership remains
separate from AtlasVault runtime ownership.

Existing detail-cache files remain excluded from locked use until a reviewed
provenance or namespace boundary proves that their requested keys, filenames,
content, writes, and warmup metadata derive only from public job data. The host
must not infer saved membership from public cache state.

## 9. Private Vault Boundary

Saved searches, saved jobs, application notes, profile snippets, and draft
metadata belong only to encrypted AtlasVault records and unlocked in-memory
presentation. The host must not place private values, counts, record IDs,
saved-only job keys, vault paths, or keys in public search state, public cache,
logs, errors, analytics, scene state, or app state restoration.

Record type remains inside the encrypted payload. The host and local-store
composition see only the reviewed encrypted-record envelope allowlist and do
not add semantic private metadata to plaintext coordination state.

## 10. Proposed App-Host Protocol

A future runtime-neutral `AtlasVaultAppHosting` protocol should expose only
host-level commands and snapshots, for example:

- `start()` and `stop()` for explicit host lifetime;
- public-search requests through the public-only service;
- unlock-request dispatch and cancellation through
  `AtlasVaultUnlockRequestCoordinating`;
- lock and mutation `apply` commands through `AtlasVaultRuntimeFacading`;
- lifecycle-event delivery;
- a UI-safe presentation subscription.

The protocol must not expose vault keys, secret buffers, hydrated records,
encrypted envelopes, filesystem URLs, Keychain clients, or mutable private
stores.

The dependency graph has two sibling branches:

- public host services -> public search and `AtlasPublicLocalSnapshot`;
- private host services -> unlock request coordinator, runtime facade,
  lifecycle coordinator, stateless presentation projection, and observable
  presentation.

Neither branch calls through the other. The host coordinates only public task
lifetime and private-free status until explicit activation succeeds.

The unlock request coordinator is the mandatory host boundary for passphrase,
recovery-key, local-key, timeout, and cancellation handling. It delegates its
validated activation request to the runtime facade. UI-originated unlock must
not call `AtlasVaultRuntimeFacading.activate` directly and thereby bypass
single-use request ownership, secret cleanup, or timeout behavior.

## 11. Proposed Test-Host Protocol

A future `AtlasVaultTestHosting` protocol should expose deterministic start,
stop, lifecycle-event, unlock-request dispatch/cancellation, mutation `apply`,
lock, public-search, and snapshot observation seams. Its mutation seam delegates
to `AtlasVaultRuntimeFacading.apply` and returns `AtlasVaultSaveOutcome`; it is
not a separate `save()` API. It may expose non-sensitive call counts and ordered
event labels for assertions, but never private request bodies, secrets, paths,
or payload descriptions. A raw facade activation seam may exist only inside
lower-level fake wiring; it is not part of the host contract.

## 12. Construction Versus Start

Construction assembles injected dependencies only and performs zero service
calls. `start()` may begin the fake or reviewed public snapshot path and create
an explicit observable presentation subscription. It must still present
locked, private-free state and must not activate the vault.

`stop()` is a fail-closed private-state transition, not subscription cleanup.
It must be idempotent, complete the cancellation and lock sequence in Section
6, and leave no reusable presentation generation containing private state.

Filesystem root resolution, Keychain lookup, vault load, decryption, and
hydration occur only after an explicit activation request crosses the runtime
boundary. Public-search startup remains independent.

The existing `ATLAS_REFERENCE_CAPTURE` environment route is not a host mode and
must be unselectable from production host composition, the Phase 2D-46 test
host, and the Phase 2D-47 locked shell. Those paths must not instantiate
`AtlasReferenceCaptureView`, whose initializer currently writes fixtures into
the normal public-cache root. Fake fixtures instead use injected in-memory
storage or an isolated temporary root.

## 13. No Automatic Activation

Launch, `start()`, scene creation, foreground entry, public search, public-cache
restore, preview creation, and lifecycle delivery must not activate or unlock
the vault. A locally stored key changes only the explicit activation input
source; it does not authorize automatic activation.

## 14. No Automatic Private Endpoint Refresh

Construction and `start()` must make zero saved-search or tracker compatibility
requests. Public refresh and foreground refresh must not call any helper that
also refreshes private state. A host must require an explicit, reviewed
unlocked bridge before any temporary compatibility behavior could be
considered.

## 15. Public Search While Locked

Locked state may search public jobs and restore the reviewed public snapshot.
Public search results must not be annotated with saved membership, private
notes, application status, or saved-only detail keys. Public-search failure
must not change vault status or reveal whether private state exists.

## 16. Dedicated Public-Only Locked Shell

The first locked UI must use a dedicated public-only shell. Its dependency
graph contains the public-search presentation boundary and a private-free vault
status snapshot, but no private panel factory, compatibility client, unlock
secret, mutation service, or direct runtime object.

## 17. Why AtlasRootView Cannot Be Reused Unchanged

`AtlasRootView` currently composes `AtlasSidebarView`, `SavedPanel`, and
`AtlasSearchViewModel`. That view model publishes saved searches and tracker
records and provides `refreshSidebarData()`. Reusing this hierarchy unchanged
would allow a locked view path to fetch and publish private compatibility state.

Visual hiding is insufficient because the fetch and publication path would
still exist. Reuse remains blocked until a separately reviewed design and
tests remove or gate that path.

## 18. Legacy Saved-Search/Tracker Panel Exclusion

The dedicated locked shell must not instantiate, render, preload, navigate to,
or retain legacy saved-search or tracker panels. Their models, selection cases,
task modifiers, and refresh callbacks must not be injected into the locked
hierarchy.

## 19. refreshSidebarData Prohibition While Locked

No locked-host command, lifecycle event, public refresh, view task, navigation
event, or preview may call `refreshSidebarData()` or an equivalent combined
private refresh. Test spies must prove zero calls before unlock and after every
lock or failed activation.

## 20. Compatibility Endpoint Reachability Boundary

The plaintext saved-search and tracker endpoints may be loopback-only,
LAN-reachable, or remote HTTP(S), depending on client base URL, server binding,
proxying, and deployment. They are compatibility surfaces, not encrypted
AtlasVault sync and not inherently local-only.

The locked host must never call them regardless of reachability. Any temporary
unlocked compatibility bridge requires a separate authenticated, authorized,
transport-aware transition design and review.

## 21. No Direct API Access From Private Presentation Layer

Private presentation consumes only snapshots produced through the runtime
facade, private-state projection, and presentation adapters. It must not call
`AtlasAPIClient`, `AtlasSearchViewModel`, compatibility endpoints, public cache,
record crypto, persistence, or Keychain to obtain private data.

## 22. Host-Owned Lifecycle Event Delivery

The future process host owns the adapter that translates platform events into
`AtlasVaultLifecycleEvent`. It serializes and deduplicates process or aggregated
scene signals before calling the lifecycle coordinator. Platform notification
registration and removal are deferred to a later app-host implementation phase.

The host also serializes lifecycle delivery with unlock-request ownership. For
an event whose policy can lock or cancel activation, it first invalidates and
awaits cancellation of the active `AtlasVaultUnlockRequest`, then delivers the
event to `AtlasVaultLifecycleCoordinating`. This closes the pre-activation
window in which secret derivation is running but the runtime facade is still
locked and therefore has no activation operation to cancel.

The host also owns a non-sensitive unlock-eligibility gate. Eligibility
requires a started host, an active process, available protected data, and no
stop or termination in progress. Before delivering `willResignActive`,
`didEnterBackground`, `protectedDataBecameUnavailable`, or `willTerminate`, the
host closes the gate and cancels any accepted unlock request. The gate remains
closed for the entire inactive interval, including every pending
`afterGracePeriod` timer, even when an already-unlocked runtime is temporarily
allowed to remain unlocked.

Unlock eligibility and private-presentation authorization are separate gates
that close in the same serialized lifecycle transition. Before an inactivity,
background, protected-data-loss, or termination event returns, the host
invalidates the private presentation generation, suppresses further private
source updates, directly commands the `@MainActor` owner to install a locked,
private-free snapshot, and awaits acknowledgement. It also publishes that
private-free state through the observable adapter as a monotonic control update
that remains admissible after invalidating the private generation. This
presentation barrier is immediate and independent of the runtime lock policy: a
grace period may keep the in-memory runtime session alive internally, but it
must not leave private presentation visible, retained by the UI owner, available
to a new subscriber, or eligible for scene or app-switcher capture.

Unlock dispatch and lifecycle delivery are serialized under the same host
authority. A request accepted before the gate closes is cancelled before the
lock-producing event returns. A request arriving after closure is rejected or
cancelled with a fixed non-sensitive result, clears its secret ownership, and
must not invoke derivation or facade activation. `didBecomeActive` may reopen
the gate only after lifecycle handling completes, protected data is available,
and the lifecycle coordinator confirms a terminal disposition for the matching
grace generation. An active event and
`AtlasVaultLifecycleStatus.hasPendingGraceLock == false` are not sufficient by
themselves: the current coordinator clears its pending fields before awaiting
`runtime.lock()`, so actor reentrancy can expose no pending timer while locking
is still in flight.

Future host integration therefore requires an explicit lifecycle-quiescence
seam, such as a matching completion generation plus a lock-transition phase.
For `afterGracePeriod(..., cancelOnActive: false)`, unlock eligibility remains
closed through timer expiry and until the coordinator reports that its awaited
runtime lock completed and the facade is stably locked. For cancellable grace,
the coordinator may instead report that the matching grace generation was
cancelled on active while the runtime remains stably unlocked. A superseded,
stale, or merely timer-free generation cannot reopen either gate. No replacement
request may consume or derive a single-use secret while the lifecycle lock is
pending or in flight.

Deferred grace expiry therefore cannot race with a newly dispatched unlock.
Private-presentation authorization follows the same fail-closed rule. If an
active transition validly cancels grace and the runtime remains unlocked, the
host may expose private state only through a fresh generation built from
current runtime state after all lifecycle checks pass; it must never replay the
obscured snapshot. If grace is not cancelled, presentation stays private-free
until the timer's runtime lock is confirmed complete and a later explicit
activation succeeds.

## 23. Host-Owned Observable Adapter Subscription

The process host owns the single observable adapter and the upstream source
that converts coherent runtime state into sequenced stateless-adapter output.
A future `@MainActor` presentation owner holds a cancellable subscription and
publishes one immutable snapshot at a time.

Scene consumers may subscribe to that process authority, but they must not
create independent runtime or private-state graphs. Cancellation must prevent
buffered private snapshots from being rendered.

The owner also implements a narrow future host-only reset operation such as
`installPrivateFreeSnapshot(generation:) async`. It atomically replaces its
published snapshot, discards buffered values from invalidated generations, and
returns only after the replacement is visible to its consumers. This operation
accepts no private payload and is not a general runtime mutation API. Host stop,
inactivity, backgrounding, protected-data loss, termination, and fatal
containment use it before cancelling subscriptions or returning control to a
caller that assumes presentation is cleared.

## 24. MainActor Boundary

Only the future UI-facing owner belongs on `@MainActor`. It receives immutable
`Sendable` presentation snapshots and public-search presentation values. Vault
keys, secret buffers, runtime services, lifecycle coordinators, tasks, and
private-state stores remain behind actor or protocol boundaries and are never
published.

## 25. Runtime Actor Boundary

The runtime facade, lifecycle coordinator, observable adapter, unlock
coordinator, and private-state store retain their actor isolation. The host
must await commands and compare request, generation, or sequence identity
before installing results. It must not use shared mutable singleton state.

## 26. Public Search Task Cancellation

The host owns public-search tasks separately from vault tasks. A newer search,
host stop, or scene teardown may cancel a public request without locking the
vault. Late public responses must be rejected by request identity and must not
mutate vault presentation.

## 27. Vault Activation Task Cancellation

The host owns one explicit activation task for the process. Lock, host stop,
background policy, protected-data loss, or user cancellation must propagate to
`AtlasVaultUnlockRequestCoordinating.cancel`. Dispatch must go through
`AtlasVaultUnlockRequestCoordinating.dispatch`; its injected activation closure
is the only bridge to the runtime facade. Late derivation or activation
completion must not restore private state after cancellation, timeout, or lock.

The host retains the active request identity until dispatch reaches a terminal
state. A lock-producing lifecycle event and dispatch completion are serialized
under host ownership: cancellation reserves the request's terminal state before
the lifecycle event may return. A derivation dependency that completes late
must not invoke facade activation.

The same serialization applies after event delivery. While unlock eligibility
is closed, the host must not accept a replacement request merely because the
runtime facade currently reports locked or because a grace timer has not yet
expired. Runtime status and active-process status are not unlock-eligibility
signals while lifecycle status still reports a pending grace lock.

## 28. Save Task Coordination

The host permits one active private mutation `apply` through the runtime facade
and tracks only non-sensitive command state. Lock and fatal containment take
precedence over pending presentation updates. Mutation contents and record
types must not enter task labels, logs, errors, analytics, or public status.

## 29. Fatal Save Lock Behavior

An integrity-unknown or unclassified save failure triggers runtime fail-closed
containment. `AtlasVaultRuntimeFacadeError.committedStateUnavailable` is also a
fatal presentation-containment result: the encrypted write committed, but the
activation controller could not reload the committed store, so the runtime
locks even though the error carries an `AtlasVaultSaveOutcome`.

For all of these cases, the host closes private-presentation authorization,
invalidates the generation, invokes and awaits the private-free
presentation-owner reset, rejects late private updates, awaits runtime teardown,
and requires explicit reactivation. It must not offer an unlocked retry, invent
rollback, or interpret the outcome embedded in `committedStateUnavailable` as a
warning-only result.

## 30. Recoverable Save Behavior

Only the runtime's typed, proven pre-commit failure may preserve the current
unlocked projection. The host may present a fixed non-sensitive failure and an
explicit retry command for the same active generation. It must not infer
recoverability from arbitrary errors.

## 31. Durability-Unconfirmed Warning Behavior

A successfully returned `committedDurabilityUnconfirmed` outcome is not a
rollback request. After the runtime has reloaded the committed store and
provided refreshed current-generation state, the host installs that projection
and presents the fixed durability warning. It must not retry automatically or
restore the pre-save projection.

The same commit-state value embedded in
`AtlasVaultRuntimeFacadeError.committedStateUnavailable` is not this warning
path. That error means refreshed committed state is unavailable and the runtime
has locked; Section 29's fatal presentation containment applies regardless of
the embedded outcome.

## 32. Multiple-Window Policy

The first integration uses one process-wide runtime, lock state, observable
adapter, and active vault. Windows may maintain public navigation state and
subscribe to the same presentation authority. Any window may request lock, and
all windows must remove private presentation when the process generation or
status changes.

The preferred first host has one process-wide `@MainActor` presentation owner
that every window renders. If a later design permits window-local owners, the
host must register them and await a private-free acknowledgement from every
current owner before lifecycle handling or stop reports completion.

## 33. Single Active Vault Policy

Only one vault may be active in the process. A request for a different vault
requires lock and teardown of the current session before explicit activation
of the next. Per-window vault sessions, parallel private stores, and implicit
vault switching are out of scope.

## 34. No Private State In Scene/App Storage

Scene restoration and process restoration must not contain private
presentation, private IDs, saved membership, notes, snippets, drafts,
activation inputs, vault IDs tied to user meaning, or prior private navigation.
After restart, the vault presentation begins locked and private-free.

## 35. No UserDefaults Private State

UserDefaults may not store private values, private counts, unlock choices,
secrets, private navigation, vault keys, or decrypted state. Existing
public-only preferences must remain independent and must not become a side
channel for saved membership.

## 36. No App-Entry Implementation

This phase does not modify `AtlasIOSHostApp`, scene declarations, launch
delegates, environment injection, or production dependency creation. App-entry
ownership remains a later reviewed implementation gate.

## 37. No SwiftUI Implementation In This Phase

No view, property wrapper, `ObservableObject`, environment key, scene object,
or SwiftUI subscription is added. The dedicated locked shell remains a design
constraint for Phase 2D-47.

## 38. Test-Host Design

Phase 2D-46 should build a runtime-neutral test host from injected fakes and
temporary roots. It should model process start, stop, public search, activation,
mutation `apply`, lock, lifecycle delivery, and presentation observation
without importing SwiftUI or changing a production app entry point.

The test host should preserve the same construction/start split and own task
identities so late public and private results can be rejected deterministically.

It requires two complementary configurations:

1. A scripted configuration with fake runtime and unlock coordinators for
   focused host ordering, cancellation, and failure-state tests.
2. An integration configuration that composes the real
   `AtlasVaultRuntimeFacade`, activation controller, lifecycle coordinator,
   stateless presentation adapter, observable presentation adapter, record
   crypto/save/hydration path, and persistence path over fake lower-level
   dependencies and an isolated temporary root.

The integration configuration uses fake root, key-store, clock, sleeper,
secret-derivation, and public-search seams. It must exercise commands through
the host contract rather than invoking the real facade directly from tests.
Neither configuration may set `ATLAS_REFERENCE_CAPTURE` or instantiate
`AtlasReferenceCaptureView`.

## 39. Fake Public-Search Service

The fake public-search service implements only the narrow public protocol. It
records non-sensitive operation names and supports success, delay,
cancellation, and fixed failure injection. It must have no method for saved
searches, tracker records, private notes, or vault operations.

## 40. Fake Runtime Facade

The fake runtime facade should implement the runtime protocol with scripted
locked, activating, unlocked, saving, locking, and failed transitions. It may
use fake private sentinels solely inside in-memory test snapshots and must use
fixed redacted descriptions. This is the focused unit configuration only; it
does not replace the required real-facade integration configuration.

The scripted host also injects a fake unlock coordinator. Secret-lifetime tests
use the real unlock coordinator with fake derivation and activation
dependencies so host dispatch still proves single-use, cancellation, timeout,
and cleanup behavior.

## 41. Fake Lifecycle Events

Tests inject lifecycle events directly into the coordinator or host. No
NotificationCenter, app delegate, scene delegate, protected-data API, timer,
or platform subscription is required. An injected clock and sleeper make grace
locking deterministic.

## 42. Temp-Root Vault Environment

The required real-facade integration configuration uses an isolated temporary
root, fake Keychain store, fake keys, and fake payloads. It must exercise
encrypted load and save through the test-host contract, runtime facade,
activation controller, and persistence path. Tests remove the root afterward
and must not create committed `.atlasvault` exports, read real data, or resolve
production Application Support.

## 43. Endpoint-Call Recorder

The test host includes an endpoint-call recorder at the public/compatibility
boundary. While locked, starting, searching, foregrounding, cancelling,
failing activation, and locking must record zero saved-search, tracker, or
`refreshSidebarData()` calls. Recorder output contains operation categories
only, never URLs, query text, job keys, or payloads.

## 44. Future Integration Tests

Phase 2D-46 must cover:

- side-effect-free construction and explicit start;
- locked public search with zero private endpoint calls;
- no automatic activation or prior private projection restore;
- unlock request dispatch through the coordinator followed by in-memory private
  presentation;
- single-use unlock, cancellation, timeout, failure cleanup, and rejection of
  direct host-to-facade activation;
- a real unlock coordinator paused during secret derivation, followed by a
  background or protected-data lock event, proving the host cancels the active
  request before lifecycle delivery completes, late derivation never calls
  facade activation, and presentation remains locked and private-free;
- an `afterGracePeriod` background event followed by a new unlock request while
  the grace timer is pending, proving the request is rejected or cancelled,
  secret ownership is cleared, and derivation and facade activation receive
  zero calls;
- a concurrent unlock/background race proving the request is either accepted
  before gate closure and cancelled before event return, or rejected after
  closure, with no third ordering that permits late activation;
- an already-unlocked runtime entering inactivity under a nonzero grace policy,
  proving the `@MainActor` owner and observable adapter become private-free
  before event delivery returns even though the runtime remains unlocked;
- a delayed or suspended presentation-owner consumer, proving the host awaits
  the direct private-free acknowledgement rather than relying on
  `currentSnapshot()` or stream delivery;
- private source updates arriving during lifecycle grace, proving the closed
  presentation gate rejects them and a new subscriber receives no prior private
  projection;
- return to active with cancellable grace, proving any private re-projection
  uses a fresh generation only after lifecycle and protected-data checks pass;
- return to active with non-cancellable grace, proving presentation remains
  private-free through timer expiry and confirmed runtime-lock completion;
- a grace timer whose runtime lock is deliberately suspended after the
  coordinator clears its pending fields, proving the host still rejects a
  replacement unlock, consumes no secret, and performs no derivation until a
  matching lifecycle-completion generation and stable locked facade are
  observed;
- advancing the grace clock past expiry while a post-event fake derivation
  would otherwise complete, proving the runtime and presentation remain locked
  and private-free until a processed active/protected-data-available transition
  explicitly reopens unlock eligibility;
- returning active before a
  `afterGracePeriod(..., cancelOnActive: false)` timer expires, then locking or
  entering fatal containment, proving a replacement unlock remains rejected
  until lifecycle status reports matching completed runtime locking, not merely
  no pending timer, and the stale timer cannot later lock a newly activated
  session;
- lifecycle background, protected-data, and terminate locking;
- recoverable pre-commit save failure preserving the active generation;
- committed durability warning installing refreshed state;
- `committedStateUnavailable` carrying either a committed or
  durability-unconfirmed outcome, proving both paths close presentation,
  acknowledge the owner reset, remain locked, and never show the warning-only
  state;
- fatal or integrity-unknown save failure locking and clearing;
- lock propagation to every subscriber and rejection of late updates;
- stop while unlocked cancelling active unlock, mutation, and public-search
  work, closing presentation authorization, invalidating the presentation
  generation, awaiting direct `@MainActor` private-free acknowledgement,
  awaiting runtime lock, and leaving both the UI owner and `currentSnapshot()`
  locked with no private projection before subscription cancellation or return;
- stop with the UI owner suspended after publishing an unlocked snapshot,
  proving cancellation cannot strand that snapshot and every registered owner
  acknowledges clearing before stop completes;
- restart or resubscribe after stop never replaying the prior private snapshot,
  including when the old source completes or yields late;
- public and private task cancellation independence;
- one active vault and one process runtime;
- real-facade encrypted load and save through the host over fake lower-level
  dependencies and a temporary root;
- scripted-facade host behavior remains a separate focused configuration;
- test-host and locked-shell source guards reject `ATLAS_REFERENCE_CAPTURE` and
  `AtlasReferenceCaptureView`;
- no fixture write reaches the normal public-cache root;
- public snapshot immutability and private-value redaction;
- temporary-root cleanup and no `.atlasvault` artifact.

## 45. Deferred Unlock UI

Passphrase fields, recovery-key input, clipboard policy, keyboard policy,
screen-capture policy, LocalAuthentication, and explicit activation UI remain
deferred. The host design accepts only an already reviewed command boundary.

## 46. Deferred Migration

Legacy plaintext discovery, consent, validation, import, coexistence, rollback,
and deletion are not part of host composition. The locked host must not load
legacy private compatibility state as a migration substitute.

## 47. Deferred Cloud Sync

No account, remote transport, server-side vault storage, device trust,
conflict resolution, or cloud lifecycle is designed here. Plaintext
compatibility endpoints are not cloud sync.

## 48. Recommended Phase 2D-46

Implement the runtime-neutral test host and fake integration suite described
above. Add a small production source protocol only if the merged design proves
it is necessary, UI-neutral, side-effect-free, and limited to host
coordination. Keep SwiftUI, app entry, real platform lifecycle subscription,
unlock UI, migration, and cloud sync deferred.
