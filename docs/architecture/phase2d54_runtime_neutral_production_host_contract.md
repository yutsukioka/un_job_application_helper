# AtlasVault Phase 2D-54 Runtime-Neutral Production Host Contract

## 1. Purpose

Phase 2D-54 implements the narrow contracts and inert factory required before a
production AtlasVault host can be built. The phase makes the first explicit
local-key journey representable without constructing that host or connecting it
to an application entry point.

The new API separates public job data, public snapshot restoration,
non-semantic vault selection, host commands, and dependency construction. It
does not claim that production host integration is ready.

## 2. Phase Scope

This phase adds:

- runtime-neutral public job-data values and a service protocol;
- a restore-only public snapshot value and protocol;
- a validated, redacted vault-ID selection result and selector protocol;
- a first-journey production-host protocol;
- a lazy unlock-controller builder protocol;
- a dependency bundle;
- an injected host-builder protocol and side-effect-free factory;
- contract, redaction, source-boundary, and construction tests.

It adds no concrete adapter, registry, host actor, task owner, lifecycle source,
view, navigation, or application wiring.

## 3. Reconstructed Phase 2D-53 Baseline

The baseline was reconstructed from Git and GitHub before implementation:

- starting `origin/master`:
  `9c401874213a1222ff26f6a75caaae11d4b642b0`;
- merged design PR:
  [#69](https://github.com/yutsukioka/un_job_application_helper/pull/69);
- final Phase 2D-53 head:
  `595cc7dc067ab735573daf01196a6a92ba73fa13`;
- Phase 2D-53 merge:
  `9c401874213a1222ff26f6a75caaae11d4b642b0`;
- review threads:
  three total and zero unresolved;
- Python and GitGuardian checks:
  successful.

Six focused merged baselines were rerun locally. They executed 155 tests with
zero failures across runtime composition, runtime facade, lifecycle,
presentation observation, locked-shell flow, and test-host integration.

## 4. Existing Blockers

Phase 2D-53 identified these blockers to a concrete host:

- no narrow concrete public job adapter;
- no narrow concrete public snapshot adapter;
- no reviewed vault-ID registry or selection-storage policy;
- no concrete production host implementation;
- no process-wide admission and reconciliation implementation;
- no production presentation source or app-level presentation owner;
- no platform lifecycle adapter;
- no reviewed app-entry integration.

Phase 2D-54 resolves only the missing contract and inert-factory portion.

## 5. Contract-First Objective

The Phase 2D-54 API makes invalid dependencies and operations difficult to
represent. A future host builder receives only narrow protocols. Views cannot
receive the dependency bundle because the future host protocol returns only
public job results and the reviewed sanitized locked-shell flow.

The contracts are implemented before concrete adapters so adapter reviews can
verify conformance to an already constrained boundary.

## 6. Public Job-Data Contract

`AtlasPublicJobSearching` represents five reviewed public operations:

- coarse service health;
- public job search;
- public source summaries;
- public update summaries;
- public detail lookup through an explicit public reference.

Search requests carry query text, limit, and offset. Search results carry
`AtlasLockedPublicJob` values and public pagination totals. All coordination
values are `Sendable`, omit persistence conformance, and use fixed redacted
descriptions.

## 7. Public Health Contract

`AtlasPublicServiceHealth` contains only:

- a coarse checking, available, or unavailable state;
- an optional open-job count;
- an optional enabled-source count;
- an optional public last-sync time.

It contains no endpoint, storage location, schema diagnostic, or raw service
error. Negative counts are rejected with a fixed non-sensitive error.

## 8. Public Source And Update Contract

`AtlasPublicSourceStatus` contains a public source identifier, display name,
coarse availability, and optional public open-job count.

`AtlasPublicUpdateStatus` contains a public source identifier, optional
observation time, and non-negative fetched, changed, and closed counts.

Both values redact their descriptions. They grant no refresh, write, or
transport authority.

## 9. Public-Detail Reference Boundary

`AtlasPublicJobReference` is a distinct redacted value used by the public
detail operation. Its raw value is module-internal, so it is not a general UI
diagnostic field. `AtlasPublicJobDetailResult` contains only the reference, one
safe public job value, and public detail text.

This phase does not implement provenance or a concrete detail adapter. It also
does not grant detail-cache restore, warmup, or write authority.

## 10. Private Compatibility Operations Made Unrepresentable

The public service protocol has no command for user-saved searches, job
membership, application status, notes, snippets, drafts, generated material,
vault records, or private compatibility routes.

There is no broad request escape hatch, arbitrary endpoint operation, mutation
command, or synchronization command. A future adapter must implement the five
public operations directly.

## 11. Public-Snapshot Restore-Only Contract

`AtlasPublicSnapshotRestoring` has one operation: `restore()`.

`AtlasProductionPublicSnapshot` contains:

- the snapshot time;
- coarse public service health;
- safe public job values;
- public source summaries;
- public update summaries.

The value is `Sendable`, uses a fixed redacted description, and is not a
transport serialization model. These values are sufficient for a future
adapter to rebuild the public portions of `AtlasLockedPublicShellModel`
without restoring transient query text or unlock controls.

## 12. Detail-Cache Exclusion

The restore contract contains no:

- detail file or detail count;
- detail warmup;
- detail restoration;
- cache replacement;
- cache write;
- delete authority;
- arbitrary saved-only key.

Detail provenance and a concrete public cache boundary require a separate
reviewed phase.

## 13. Vault-ID Selection Contract

`AtlasVaultIDSelecting` returns `AtlasVaultIDSelection`, which has exactly two
states:

- no selected vault;
- one `AtlasSelectedVaultID`.

The result exposes no display label, list, count, timestamp, path, record
metadata, or credential-presence signal.

## 14. Vault-ID Validation And Redaction

`AtlasSelectedVaultID` delegates validation to
`AtlasInjectedRootVaultPathLocator.validatedVaultID(_:)`. Phase 2D-54 does not
introduce a second validator.

The existing policy rejects empty or whitespace-padded values, path
separators, dot components, reserved semantic record IDs, unsupported
characters, and excessive length. The selected raw ID remains
module-internal. Public descriptions for both the ID and selection result are
fixed and redacted.

## 15. No Vault Registry Implementation

The selector protocol defines a result, not storage. This phase performs no:

- registry read or write;
- directory scan;
- credential probe;
- preferences access;
- vault creation or deletion;
- multiple-vault switching;
- automatic selection.

A concrete registry and selection policy remain required.

## 16. Production-Host Protocol

`AtlasVaultProductionHosting` is a `Sendable`, runtime-neutral protocol. It
represents the first public-locked-to-unlocked-transition journey and exposes
no implementation dependency.

The protocol uses merged public values:

- `AtlasLockedShellUnlockFlowState`;
- `AtlasVaultUnlockMethod`;
- `AtlasVaultUnlockSubmission`;
- `AtlasVaultLifecycleEvent`;
- Phase 2D-54 public search request and result values.

## 17. First-Journey Command Surface

The host protocol contains:

- explicit start and stop;
- current sanitized flow state;
- public job search;
- explicit unlock-panel request;
- unlock method selection;
- unlock submission;
- unlock cancellation;
- explicit lock;
- platform-neutral lifecycle-event delivery.

No operation is invoked by the contract itself.

## 18. Fixed Unlocked-Transition Boundary

Host commands return `AtlasLockedShellUnlockFlowState`. Its terminal success
mode is the existing non-sensitive `unlockedTransition`.

Phase 2D-54 adds no post-unlock private model, route, navigation destination,
or private view command. A later phase must review any transition beyond this
fixed boundary.

## 19. No Private Rendering

The host protocol exposes no hydrated records, private presentation state,
saved user content, private count, encrypted envelope, key, or persistence
object. The new source imports Foundation only.

Views remain limited to the already reviewed locked shell, explicit unlock
panel, and non-sensitive transition.

## 20. No Mutation Or Save API

The first-journey host protocol has no mutation, save, durability, or private
record command. The dependency bundle may hold the existing runtime facade for
a future concrete host, but that facade is module-internal to the bundle and
is never returned through the host protocol.

Write-side product behavior remains outside this phase.

## 21. Dependency Bundle

`AtlasVaultProductionHostDependencies` stores these injected abstractions:

- `AtlasPublicJobSearching`;
- `AtlasPublicSnapshotRestoring`;
- `AtlasVaultIDSelecting`;
- `AtlasVaultRuntimeFacading`;
- `AtlasVaultLifecycleCoordinating`;
- `AtlasVaultPresentationObserving`;
- `AtlasVaultUnlockRequestCoordinating`;
- `AtlasVaultUnlockPresentationControllerBuilding`.

Stored references are module-internal. The public initializer performs only
assignments, and the public description is fixed and redacted.

## 22. Lazy Unlock-Controller Builder

`AtlasVaultUnlockPresentationControllerBuilding` receives:

- one already validated selected ID;
- a reviewed capability snapshot;
- the injected unlock request coordinator.

The contract exists because the controller cannot be created until explicit
host-side vault selection has completed. The builder has no selection or
submission command and receives no concrete provider, storage client, or
runtime implementation detail.

The production capability remains local-key-only. Passphrase and recovery
remain unavailable without reviewed production providers.

## 23. Host Builder

`AtlasVaultProductionHostBuilding` receives the exact dependency bundle and
returns an inactive `AtlasVaultProductionHosting` value.

Its contract requires side-effect-free host construction. A concrete builder
is intentionally absent because the adapter and registry blockers remain.

## 24. Side-Effect-Free Factory

`AtlasVaultProductionHostFactory` stores one dependency bundle and one injected
host builder. Initialization performs assignments only.

`makeHost()` is the sole factory operation. It calls only the injected builder,
exactly once per invocation, passes the exact dependency bundle, and returns
the builder result. It does not call the returned host.

The factory and bundle expose fixed redacted descriptions.

## 25. No Default Production Constructor

The factory has no zero-argument initializer and no concrete production
convenience. Callers must supply every dependency and the host builder.

This prevents the contract phase from silently choosing broad API, cache,
registry, lifecycle, or presentation implementations.

## 26. Construction Versus Explicit Host Creation

Dependency-bundle construction and factory construction invoke nothing.

Explicit `makeHost()` invokes only the host builder. It does not:

- search public jobs;
- restore a public snapshot;
- select a vault;
- call runtime or lifecycle methods;
- start presentation observation;
- dispatch an unlock request;
- start the returned host.

Any future concrete host must retain the same construction/start separation.

## 27. No Automatic Start

The host protocol has an explicit `start()` operation. The factory never calls
it. Merely creating dependencies, a factory, or a host value cannot establish
the host lifetime.

Start semantics, idempotence, task ownership, and teardown belong to the
future concrete host implementation.

## 28. No Automatic Unlock

The host command surface requires an explicit unlock-panel request, method
selection, and submission. Factory or host construction selects no vault and
submits no unlock request.

The raw test-key path is absent. Production passphrase and recovery capability
remain unavailable.

## 29. No App-Entry Or Navigation Wiring

No app entry point, scene, root view, route, navigation source, locked shell,
unlock panel, or Phase 2D-52 flow file changes in this phase.

The new contracts import no UI framework. Application wiring remains blocked
until concrete dependencies and a concrete host pass review.

## 30. No Concrete API, Cache, Or Vault-ID Adapters

Phase 2D-54 defines protocols only. It adds no:

- broad API-client wrapper;
- cache-file reader or writer;
- public snapshot decoder;
- public detail-cache adapter;
- vault registry;
- vault selection storage;
- concrete host.

The contracts therefore perform no network, storage, credential, or
cryptographic operation.

## 31. Error And Diagnostic Redaction

Public service, snapshot, and vault-selection errors are fixed enums. They do
not retain underlying errors, queries, identifiers, paths, responses, or
server text.

Every new value that can be rendered diagnostically has a fixed
`description` and `debugDescription`. Query text, public job IDs, detail text,
source identifiers, selected vault IDs, and dependency identities are
redacted.

## 32. TDD Evidence

The persistent test perspective was written before production source.

The first repository change was a valid red test that referenced the missing
contracts and factory. The focused compile failed only on those absent symbols.
That red state was committed and pushed as `5db77d09`.

After the smallest coherent implementation, the focused
`AtlasVaultProductionHostFactoryTests` suite executed 23 tests with zero
failures.

## 33. Test Coverage

The Phase 2D-54 suite covers:

- public request/result, health, source, update, reference, and detail shape;
- validation and fixed error behavior;
- redacted diagnostics and `Sendable` values;
- private-operation and detail-cache exclusions;
- restore-none and restore-safe-snapshot behavior;
- validator parity and vault-ID rejection cases;
- first-journey host protocol conformance;
- exact injected dependency identity;
- zero calls during dependency and factory construction;
- builder-only explicit host creation;
- no automatic host start;
- no default concrete constructor;
- source guards, artifact scans, and the exact phase file set.

## 34. Go/No-Go Update

| Capability | Status |
| --- | --- |
| Narrow public-search contract | Implemented |
| Narrow public-snapshot restore contract | Implemented |
| Vault-ID selection contract | Implemented |
| Concrete public-search adapter | Not implemented |
| Concrete public-snapshot adapter | Not implemented |
| Vault-ID registry and selection storage | Not implemented |
| Production-host contract | Implemented |
| Side-effect-free factory seam | Implemented |
| Concrete production host | Not implemented |
| App-entry wiring | Blocked |

The implemented contracts are ready for review and adapter work. They do not
remove the blockers to production integration.

## 35. Deferred Work

Deferred work includes:

- concrete narrow public job adapter;
- concrete restore-only public snapshot adapter;
- public detail provenance and cache boundary;
- vault registry and selection policy;
- concrete production host and builder;
- process-wide admission and reconciliation;
- production presentation source and app-level owner;
- platform lifecycle delivery;
- app-entry and navigation integration;
- post-unlock private rendering;
- save and mutation journeys;
- passphrase and recovery providers;
- migration, cleanup, recovery, onboarding, key rotation, and cloud sync;
- production threat-model and readiness review.

## 36. Next Product Gate

Phase 2D-55 must implement and review the concrete narrow public-search
adapter, the concrete restore-only public-snapshot adapter, and the
non-semantic vault-ID registry/selection policy and adapter before a concrete
production host can be created.

Phase 2D-55 must keep the detail cache excluded, keep private compatibility
operations unavailable, leave app entry unchanged, perform no automatic
unlock, and preserve local key as the only production unlock capability.

Phase 2D-54 creates no Phase 2D-55 branch or files.
