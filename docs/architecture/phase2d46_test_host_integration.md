# AtlasVault Phase 2D-46 Test-Host Integration

## 1. Purpose

Phase 2D-46 adds a runtime-neutral test host that exercises the boundaries
designed in Phase 2D-45. The host composes public job search, an AtlasVault
runtime, lifecycle coordination, presentation projection, and test persistence
without creating a production app host or a SwiftUI call site.

## 2. Scope

This phase adds test-target code only. It does not add a production host
protocol because the existing runtime, lifecycle, private-state, and
presentation protocols are sufficient under `@testable import AtlasUI`.

The phase does not add SwiftUI, an app entry point, platform lifecycle
subscriptions, `SearchViewModel` integration, `AtlasLocalCache` private-state
integration, LocalAuthentication, migration, plaintext cleanup, cloud sync,
onboarding, recovery UX, or key rotation.

## 3. Test Host Graph

`AtlasVaultTestHost` composes:

- a fake public-job-search service;
- either a scripted runtime or the real `AtlasVaultRuntimeFacade`;
- the real `AtlasVaultLifecycleCoordinator`;
- the real single-use `AtlasVaultUnlockRequestCoordinator`;
- `AtlasVaultPresentationAdapter`;
- `AtlasVaultObservablePresentationAdapter`;
- an in-memory presentation update source;
- a temporary-root environment;
- a fake `AtlasVaultKeyStore`;
- an endpoint-category recorder.

Construction invokes none of those services. Observation starts only after an
explicit subscription, and host start does not activate a vault.

## 4. Locked Public Boundary

The initial host state is locked and contains no private presentation. Public
job search remains available and records only the public-search category. The
host has no operation that loads saved-search or tracker compatibility state,
and it never invokes the legacy sidebar refresh path.

The endpoint recorder distinguishes public search, saved-search compatibility,
tracker compatibility, and private-sidebar refresh categories without storing
URLs, query text, job keys, or payloads. Tests require zero private categories
while locked.

## 5. Explicit Activation

Private projection begins only after an explicit single-use unlock request
succeeds through `AtlasVaultUnlockRequestCoordinator`. The coordinator alone
dispatches facade activation. The host reads the facade's in-memory private
snapshot, creates a process-local presentation generation, and sends the
projected snapshot through the observable adapter.

The host records separate process-local authorization for a session activated
through its own unlock coordinator. An active lifecycle event may restore a
grace-hidden projection only for that previously authorized session; it cannot
adopt a runtime that another owner unlocked.

Activation failure or cancellation clears the cached test projection and
publishes no private state. Timeout fails before facade activation. Lifecycle
gate closure cancels the active unlock request before lifecycle delivery. If
that cancellation loses to an activation that has already committed, the host
keeps presentation closed and explicitly locks the runtime before continuing
the lifecycle transition. No private state is restored from the public cache.

## 6. Lifecycle And Lock

Explicit lock closes private presentation authorization before awaiting runtime
lock. Backgrounding, protected-data loss, termination, and configured
inactivity do the same before lifecycle delivery.

Immediate lock clears the runtime state and presentation. A grace-period
lifecycle may leave the scripted runtime unlocked temporarily, but the host
keeps presentation private-free and rejects private mutation admission until a
processed active transition confirms that no grace lock remains. When policy
retains a grace timer after foregrounding, each later explicit unlock attempt
re-reads lifecycle status so timer completion can reopen admission only after
the runtime is locked. Grace cancellation can reopen private presentation only
when this host authorized the still-running session before the lifecycle
closure.

## 7. Save Outcomes

The host projects save progress from its current in-memory generation and then
delegates the mutation to the runtime.

- A proven recoverable save failure preserves the prior unlocked private
  projection and reports a fixed save-failed status.
- A committed durability warning refreshes private state and reports the fixed
  warning status.
- Fatal, integrity-unknown, or committed-state-unavailable failure closes
  private presentation and leaves the host locked.
- Reactivation after fatal containment creates a fresh presentation generation.

Private mutations are rejected before the runtime call whenever lifecycle or
presentation authorization is closed. A failed private-state read during
activation or post-save refresh is not converted to an unlocked projection
with missing state: the host closes authorization, clears presentation, locks
the runtime, and reports failure.

## 8. Temporary-Root Real Facade

One integration configuration uses:

- a temporary root supplied by a fixed test root provider;
- a fake in-memory key store;
- real encrypted record saver and hydrator;
- real local-store merger;
- real persistence coordinator and atomic writer.

The fixture writes only an encrypted local-store JSON envelope below that
temporary root. It proves encrypted load, encrypted save, lock, and rehydration
without using Application Support or the real Keychain. Store JSON must not
contain fake private name or query sentinels or plaintext record-type strings,
and no `.atlasvault` export is created.

## 9. Public Snapshot Invariant

The test host has no public-snapshot mutation dependency. Integration tests
encode a fake `AtlasPublicLocalSnapshot` before and after activation, save,
failure, lifecycle, and lock operations and require byte equality.

## 10. Failure Injection

Scripted tests cover non-sensitive activation failures for key unavailability,
wrong-key/authentication failure, corrupt store, unsupported version, and path
unavailability. Save tests cover recoverable atomic-write or stale-revision
failure, committed durability warning, cancellation, fatal containment,
background lock, protected-data lock, and delayed completion.

Every failure preserves the independent public snapshot. Locked or failed
activation paths expose no partial or stale private presentation. Recoverable
save failures may retain only the last successfully installed unlocked
projection.

## 11. Pre-Teardown Transition Limitation

The current real runtime facade exposes status polling and terminal operation
results, but not the Phase 2D-45 sequenced pre-teardown containment transition
with its observation-ready handshake and opaque admission token.

Accordingly, this phase proves scripted host gate behavior and real-facade
terminal containment, but it does not claim that a future production host can
acknowledge private presentation clearing before the real facade's first
teardown suspension. Production private-mutation host integration remains
blocked on that runtime-neutral transition seam and dedicated race tests.

## 12. Verification

Tests cover:

- side-effect-free construction and explicit locked start;
- locked public search with zero private compatibility categories;
- explicit private activation and observable projection;
- explicit, background, and protected-data lock clearing;
- recoverable, durability-warning, and fatal save outcomes;
- cancellation and late-result rejection;
- lifecycle cancellation that loses to committed activation locks the runtime
  and still permits a later fresh explicit unlock;
- stale unlock failure rejection after a replacement session activates;
- stale save completion rejection after a replacement presentation generation;
- stale private-state reads cannot publish over a completed lock transition;
- private-state read failure during activation or save refresh locks and clears
  presentation rather than publishing an incomplete unlocked snapshot;
- lifecycle-grace presentation closure and mutation rejection;
- an active event cannot authorize a runtime session unlocked outside the host;
- fresh explicit unlock after a retained foreground grace timer completes;
- suspended mutation admission and stale active-transition race rejection;
- reactivation after fatal containment;
- real encrypted load, save, and rehydration under a temporary root;
- encrypted JSON excludes both fake private name and query sentinels;
- state-aware observable waits distinguish same-status private-state updates;
- public snapshot immutability;
- fake key-store usage and no `.atlasvault` artifact;
- source guards against UI frameworks, app entry, view-model/cache coupling,
  platform authentication, networking, Application Support, migration, and
  cloud behavior.

## 13. Deferred

Deferred work includes:

- the production pre-teardown transition source;
- a production app-host coordinator;
- platform lifecycle subscriptions;
- multi-window presentation-owner acknowledgement;
- SwiftUI and app-entry integration;
- unlock UI and LocalAuthentication;
- migration and plaintext cleanup;
- cloud sync, onboarding, recovery UX, and key rotation;
- production-readiness and threat-model review.

## 14. Recommended Phase 2D-47

Phase 2D-47 may add only the dedicated public-only locked SwiftUI shell. It
must not reuse `AtlasRootView` unchanged, instantiate legacy private panels, or
invoke private compatibility refresh. The test-host endpoint and public/private
invariants remain mandatory integration gates.
