# AtlasVault Phase 2D-53 Production App-Host Composition Design

## 1. Purpose

Phase 2D-53 defines the production app-host composition needed for the first
real, explicit local-key AtlasVault journey. It identifies which reviewed
pieces already exist, which production dependencies are absent, and which
contracts must be implemented and tested before changing an app entry point.

The intended first journey starts in a dedicated public-only locked shell,
keeps public job search available, performs no automatic unlock, permits one
explicit local-key request, and ends at the existing non-sensitive unlocked
transition. Private SwiftUI rendering is not part of that journey.

## 2. Design-Only Scope

This phase changes one architecture document only. It adds no Swift source,
test source, app-entry or navigation change, runtime operation, cache or API
behavior, production host, lifecycle subscription, private rendering,
migration, plaintext cleanup, or cloud behavior.

Every proposed type name in this document is illustrative. An absent
dependency is classified as design required, implementation required, or
blocked rather than described as implemented.

## 3. Reconstructed Baseline

The baseline was reconstructed from Git and GitHub before this document was
written:

- audited `origin/master`:
  `fd770bf1f80448bf022ac38bc19245532038722d`;
- merged Phase 2D-52 PR:
  [#68](https://github.com/yutsukioka/un_job_application_helper/pull/68);
- final Phase 2D-52 head:
  `1fd4fdf6a73eed4f101cfa854848e55a24b847fc`;
- Phase 2D-52 merge:
  `fd770bf1f80448bf022ac38bc19245532038722d`;
- Phase 2D-52 changed exactly its state, view, tests, and architecture
  document;
- Phase 2D-52 had two review threads and zero unresolved threads on the merged
  head;
- Python and GitGuardian checks passed.

The merged focused baselines were rerun locally:

- `AtlasLockedShellUnlockFlowTests`: 22 tests, zero failures;
- `AtlasExplicitUnlockViewTests`: 40 tests, zero failures;
- `AtlasVaultTestHostIntegrationTests`: 39 tests, zero failures.

No late actionable Phase 2D-52 defect was found.

## 4. Existing Entry-Point Inventory

The audited Apple entry points and host artifacts are:

| Artifact | Actual role |
| --- | --- |
| `AtlasIOSHostApp` in `AtlasIOSHostApp.swift` | The only production Xcode app target. It is an iOS `@main` app with a `WindowGroup`. |
| `ATLAS_REFERENCE_CAPTURE` branch in `AtlasIOSHostApp` | A reference-capture route that constructs `AtlasReferenceCaptureView` instead of the normal root. |
| `AtlasPreviewApp` in `PreviewHost` | A separate macOS preview executable that renders `AtlasRootView`; it is not the production app target. |
| `AtlasIconExport` in `PreviewHost` | A development icon-export tool, not a product host. |
| `AtlasUI` Swift package | A library with iOS 18 and macOS 15 support plus a test target. Platform support does not create a production macOS app. |
| `AtlasIOSHost.xcodeproj` | Contains one native app target named `AtlasIOSHost`, uses `iphoneos`, and targets iPhone and iPad. |

`Info.plist` enables multiple iOS scenes. No production macOS app target and
no production AtlasVault host type were found.

## 5. Current Normal Route

The normal `AtlasIOSHostApp` route currently renders `AtlasRootView()`.
`AtlasRootView` constructs the legacy `AtlasSearchViewModel`, renders saved
and tracker-related panels, and can reach `refreshSidebarData()`.

Production AtlasVault wiring has not replaced this legacy root. The dedicated
locked shell, explicit unlock panel, and Phase 2D-52 flow are merged but remain
unwired.

## 6. Reference-Capture Isolation

`AtlasIOSHostApp` checks `ATLAS_REFERENCE_CAPTURE` and, for a recognized mode,
renders `AtlasReferenceCaptureView`. That view is not side-effect-free: its
initializer writes a reference public snapshot, seeds detail-cache fixtures,
and constructs the legacy search view model.

Future app-entry wiring must select the app mode before constructing any
production AtlasVault host, host factory result, observation task, vault-ID
source, or service graph:

1. Parse the reference-capture mode.
2. For reference capture, construct only the existing capture path.
3. For the normal route, construct the production host owner.

Reference-capture mode must instantiate, start, and activate no production
AtlasVault host. A lazily unused host property is still prohibited if creating
that property constructs the graph. Reference fixtures must never become
inputs to the production host.

## 7. Existing Service Inventory

The audited AtlasVault services have these boundaries:

| Component | Current boundary |
| --- | --- |
| `AtlasVaultRuntimeFactory` | Constructs inactive Foundation, Keychain, persistence, crypto, merger, saver, and hydrator dependencies without invoking them. |
| `AtlasVaultPerVaultServiceFactory` | Creates a fresh vault-bound locator and persistence coordinator only after receiving an explicit root URL and validated vault ID. |
| `AtlasVaultRuntimeFacade` | Actor that serializes activation, save, lock, cancellation, and typed terminal status. |
| `AtlasVaultActivationController` | Actor that owns the active session, key owner, activation generation, and private-state store. |
| `AtlasVaultPrivateStateStore` | Internal actor that stages, commits, snapshots, and clears hydrated private state by generation. |
| Saver, hydrator, merger, persistence, atomic writer | Perform encrypted record conversion, hydration, envelope merge, filesystem load, and atomic save only when explicitly invoked. |
| `AtlasVaultLifecycleCoordinator` | Runtime-neutral actor that handles injected lifecycle events; it subscribes to no platform source. |
| `AtlasVaultPresentationAdapter` | Stateless projection from runtime/private state to a sanitized snapshot. |
| `AtlasVaultObservablePresentationAdapter` | Actor that distributes sequenced snapshots, rejects stale sequences, strips private state from non-private statuses, and fails closed when its source finishes. |
| `AtlasVaultUnlockRequestCoordinator` | Actor that enforces single-use request dispatch, timeout, cancellation, secret cleanup, and activation delegation. |
| `AtlasVaultUnlockPresentationController` | Actor that owns one controller-local attempt and exposes sanitized capability, selection, progress, terminal, and reconciliation state. |
| `AtlasApplicationSupportVaultRootProvider` | Resolves the Application Support URL only when `rootDirectory()` is called. |
| `AtlasKeychainVaultKeyStore` | Calls its `AtlasKeychainClient` only on explicit load, save, or delete. |
| `AtlasLockedPublicShellView` | Receives a public-only model and injected public-search/unlock-request actions. |
| `AtlasExplicitUnlockView` | Owns ephemeral local input and delegates through injected presentation actions. |
| `AtlasLockedShellUnlockFlowView` | Thin switch between the locked shell, unlock panel, and fixed unlocked transition. |

`AtlasVaultRuntimeFacade.production()` can construct the runtime graph, but no
production host, production presentation-update source, `@MainActor`
presentation owner, process-global admission authority, production vault-ID
source, or platform lifecycle adapter exists.

Construction is safe for the runtime factory, root-provider value, Keychain
adapter value, facade, lifecycle coordinator, stateless adapter, unlock
coordinator, and unlock controller when their required values are already
available. Calls that perform side effects include Keychain load, root
resolution, store load/save, cache restore/write, network search, and
observation task startup.

## 8. Proposed Production Host Boundary

Future names may include:

- `AtlasVaultProductionHosting`;
- `AtlasVaultProductionHost`;
- `AtlasVaultProductionHostFactory`;
- `AtlasVaultProductionHostDependencies`;
- `AtlasVaultProductionPresentationOwning`;
- `AtlasVaultIDSelecting`;
- `AtlasPublicJobSearching`;
- `AtlasPublicSnapshotRestoring`.

The production host is the sole process authority for the AtlasVault user
journey. Its narrow contract should expose:

- explicit `start()` and `stop()`;
- public-search commands and public shell state;
- explicit unlock-panel presentation;
- unlock method selection, submission, cancellation, and disappearance;
- explicit process-wide lock;
- platform-neutral lifecycle-event delivery;
- sanitized presentation observation;
- reconciliation status.

It must not expose a runtime facade, Keychain client, secret buffer, vault key,
filesystem URL, encrypted envelope, hydrated private state, or compatibility
client to views.

The initial implementation should accept all missing dependencies through
protocols. It must not offer a concrete `.production()` factory until the
public-search, public-snapshot, vault-ID, lifecycle, and presentation-owner
dependencies have reviewed production implementations.

## 9. Host Responsibilities

The production host owns:

- side-effect-free dependency assembly;
- an idempotent explicit start and stop lifetime;
- one process runtime and at most one active vault;
- public-search task identities and cancellation;
- one process-global unlock-admission token;
- one shared unlock presentation controller for the selected vault;
- unlock request lifetime through terminal reconciliation;
- lifecycle-event serialization and admission closure;
- one host-owned presentation source and observable adapter;
- a monotonic host/presentation generation;
- future `@MainActor` presentation-owner registration and reset
  acknowledgement;
- explicit lock and private-free teardown;
- authoritative runtime reconciliation after cancellation races;
- sanitized public shell and unlock-flow state;
- rejection of stale public, unlock, lifecycle, save, and presentation
  completions.

The host must preserve public job-search state independently from vault lock
state. It must close private authorization before any suspension that could
allow a late private result to become visible.

## 10. Host Non-Responsibilities

The host must not:

- implement cryptography or key derivation;
- call `SecItem` or a Keychain client directly;
- parse passphrases or recovery keys;
- own SwiftUI secret input;
- render SwiftUI;
- expose or persist decrypted private state;
- expose encrypted records or plaintext record types;
- scrape or aggregate jobs;
- pass the full `AtlasAPIClient` to locked UI;
- call saved-search, tracker, or sidebar compatibility endpoints while locked;
- infer saved membership from public cache data;
- perform migration or plaintext cleanup;
- implement cloud sync, recovery, onboarding, or key rotation.

Views issue host commands through narrow action values. They do not receive
service objects.

## 11. Construction Versus Start

Construction creates values only. It must:

- resolve no Application Support URL;
- read no Keychain item;
- inspect no vault file;
- read or write no public cache;
- start no observation, lifecycle, timeout, or search task;
- call no API;
- select no vault ID;
- restore no private or secret state;
- activate no runtime.

No constructor or `deinit` starts detached work.

`start()` establishes the public-only host lifetime. It installs a locked,
private-free presentation generation and may restore a reviewed public
snapshot through a future narrow adapter. It starts the host-owned
presentation subscription only after the private-free owner is ready.
`start()` does not issue a public network search automatically, select a vault,
read Keychain, resolve the vault root, or activate.

`stop()` is explicit and idempotent. It closes all admission, cancels host
tasks, locks the runtime, completes the private-free reset barrier, finishes
the presentation source, and prevents replay on a later start.

## 12. Process And Window Ownership

The initial policy is:

- one `AtlasVaultProductionHost` per app process;
- one active vault per process;
- one shared sanitized presentation authority;
- no independent per-window activation controller, runtime, key, or private
  store.

A future `@MainActor` app-level owner holds the process host as an injected
dependency, not as global mutable singleton state. The owner is created only
for the normal app mode and is shared with every `WindowGroup` scene.

Windows may own public-only navigation and the unlock panel's ephemeral local
draft. They do not own vault services. Any window can request a process lock;
that lock clears private presentation for all windows. Closing one window
removes that window's subscription and input without implicitly creating or
switching a vault.

## 13. MainActor And Actor Boundaries

The proposed isolation model is:

- a production-host actor serializes process admission, task identity,
  generations, and lifecycle commands;
- `AtlasVaultRuntimeFacade` serializes runtime commands;
- `AtlasVaultLifecycleCoordinator` serializes neutral lifecycle policy;
- `AtlasVaultUnlockRequestCoordinator` and
  `AtlasVaultUnlockPresentationController` retain their actor isolation;
- `AtlasVaultObservablePresentationAdapter` serializes sequenced
  distribution;
- only a future UI-facing presentation owner is `@MainActor`.

The host closes authorization and advances its generation before awaiting the
`@MainActor` reset. It captures the generation/token needed for
acknowledgement, then awaits without assuming actor non-reentrancy. A late
result must revalidate host generation and admission after every suspension.

Runtime lock and owner reset may proceed concurrently after gate closure, but
the host command returns only after both complete. The host must not await a
callback while retaining a separate lock that the `@MainActor` owner needs to
acknowledge, and the owner must never call synchronously back into a
host-critical section.

## 14. Public Job-Search Boundary

No narrow production `AtlasPublicJobSearching` protocol or adapter exists.
The only narrow protocol and `AtlasVaultFakePublicJobSearchService` are in the
test target.

Current production search uses `AtlasSearchViewModel` and the broad
`AtlasAPIClient`. That client exposes public search together with plaintext
saved-search and tracker methods, reads a base URL from `UserDefaults`, and
performs `URLSession` calls. Passing it or the legacy view model to the locked
host would keep private compatibility methods reachable.

Production host implementation is blocked on a narrow adapter whose public
contract can represent only:

- public health/service availability;
- public job search;
- public source and update metadata;
- public detail fetched from a proven public search result.

The adapter must sanitize errors, never log query text or job keys, and make
private compatibility calls unrepresentable. `AtlasLockedPublicShellActions`
must call this host-owned narrow boundary, never `AtlasAPIClient` directly.

## 15. Public Cache Boundary

`AtlasPublicLocalSnapshot` is an existing public model that contains health,
public search results, sources, and recent runs without saved membership.
That model is usable with constraints while locked.

No narrow production public-snapshot store/restore adapter exists.
`AtlasLocalCache` is a static legacy surface that:

- resolves Application Support and performs filesystem I/O;
- stores a public refresh preference in `UserDefaults`;
- manages both the public snapshot and per-job detail files;
- can be reached from the legacy view model during construction and refresh.

A future `AtlasPublicSnapshotRestoring` boundary may restore only
`AtlasPublicLocalSnapshot` during explicit host start. It must not expose
detail-cache APIs, private membership, saved-only keys, or write authority to
the vault branch.

The existing detail cache remains blocked for locked-shell use. Its namespace
can contain files written from saved-only navigation, and no reviewed
provenance marker proves that every key, filename, warmup selection, count,
or diagnostic derives exclusively from public results. Existing unproven
files must be excluded, not retroactively trusted.

## 16. Private Compatibility Endpoint Boundary

`AtlasAPIClient.savedSearches()`, saved-search mutation methods, and tracker
methods are plaintext compatibility surfaces. Depending on configured base
URL and deployment, they may be loopback, LAN, or remotely reachable. They
are not AtlasVault sync and are not safe merely because a current development
deployment is local.

The locked production host never calls, injects, or exposes these methods.
Public search, launch, foregrounding, cache restore, panel presentation,
failed activation, cancellation, timeout, lock, and stop must all record zero
saved-search, tracker, and private-sidebar refresh calls.

Any temporary unlocked compatibility bridge would require a separate
reviewed transition design. It is outside the first local-key journey.

## 17. Vault-ID Source Audit

The runtime, activation request, unlock request, and unlock presentation
controller accept a caller-supplied vault ID. The path locator validates the
ID and uses it as a plaintext path component. Tests supply fixed fake or
random IDs.

No reviewed production vault registry, selector, or persistence source exists.
No current production code selects the AtlasVault ID for the app host, and no
AtlasVault code was found storing it in `UserDefaults`, `@AppStorage`,
`@SceneStorage`, or the public snapshot.

This is a blocking prerequisite. A future narrow boundary should:

- return zero or one selected stable, random, non-semantic ID for the initial
  single-vault policy;
- resolve selection only after an explicit unlock-panel request, not during
  host construction or start;
- distinguish no selected vault with a fixed non-sensitive result;
- generate IDs only during a separate explicit vault-creation flow;
- retain the selected ID in host memory only as long as needed;
- expose no user label, private count, timestamp, record ID, job key, or path;
- avoid ad hoc directory scans or Keychain probing as implicit selection;
- define atomic registry update, corruption, deletion, backup, and file
  protection behavior before implementation.

The ID must not be persisted in `UserDefaults`, scene/app storage, public
snapshot state, logs, analytics, or accessibility output. Although the ID is
non-semantic and already appears in a vault path and Keychain account, a
registry can still leak vault existence or count; that metadata requires
review.

Multiple-vault selection is outside the first journey. Switching requires
explicit lock and teardown before selecting another ID.

If explicit selection returns no ID, the host remains private-free, projects
the public shell's `noVault` state, creates no unlock presentation controller,
opens no panel, and performs no Keychain, root, or store call.

## 18. Local-Key Capability

`AtlasVaultUnlockCapabilities.currentProduction` currently marks local key
available and leaves passphrase and recovery unavailable. Availability means
the method is implemented, not that a Keychain item is known to exist.

The view and host must not preflight Keychain to alter capability visibility.
Only an explicit local-key submit may traverse:

1. `AtlasVaultUnlockPresentationController`;
2. `AtlasVaultUnlockRequestCoordinator`;
3. `AtlasVaultRuntimeFacade`;
4. `AtlasVaultActivationController`;
5. `AtlasVaultKeyStore`;
6. `AtlasKeychainVaultKeyStore`;
7. `SecItemAtlasKeychainClient`.

The request coordinator's local-key branch carries no secret and constructs a
runtime activation request without a supplied raw key. Activation validates
the vault ID, loads the stored key, then resolves the root only if a valid key
was obtained.

A missing item, invalid stored key, or Keychain error becomes a fixed
non-sensitive unlock failure at the current unlock-presentation boundary.
The host must not expose credential presence, raw `OSStatus`, key length,
vault ID, or path. It does not call Keychain directly.

An invalid-length stored key fails before root resolution. A valid-length but
wrong key can reach encrypted-store hydration and fail authentication. Both
remain non-sensitive failures at the unlock presentation boundary; neither
authorizes partial private state or an automatic retry.

## 19. Passphrase And Recovery

Phase 2D-49 added wrapped-key models and a context-aware provider protocol, but
no reviewed production passphrase or recovery provider is present.
`currentProduction` therefore hides both methods.

The first production host journey:

- does not connect provider closures;
- does not implement Argon2id or key unwrap;
- does not advertise passphrase or recovery;
- does not accept a raw supplied key;
- does not expose the module-internal supplied-test-key request.

Passphrase and recovery remain blocked until reviewed providers, authenticated
vault binding/key confirmation, secret-lifetime tests, and production threat
review exist.

## 20. Explicit Local-Key User Journey

The intended journey is:

1. App-entry mode selection checks `ATLAS_REFERENCE_CAPTURE`.
2. Reference capture bypasses the production host entirely.
3. The normal route constructs the process host and dependencies without
   invoking them.
4. The app explicitly calls `start()`.
5. The host installs a private-free locked generation and renders the
   dedicated public shell.
6. Public search is available through the narrow public boundary.
7. The user explicitly requests the unlock panel.
8. The host resolves one reviewed non-semantic vault ID without Keychain,
   vault-root, or store access. If none exists, it remains in the public
   `noVault` state and stops this sequence.
9. The shared unlock controller exposes the production capability snapshot,
   which shows local key only.
10. Opening the panel performs no activation.
11. The user explicitly chooses the local-key action.
12. The unlock presentation controller creates one single-use request.
13. The request coordinator dispatches one local-key runtime activation.
14. Runtime activation loads the Keychain item behind its protocol, resolves
    Application Support, binds the vault path, reads the encrypted store, and
    hydrates the controller-owned in-memory private state.
15. Success reaches the controller's non-sensitive unlocked status.
16. The host verifies authoritative runtime state and publishes only the
    existing fixed unlocked transition.
17. The host does not request or project private state for SwiftUI.

This sequence is not implementation-ready until the blockers in Sections 40
and 41 are resolved.

## 21. No Automatic Unlock

Activation is prohibited from:

- process or host construction;
- `start()`;
- launch or scene creation;
- foregrounding or protected-data availability;
- public search or public-cache restore;
- Keychain item existence;
- unlock-panel appearance;
- local-key capability visibility;
- view appearance or preview construction.

Foreground and protected-data-available events can reopen admission only after
lifecycle quiescence. They never select a vault or submit a request.

## 22. Unlock Admission

`AtlasVaultUnlockPresentationController` enforces one controller-local attempt,
but it explicitly does not claim process-global or multi-window admission.
The production host must add that authority.

The host actor reserves one opaque admission token before any asynchronous
vault-ID lookup, controller selection, or dispatch. Every window uses the same
controller and host admission gate. A second request is rejected or shown a
fixed busy state before it can invoke a dependency.

Because `AtlasVaultUnlockPresentationController` requires a vault ID at
initialization, the host creates that one shared controller lazily only after
explicit vault-ID selection succeeds. It never creates a controller per
window or speculatively at process start.

Admission remains reserved through:

- request dispatch;
- timeout or cancellation request;
- controller invalidation;
- any still-running submit task;
- authoritative runtime reconciliation;
- private-free reset when reconciliation requires lock.

Closing admission invalidates the host generation before cancellation. Late
completion can update neither the current unlock state nor the current
presentation owner unless its token and generation remain current.

Lifecycle inactivity, protected-data loss, stop, explicit lock, or unresolved
host reconciliation closes admission. Multiple windows cannot create
independent controllers or race different vault IDs.

## 23. Host Reconciliation

`hostReconciliationRequired` means a cancellation, timeout, disappearance, or
host-lock notification may have lost to committed activation. It must not be
downgraded to an ordinary locked state based on one status read.

The host must:

1. close unlock and private-presentation admission;
2. retain the active submit task/token until it reaches a terminal result;
3. query authoritative runtime status through the reviewed runtime seam;
4. command `runtime.lock()` whenever status is unlocked, activating, saving,
   locking, failed-but-uncertain, or otherwise not proven stably locked;
5. complete the private-free reset barrier;
6. verify terminal runtime locked state and a private-free observable adapter;
7. notify the unlock presentation controller through `hostDidLock()`;
8. publish ordinary locked state only after all acknowledgements succeed.

If status cannot be read, lock fails to complete, presentation reset cannot be
acknowledged, or another late success appears, reconciliation remains visible
and non-interactive. The host fails closed and does not permit another unlock.

The host owns the task that calls `submit`. It must not immediately publish the
controller's cancelled state while that task can still commit activation.
Only terminal failure permits the Phase 2D-52 cancelled-to-locked projection;
terminal success takes the reconciliation path.

## 24. Lifecycle Integration

Only the runtime-neutral `AtlasVaultLifecycleCoordinator` exists. It accepts
explicit events, applies immediate or grace locking, and deduplicates repeated
equivalent events. No iOS `scenePhase`, UIKit notification,
protected-data notification, AppKit event, or process/scene aggregation
adapter is wired.

A future iOS adapter must:

- aggregate all `WindowGroup` scenes before deriving process-active,
  inactive, and background transitions;
- avoid locking the process because one scene resigns while another remains
  active;
- deliver protected-data unavailable/available independently from scene
  visibility;
- serialize and deduplicate transitions through the process host;
- close unlock admission before a lock-producing event is delivered;
- never unlock on foreground or protected-data availability;
- treat termination notification as best effort and rely on prior private-free
  background handling.

A future macOS adapter requires a separate product target and AppKit lifecycle
design. macOS has no reviewed equivalent in this repository for the iOS
protected-data path.

App-entry integration is blocked until platform adapters, multi-scene
aggregation, event ordering, and teardown tests exist.

## 25. Private-Free Reset Barrier

Explicit lock, host stop, lifecycle lock, protected-data loss, termination,
and fatal containment use one barrier:

1. Close private-presentation and unlock authorization.
2. Advance the host generation and invalidate pending private updates.
3. Cancel pending unlock and private work; cancel public work only when host
   lifetime ends or public-task policy requires it.
4. Command and await runtime lock.
5. Command the future `@MainActor` presentation owner to install a locked,
   private-free snapshot for the invalidated generation.
6. Emit a monotonic private-free control update or finish the host-owned
   presentation source private-free.
7. Verify the observable adapter's current snapshot has no private state and
   await every registered UI-owner acknowledgement.
8. Return only after the runtime, source, adapter, and owners satisfy the
   barrier.

Authorization closes before runtime and `@MainActor` suspension. Late buffered
updates from the old generation are rejected. A missing or stalled owner
acknowledgement does not reopen private presentation; the host remains in a
non-sensitive locking/reconciliation condition.

Normal explicit vault lock preserves the independent public shell model and
may preserve public-search tasks. `stop()` cancels all host-owned public tasks
and rotates to a fresh presentation pipeline.

## 26. Public Search During Lock And Unlock

Public search is a sibling host branch, not a vault command. The initial policy
is:

- opening the unlock panel does not cancel an in-flight public search;
- public completion may update only the current public request generation;
- an explicit vault lock does not clear public results;
- unlock success does not annotate public results with saved membership;
- host stop or superseding public search cancels the prior public task;
- a late public result cannot change vault, unlock, or private presentation;
- lifecycle policy may cancel public work for resource reasons, but that
  decision is independent from private lock.

The Phase 2D-52 flow retains the public shell model while the panel or unlocked
transition is shown. Returning to locked state can therefore restore public
results without consulting private state.

Search query text remains in-memory UI state only. It is not logged, placed in
scene restoration, or persisted as an implicit saved search.

## 27. Cancellation And Timeout

Panel cancel delegates to the shared unlock controller and closes the host's
admission token. The host keeps the submission task and generation until
dispatch terminates.

Controller timeout and request timeout both revoke presentation authorization.
Neither permits immediate replacement while the invalidated operation can
still finish.

Backgrounding, protected-data loss, and stop close admission before invoking
coordinator cancellation or lifecycle lock. Host stop also cancels public
tasks. A user cancellation does not clear public search state.

If cancellation wins, the controller may publish cancelled and the host
returns to a private-free locked shell. If activation has committed or its
outcome is uncertain, the host publishes reconciliation, locks the runtime,
and completes the private-free barrier. No automatic retry occurs.

## 28. Save-Failure Containment

The runtime already distinguishes:

- a recoverable pre-commit save failure that may preserve the authoritative
  unlocked session;
- a returned `committedDurabilityUnconfirmed` outcome that must not be rolled
  back;
- committed-state-unavailable, integrity-unknown, and unclassified failures
  that fail closed and lock.

The first production local-key journey admits no private mutation and adds no
save UI. It must not call `apply`.

Before a later production host admits private mutations, an additional runtime
transition seam is required. The current facade exposes terminal status and
results but no sequenced pre-teardown containment transition with an
observation-ready handshake and opaque operation token. Without that seam, a
host cannot guarantee `@MainActor` private clearing before the facade's first
teardown suspension.

Production mutation admission is therefore blocked until that seam and race
tests exist. Public presentation of all save outcomes remains fixed and
non-sensitive.

## 29. Presentation Ownership

The production process must own:

- one host-controlled `AtlasVaultPresentationUpdateSourcing` implementation;
- one `AtlasVaultObservablePresentationAdapter`;
- one monotonic presentation generation;
- one future `@MainActor` presentation owner shared by all windows.

Neither the production update source nor the `@MainActor` owner exists today.
Both require implementation and tests.

Views receive immutable shell/flow state and injected actions. They never
observe the runtime facade, lifecycle coordinator, private-state store, or
Keychain directly.

For the first journey, runtime activation may hydrate controller-owned memory,
but the host does not call `privateState()` and does not pass hydrated state to
`AtlasVaultPresentationAdapter`. The UI receives only locked, progress,
failure, reconciliation, and fixed unlocked-transition state.

No private or secret value enters app restoration, scene restoration, previews,
or a newly subscribing window. Source completion and host stop finish
private-free.

## 30. Locked Shell Ownership

The production host supplies:

- `AtlasLockedPublicShellModel`;
- `AtlasLockedPublicShellActions`;
- the exact host-authorized `AtlasVaultUnlockPresentationState`;
- `AtlasLockedShellUnlockFlowState`;
- `AtlasExplicitUnlockViewActions`.

The public shell receives no runtime facade, `AtlasAPIClient`, Keychain
adapter, root provider, vault ID, compatibility client, secret, or private
state. Its unlock action only requests panel presentation. Opening the panel
does not select a method or activate.

The host keeps the public shell model while `AtlasLockedShellUnlockFlowView`
switches to the unlock panel or unlocked transition.

## 31. Unlocked Transition Boundary

The merged Phase 2D-52 flow maps unlock success to a fixed
`unlockedTransition`. It carries no saved searches, saved jobs, notes,
snippets, drafts, private counts, record IDs, keys, paths, or envelopes.

Production private rendering is not ready. The first host-wiring
implementation must stop at this transition and must not:

- query runtime private state for SwiftUI;
- render a legacy private panel;
- navigate automatically to `AtlasRootView`;
- call a compatibility endpoint;
- enable private mutation.

A later reviewed phase must design private presentation ownership, navigation,
mutation admission, and lock clearing before private content appears.

## 32. Legacy Root Replacement Plan

`AtlasRootView` must not be reused as the locked root. Its legacy hierarchy
owns `AtlasSearchViewModel`, renders saved/tracker panels, and invokes
`refreshSidebarData()`, which fetches and publishes plaintext saved-search and
tracker state.

The staged replacement is:

1. Implement and test the narrow production public-search and snapshot
   boundaries.
2. Implement and test the runtime-neutral production host and presentation
   owner without app-entry wiring.
3. Add platform lifecycle and multi-scene tests.
4. Exercise the dedicated `AtlasLockedShellUnlockFlowView` in an isolated
   production-like host harness.
5. Replace only the normal `AtlasIOSHostApp` route after every prior gate
   passes.

The new route must contain no `AtlasRootView`, `SavedPanel`,
`AtlasSearchViewModel`, or `refreshSidebarData()` reference. Public search is
preserved through the new narrow boundary. Reference capture may retain its
legacy fixture route because it bypasses the production host.

## 33. App-Entry Integration Plan

Future app-entry integration is design only in this phase:

1. Resolve an app-mode enum before constructing host dependencies.
2. Keep the existing reference-capture branch isolated.
3. In normal mode only, create one app-level `@MainActor` production-host
   owner.
4. Inject that owner and shared actions into every `WindowGroup` scene.
5. Call explicit host `start()` for the normal app lifetime.
6. Render the dedicated Phase 2D-52 flow as the root.
7. Deliver aggregated platform lifecycle events through the host.
8. Complete stop/private-free teardown on process lifetime end as far as the
   platform permits.

The normal route starts locked. It performs no automatic unlock, private
compatibility refresh, private restoration, or private navigation.

## 34. iOS Host Plan

The current production target is iOS and uses `WindowGroup`. Its plist permits
multiple scenes, so app-host ownership cannot be created inside each scene's
content closure.

The future iOS plan requires:

- one app-level `@MainActor` owner created only in normal mode;
- one shared process host/runtime;
- scene registration with process-level active/background aggregation;
- protected-data unavailable/available delivery;
- one shared unlock admission authority;
- per-window sanitized subscriptions and public-only navigation state;
- immediate private-free reset on protected-data loss;
- no unlock on foreground or protected-data return;
- explicit startup after mode selection;
- idempotent stop and best-effort termination handling.

Closing one window cancels that window's input and subscription. It does not
silently switch or unlock a vault. Any explicit lock affects the process.

## 35. macOS Host Plan

There is no production macOS app target. The Swift package supports macOS, and
`PreviewHost` provides development preview/icon executables, but neither is a
production host.

macOS requires a separate future product decision, target, entitlements,
Keychain policy, lifecycle adapter, window aggregation policy, app entry,
tests, and distribution review. iOS entry wiring does not complete macOS.

The runtime-neutral host, public-search, vault-ID, admission, and presentation
contracts should remain reusable. Platform lifecycle and app-entry adapters
must be separate.

## 36. Multiple-Window Policy

The initial multi-window contract is:

- one process host and one active vault;
- one shared unlock controller and one admission token;
- one shared sanitized vault presentation authority;
- no per-window secret buffer outside the panel's local ephemeral input;
- no competing vault-ID selection;
- any window's explicit lock locks all windows;
- all windows acknowledge the same private-free generation;
- stale updates from a closed window are rejected.

Public-only query/navigation state may be window-local, but public-search
requests cross the process host with non-sensitive request identities. The
host scopes late results to the originating current public generation.

Only one window may own an active unlock panel authorization at a time. Other
windows display the shared activating/reconciliation status and cannot submit.
Window closure invalidates its panel authorization; it does not by itself lock
the process unless the aggregated lifecycle policy requires lock.

## 37. Diagnostics And Redaction

Production host diagnostics may contain only fixed operation categories,
coarse state classes, opaque process-local tokens, and reviewed timing data.
They must not contain:

- vault IDs;
- paths or filenames;
- keys, key lengths, nonces, ciphertext, or secret lengths;
- Keychain account/service values or raw `OSStatus`;
- credential availability analytics;
- endpoint URLs, bodies, query text, or job keys;
- saved-search names/filters, saved membership, notes, snippets, drafts, or
  generated-document references;
- private counts or record types.

Host error mapping must use fixed non-sensitive categories and must not forward
underlying `localizedDescription`, API response bodies, or dependency
descriptions. Diagnostics that need to correlate an internal operation must use
an opaque process-local operation token or a fixed category; a vault ID is
never an acceptable correlation value.

`AtlasVaultUnlockedSession.description` currently includes a vault ID and key
byte count. Production host code must never log or reflect this lower-level
description. A diagnostic hardening audit that fully redacts such lower-level
descriptions is required before production launch.

Tests use fake sentinels and assert absence from descriptions, errors, logs,
accessibility output, and captured diagnostics.

## 38. Testability And Injection

Future production-host tests require injected seams for:

- narrow public search and fixed public failures;
- an endpoint-category recorder;
- public snapshot restore and replacement counts;
- vault-ID selection/registry behavior;
- `AtlasVaultKeyStore` and Keychain call counts;
- root-provider and filesystem call counts;
- lifecycle events, scene aggregation, clock, and sleep;
- runtime status/transitions and lock suspension;
- unlock coordinator/controller completion races;
- presentation source sequencing;
- `@MainActor` owner reset acknowledgement and suspension;
- app-entry mode selection and reference-capture factory call counts.

The Phase 2D-46 `AtlasVaultTestHost` is behavioral evidence and a reusable test
fixture. It must not be copied wholesale into production. Production
interfaces should remain narrow; tests may adapt the existing host, endpoint
recorder, and public-search fake to those interfaces.

Every side-effect seam needs zero-call construction/start assertions. Every
async result needs a request or generation identity test. Source guards must
prove no legacy root, private endpoint, global singleton, direct Keychain,
app-entry, migration, or cloud coupling.

## 39. Future Production-Host Tests

The minimum future test suite is:

1. Construction invokes zero root, Keychain, filesystem, cache, API,
   lifecycle, observation, and activation calls.
2. Reference-capture mode constructs no production host or service graph.
3. Normal host start publishes locked, private-free state.
4. Start performs zero Keychain and vault-root calls.
5. Start performs zero private compatibility calls.
6. Start restores only a reviewed public snapshot and issues no automatic
   network search.
7. Explicit public search works while locked.
8. Public-search call count is greater than zero.
9. Saved-search compatibility call count remains zero.
10. Tracker compatibility call count remains zero.
11. Private-sidebar refresh count remains zero.
12. Public results contain no saved membership or saved-only job key.
13. Explicit panel open performs no activation or Keychain call.
14. Production capability projection exposes local key only.
15. Explicit local-key submit traverses controller, coordinator, and runtime.
16. Missing Keychain item fails non-sensitively and performs no root lookup.
17. Invalid-length stored key fails non-sensitively and performs no root
    lookup.
18. A valid-length wrong key reaches authentication failure without partial
    private state or detailed presentation.
19. Missing vault file does not create or overwrite a store.
20. Corrupt store and unsupported version install no partial private state.
21. Panel cancellation releases admission and returns private-free.
22. Timeout releases secret/request ownership and blocks replacement until
    terminal reconciliation.
23. Cancellation losing to activation publishes reconciliation, locks, and
    never publishes stale unlocked state.
24. Background and protected-data loss close admission before lifecycle
    delivery.
25. Foreground and protected-data availability never unlock automatically.
26. Stop is idempotent and cancels host-owned tasks.
27. Stop returns only after runtime, source, adapter, and every owner are
    private-free.
28. Multiple windows share one runtime, vault selection, controller, and
    admission token.
29. A second-window unlock race cannot dispatch twice.
30. Closing a window clears its local input without duplicating or retaining a
    secret.
31. Stale public, unlock, lifecycle, save, and presentation generations cannot
    publish.
32. In-flight public search may complete during unlock but cannot mutate vault
    state.
33. Explicit vault lock preserves reviewed public-only state.
34. Recoverable save failure preserves only authoritative current-generation
    private state in a later mutation-enabled host.
35. Durability-unconfirmed commit is not rolled back.
36. Fatal/integrity-unknown save closes presentation before terminal lock in a
    later mutation-enabled host.
37. No app/scene restoration contains private state, vault ID, or secret.
38. New production route source contains no `AtlasRootView`,
    `refreshSidebarData`, saved/tracker panel, or compatibility endpoint.
39. Reference-capture fixtures and cache side effects remain isolated from the
    production host.
40. Diagnostics contain no fake vault ID, key length, path, query, job key,
    private sentinel, or credential-availability signal, including for
    internal-operation correlation; correlation tests accept only opaque
    process-local operation tokens or fixed categories.
41. App-entry replacement is rejected unless all matrix blockers have
    completed tests and review evidence.

## 40. Go/No-Go Matrix

| Capability | Classification | Evidence or constraint |
| --- | --- | --- |
| Runtime composition | ready | Production dependency values construct without invoking root, Keychain, filesystem, or crypto operations. |
| Runtime facade | ready with constraints | Activation, lock, save, and terminal containment exist; pre-teardown transition observation is absent. |
| Activation controller/private store | ready with constraints | Explicit local-key activation and fail-closed hydration exist; production host ownership is absent. |
| Lifecycle coordinator | ready with constraints | Neutral policy and tests exist; no platform adapter or multi-scene aggregation exists. |
| Observable presentation adapter | ready with constraints | Sequenced sanitization exists; production source and `@MainActor` owner are absent. |
| Dedicated locked shell | ready with constraints | Public-only model/view and injected actions exist; production search/cache adapters are absent. |
| Explicit unlock panel | ready with constraints | Capability-driven local-key UI exists and is unwired. |
| Thin locked-shell unlock flow | ready with constraints | Locked, panel, and fixed unlocked modes exist and remain unwired. |
| Local-key runtime path | ready with constraints | Keychain-backed activation exists behind protocols; vault-ID source and host composition are absent. |
| Runtime-neutral production-host contract | implementation required | No production host protocol/type exists. |
| Production presentation update source | implementation required | Only test-target source ownership exists. |
| MainActor presentation owner/reset | implementation required | Design requirements exist; no production owner or acknowledgement seam exists. |
| Process-global unlock admission | implementation required | Controller-local admission exists; process/window authority does not. |
| Production public-search adapter | implementation required | Only the broad client and test-only narrow fake exist. |
| Public snapshot model | ready with constraints | Public data model exists; narrow restore/storage adapter is absent. |
| Public cache restore adapter | implementation required | Legacy static cache is not the reviewed host boundary. |
| Public detail cache while locked | blocked | Existing files and warmup lack exclusive public provenance/namespace proof. |
| Production vault-ID source | design required | Runtime accepts caller IDs; no reviewed registry or selector exists. |
| Platform lifecycle adapter | design required | Neutral events exist; iOS/macOS adapters and multi-scene aggregation do not. |
| Host reconciliation | implementation required | Controller reports the state; process authority and reset barrier are absent. |
| Production host implementation | blocked | Public search, vault ID, presentation owner/source, admission, and lifecycle prerequisites are incomplete. |
| iOS app-entry replacement | blocked | Production host and platform gates have not passed. |
| macOS production host | blocked | No production macOS target or lifecycle/app-entry design exists. |
| Passphrase unlock | blocked | No reviewed production provider or authenticated binding exists. |
| Recovery-key unlock | blocked | No reviewed production provider or authenticated binding exists. |
| Private SwiftUI rendering | design required | Current flow intentionally stops at a non-sensitive transition. |
| Production private mutation admission | blocked | Pre-teardown transition/token handshake and private owner are absent. |
| Migration/plaintext cleanup | blocked | Explicitly deferred and requires separate policy and user-confirmed flow. |
| Cloud sync | blocked | No cloud protocol, identity, onboarding, or threat review is in scope. |
| Production launch | blocked | Host, lifecycle, identity, cache, diagnostics, platform, and threat gates remain. |

## 41. Blocking Prerequisites

The following prerequisites must not be suppressed:

1. Define and implement a narrow production public-search protocol/adapter
   that cannot call private compatibility endpoints.
2. Define a narrow public-snapshot restore/store adapter and keep unproven
   detail-cache files excluded.
3. Design and review a stable non-semantic vault-ID selector/registry,
   including storage metadata, corruption, file protection, backup, creation,
   and deletion policy.
4. Implement the runtime-neutral production-host contract with side-effect
   injection, explicit start/stop, one process admission token, and no app
   entry.
5. Implement a production presentation-update source and `@MainActor` owner
   with an awaitable private-free reset.
6. Implement authoritative host reconciliation around the shared submit task,
   runtime lock, and owner acknowledgement.
7. Design and test iOS scene aggregation and protected-data delivery. Keep
   macOS separate until a production target is approved.
8. Add production-like tests for the explicit local-key journey, missing key,
   missing/corrupt store, cancellation, timeout, stale completion, and
   multi-window races.
9. Preserve complete reference-capture isolation and prove zero production
   host construction in that route.
10. Complete diagnostics hardening so no lower-level vault ID, key-length,
    path, raw API body, or raw Keychain error can reach production telemetry.
11. Decide Application Support file protection, backup exclusion,
    entitlements, and production threat-model requirements before launch.
12. Before enabling private mutations, add the facade's sequenced
    pre-teardown transition, opaque operation token, observation-ready
    handshake, and containment race tests.
13. Before private rendering, design navigation, owner generation, mutation,
    and all-window clearing separately.

Phase 2D-54 can coherently implement only a runtime-neutral injected host
contract if the missing public-search, public-snapshot, and vault-ID seams are
defined inside that phase. It must not claim a complete concrete production
factory or app-entry readiness.

## 42. Staged Future Plan

The evidence-based sequence is:

- **Phase 2D-54:** implement a runtime-neutral production-host contract/factory
  and tests, still unwired. Include narrow injected protocol contracts for
  public search, public snapshot restore, and explicit non-semantic vault-ID
  selection because those seams are currently absent. The factory must have
  no concrete default production constructor yet and must stop at the fixed
  unlocked transition.
- **Phase 2D-55:** implement and review the concrete public-search/public
  snapshot adapters and the vault-ID registry/selection policy. Keep detail
  cache excluded unless provenance is separately proven.
- **Phase 2D-56:** implement the host-owned production presentation source,
  `@MainActor` reset owner, process-global admission/reconciliation, and
  platform-neutral race tests.
- **Phase 2D-57:** design and test iOS multi-scene/protected-data lifecycle and
  app-entry mode composition without changing the real entry.
- **Later iOS integration:** replace only the normal `AtlasIOSHostApp` route
  after every prior gate passes; preserve reference capture and no automatic
  unlock.
- **Separate later gates:** private rendering and mutation admission,
  passphrase/recovery providers, a macOS product target, migration, plaintext
  cleanup, cloud sync, recovery, onboarding, and key rotation.

No later branch or implementation is created by Phase 2D-53.

## 43. Production-Readiness Statement

Phase 2D-53 is design only. It adds no production host, app-entry wiring,
navigation, platform lifecycle subscription, public-search adapter, vault-ID
registry, private rendering, provider, migration, cleanup, or cloud behavior.

Local key is the only currently supported production unlock method, and it
still lacks the production host, vault-ID source, public-search adapter,
presentation owner, and lifecycle integration needed for a real user journey.
Passphrase and recovery remain unavailable. Raw test-key activation remains
test-only.

Migration, plaintext cleanup, cloud sync, recovery UX, LocalAuthentication,
device onboarding, key rotation, file-protection review, backup policy, and
production threat review remain deferred. No production-readiness claim is
made.
