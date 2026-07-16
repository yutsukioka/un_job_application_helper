# Phase 2D-37 AtlasVault Lifecycle Coordinator

## Purpose

Phase 2D-37 implements the runtime-neutral lifecycle coordinator designed in
Phase 2D-36. It accepts explicitly delivered neutral events and coordinates
only redacted runtime facade controls.

## Scope

This phase adds the coordinator seam and tests. It adds no platform
notification subscription, app-entry or SwiftUI wiring, automatic activation,
public-cache mutation, migration, cloud sync, recovery, onboarding, or key
rotation. It does not claim production readiness.

## Runtime Boundary

`AtlasVaultLifecycleRuntimeControlling` is module-internal. It exposes only
redacted runtime status, idempotent lock, and atomic
`cancelActivationIfInProgress()`. The public `AtlasVaultRuntimeFacading`
application boundary remains unchanged.

The facade asks its activation controller to cancel before changing facade
state. It transitions to locked only when cancellation returns success. If
activation has already completed, the operation returns `false` and leaves the
new unlocked session intact.

External clients construct the public coordinator with a concrete public
`AtlasVaultRuntimeFacade` plus public clock and sleeper abstractions. The
initializer does not invoke any dependency. Test injection of alternate
runtime controls remains module-internal.

The coordinator never receives an activation request, vault ID, vault key,
hydrated state, mutation, record envelope, path, or local-store service.

## Neutral Events

`AtlasVaultLifecycleEvent` contains `didBecomeActive`, `willResignActive`,
`didEnterBackground`, `willTerminate`, `protectedDataBecameUnavailable`, and
`protectedDataBecameAvailable`. Callers deliver these values explicitly; they
are not platform framework types.

## Lock Policy

`immediate` locks on background immediately. `afterGracePeriod` gives an
already unlocked or saving runtime a bounded interval before lock. Its
`cancelOnActive` option allows foregrounding to invalidate the pending timer.
Non-positive grace durations fail closed as immediate lock. Inactive cancels
only an activation still in progress unless the separately injected strict
inactive-lock option is enabled.

Protected-data unavailability and termination always invalidate grace work
and request lock immediately. Protected-data availability and foregrounding
never activate a vault.

## Time And Stale Work

The clock and sleeper share an arbitrary monotonic `Duration` origin. Grace
work is tagged with a coordinator-owned generation. Cancellation, foreground,
security events, and later timers invalidate older generations so stale timer
completion cannot lock a later foreground session.

A policy that retains grace on foreground also retains scheduling across a
foreground or inactive event that arrives while a facade await is in progress.
A cancel-on-active policy invalidates that same scheduling generation.

The pending task retains the coordinator until the scheduled lock completes
or an explicit lifecycle event cancels it, so dropping the host's last
reference cannot silently abandon an already scheduled security action.

Tests inject manual time and continuations. No test waits for wall-clock sleep.

## Save And Activation Behavior

Background grace first atomically cancels activation if one is in progress.
Unlocked and saving states may receive the bounded grace interval; the facade
retains responsibility for commit-aware save cancellation and teardown when
lock is eventually requested. The lifecycle coordinator does not inspect save
input, encrypted output, or private state.

## Failure And Diagnostics

An unexpected timer failure records only `graceTimerUnavailable` and requests
lock fail-closed. Event, policy, status, failure, and coordinator descriptions
contain fixed non-sensitive values and no underlying error text.

## Privacy Boundaries

The coordinator stores only neutral event/timer bookkeeping. It stores no key,
private model, public snapshot, path, record ID, saved-only membership, private
count, or user-entered value. It performs no filesystem, Keychain, defaults,
network, crypto, hydration, or persistence operation.

## Verification Coverage

Tests cover side-effect-free construction, no activation on foreground,
immediate and grace background lock, foreground cancellation, protected-data
override, termination, repeated events, atomic activation cancellation,
saving-state timeout, manual clock advancement, stale timers, event ordering,
redaction, absence of private-state duplication, public-cache isolation, and
source guards for platform/runtime coupling.

## Deferred

- platform notification and scene adapters
- SwiftUI and app-launch integration
- UI obscuring and unlock prompts
- migration and plaintext cleanup
- cloud sync and remote conflict behavior
- recovery, onboarding, and key rotation

## Recommended Phase 2D-38

Design the SwiftUI presentation boundary that may consume redacted facade and
lifecycle status without exposing keys or decrypted private data to views.
