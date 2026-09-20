# AtlasVault Phase 2D-44 Observable Presentation Adapter

## Purpose

Phase 2D-44 adds a runtime-neutral observation boundary around the existing
stateless `AtlasVaultPresentationAdapter`. It distributes UI-safe in-memory
presentation snapshots without adding SwiftUI, property-wrapper, view-model,
app-host, or app-entry integration.

## Scope

This phase implements:

- a `Sendable` presentation-update source protocol;
- sequenced presentation updates;
- an actor-isolated observable adapter;
- explicit cancellable subscriptions backed by `AsyncStream`;
- fake-state tests for ordering, privacy, cancellation, and save outcomes.

It adds no runtime facade polling or mutation, filesystem or Keychain access,
public-cache mutation, persistence, networking, platform lifecycle
subscription, UI framework dependency, migration, or cloud behavior.

## Boundary

`AtlasVaultPresentationUpdateSourcing` supplies already adapted
`AtlasVaultPresentationSnapshot` values. The observable adapter never receives
vault keys, unlocked sessions, hydrated record envelopes, filesystem paths, or
public snapshots. A future host owns the source that crosses from runtime state
to the stateless presentation adapter.

`AtlasVaultPresentationUpdate` pairs a snapshot with an adapter-lifetime
monotonic sequence. `AtlasVaultObservablePresentationAdapter` rejects a
sequence that is not strictly newer than the last accepted sequence. A source
that reconnects must continue the same monotonic sequence rather than restart
at zero.

## Explicit Observation

Construction is side-effect-free and starts with a fixed locked snapshot.
Calling `currentSnapshot()` does not contact the source. The first explicit
`subscribe()` starts exactly one source observation; later subscribers share
it and immediately receive the latest retained snapshot.

Subscriber cancellation first invalidates that subscription's read gate,
replaces its one-slot buffer with a locked private-free snapshot, and then
finishes and releases only that subscriber stream. The gate makes the
replacement unreadable after `cancel()` returns, while overwriting any unlocked
private snapshot that `AsyncStream.finish()` would otherwise preserve. Once
explicitly started, source observation continues even when no subscriber is
present. This allows a lock or fatal-containment update to clear retained
private presentation before a later subscriber appears. Source observation
ends when its stream ends or the adapter is destroyed.

If the source stream ends, the adapter fails closed: it installs a locked,
private-free snapshot, offers that final state to active subscribers, and then
finishes their streams. A later explicit subscription may start a new source
stream, but stale sequence values remain rejected.

## Snapshot Sanitization

Private presentation may be retained only for:

- `unlocked`;
- `saveInProgress`;
- recoverable `saveFailed`;
- committed `saveDurabilityUnconfirmed`.

For locked, no-vault, activating, locking, key-unavailable, corrupt-store,
unsupported-version, cancelled, or failed status, the adapter always replaces
the supplied private projection with `nil`. This is defense in depth around
the stateless adapter and prevents an unsafe injected snapshot from retaining
private values.

Recoverable save failure may preserve the current private projection because
the runtime facade exposes that status only for its reviewed pre-commit
category. A committed durability warning carries the refreshed projection.
Fatal save containment arrives as locking or locked and therefore clears the
projection.

## Ordering And Backpressure

Actor isolation serializes accepted updates and fan-out. Identical sanitized
snapshots update the accepted sequence but are not published twice. Each
subscriber uses `bufferingNewest(1)`, which bounds memory and ensures a slow
consumer receives the latest available snapshot. A slow consumer may skip
intermediate transitions; snapshots it does receive remain in monotonic source
order. Consumers that require every transition belong at a different runtime
coordination boundary.

## Privacy

The adapter and its update and subscription types use fixed redacted
descriptions. They do not encode or persist state. No error, status, debug
description, path, log, or analytics event includes private presentation,
record metadata, keys, or secret input. The adapter does not reference or
mutate `AtlasPublicLocalSnapshot`.

## Tests

Fake tests cover:

- side-effect-free construction and explicit first subscription;
- immediate current snapshot and one shared source observation;
- ordered multi-subscriber delivery;
- subscriber cancellation, buffered-private-state invalidation, and continued
  lock observation;
- bounded slow-subscriber behavior;
- stale-sequence rejection and duplicate-state suppression;
- activation, activation failure, recoverable save failure, committed warning,
  fatal containment, lock, and repeated lock;
- unsafe private-projection sanitization;
- fail-closed source completion;
- stateless-adapter integration;
- public-snapshot immutability;
- fixed redacted descriptions and non-persistable types;
- source guards against UI, platform, persistence, runtime, cache, and secret
  coupling.

## Deferred

- `@MainActor` and observable-framework ownership
- App-host and scene composition
- Test-host integration
- Production SwiftUI views and app entry points
- Explicit unlock UI and platform authentication
- Migration and plaintext cleanup
- Cloud sync, recovery, onboarding, and key rotation
- Production threat-model and readiness review
