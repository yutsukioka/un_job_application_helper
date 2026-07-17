# Phase 2D-43 AtlasVault SwiftUI Integration Readiness

## 1. Purpose

Phase 2D-43 audits the local Apple AtlasVault stack before any production
SwiftUI integration. It records what can be reused, what still needs an
integration boundary, and which capabilities remain blocked. This document is
a readiness gate, not a production-readiness claim.

## 2. Design-Only Scope

This phase adds documentation only. It adds no SwiftUI view, observable object,
property wrapper, app-entry wiring, platform lifecycle subscription, unlock
prompt, migration, cloud sync, onboarding, recovery UX, or key rotation.

## 3. Complete Service Inventory

| Layer | Current components | Current role |
| --- | --- | --- |
| Composition | `AtlasVaultRuntimeFactory`, `AtlasVaultRuntimeServices`, `AtlasVaultPerVaultServiceFactory` | Constructs injected services without activating them. |
| Activation | `AtlasVaultActivationController` | Resolves an explicit activation request, loads encrypted state, and hydrates private state under injected dependencies. |
| Runtime | `AtlasVaultRuntimeFacade` | Serializes activation, lock, status, private-state access, and encrypted save operations. |
| Lifecycle | `AtlasVaultLifecycleCoordinator` | Converts neutral lifecycle events into lock decisions without subscribing to a platform. |
| Private state | `AtlasVaultPrivateStateStore` | Holds generation-checked hydrated records in memory and clears them on lock or failure. |
| Presentation | `AtlasVaultPresentationAdapter` | Projects runtime status and optional private state into non-persistent presentation values. |
| Unlock input | `AtlasVaultUnlockRequestCoordinator`, secret-buffer and injected derivation/activation seams | Enforces single-use requests and bounded secret ownership without providing UI. |
| Persistence | Persistence coordinator, path locator, directory preparer, local-store reader/writer, merger, saver, hydrator, crypto, and atomic writer | Loads and saves encrypted record envelopes under explicit calls. |
| Root and key access | Application Support root provider and `AtlasVaultKeyStore` / `AtlasKeychainVaultKeyStore` | Encapsulates platform root lookup and Keychain operations behind protocols. |
| Public cache | `AtlasPublicLocalSnapshot`, per-job `AtlasJobDetail` files, and `AtlasLocalCache` | Persists the public snapshot and public detail cache independently from private vault state. |
| Existing app model | `SearchViewModel` and `AtlasIOSHostApp` | Existing application behavior; neither is integrated with the AtlasVault runtime. |

## 4. Runtime Facade Readiness

The runtime facade is ready as the sole vault-facing command and status seam.
Its actor serialization, explicit activation, lock, private-state snapshot, and
save paths are tested. Integration remains constrained because no `@MainActor`
owner, host lifetime, or UI task policy currently drives it.

## 5. Presentation Adapter Readiness

The stateless presentation adapter is ready for runtime-neutral projection. It
exposes UI-safe status and private in-memory presentation models without keys,
record envelopes, or filesystem URLs. A future observable owner must still
publish snapshots, reject stale generations, and clear projected state on every
non-unlocked transition. While locked, the adapter must receive no private-state
snapshot and must project no private collection, including legacy saved-search
or tracker state.

## 6. Lifecycle Coordinator Readiness

The lifecycle coordinator is ready to consume injected, platform-neutral
events and apply the reviewed lock policy. It does not subscribe to iOS or
macOS notifications. A later host phase must define event sources, ordering,
scene aggregation, background grace periods, and cancellation ownership. A
Phase 2D-37 lifecycle lock transition must clear the runtime private state and
remove its presentation projection before a locked view is rendered.

## 7. Unlock Request Coordinator Readiness

The unlock request coordinator is ready as a test-only, runtime-neutral secret
handoff seam. Requests are single-use, concurrent dispatch is serialized, and
owned references are cleared on success, failure, cancellation, and timeout.
Production passphrase derivation, recovery-key parsing, and UI capture remain
unimplemented.

## 8. Private-State Store Readiness

The actor-isolated private-state store is ready to hold decrypted records only
in memory. Generation checks prevent stale installation and lock or activation
failure clears state. It is intentionally module-internal and must not become a
persistent cache or a public status source.

## 9. Runtime Composition Readiness

The composition factory constructs the root provider, key-store adapter,
crypto, persistence, activation, and runtime seams without querying them. It is
ready for use by a future host composition layer, subject to host ownership and
platform policy design. Construction must remain free of unlock, Keychain,
filesystem, hydration, save, and network side effects.

## 10. Activation Controller Readiness

The activation controller is ready for explicit activation under injected
services. It fails closed for missing keys, authentication failures, corrupt or
unsupported stores, and path failures, and it does not install partial hydrated
state. Production UI input and host task ownership remain separate work.

## 11. Persistence Path Readiness

The encrypted local persistence path supports locating a per-vault store,
preparing its directory, loading encrypted envelopes, hydrating in memory,
planning encrypted mutations, merging records, and saving. It is not connected
to app launch or existing view models. Platform file protection, backup policy,
and legacy-state transition are unresolved.

## 12. Atomic Writer Readiness

The atomic writer is implemented and tested through temporary roots, including
replacement and failure behavior. App-host integration must still establish
platform protection attributes, backup treatment, cross-process assumptions,
and recovery behavior after an interrupted write.

## 13. Keychain Adapter Readiness

The Keychain implementation remains behind `AtlasVaultKeyStore` and its
injected client. Presentation and unlock coordination must never import or
expose `SecItem` details. Local-key activation is usable only after a later host
phase defines explicit user action, accessibility policy, error mapping, and
whether authentication prompts are ever required.

## 14. Public/Private Snapshot Separation

The public cache has two formats: `AtlasPublicLocalSnapshot` and separate
per-job `AtlasJobDetail` JSON files managed by `AtlasLocalCache`. Saved-search
membership, saved-job membership, notes, snippets, drafts, generated-document
references, vault IDs, private record IDs, and private counts must not enter
either format. Private membership must not influence detail-cache filenames,
contents, requested keys, warmup selection, counts, or progress metadata.
Records managed by the AtlasVault runtime exist as decrypted values only in
generation-scoped in-memory state after unlock. This invariant does not describe
or legitimize the legacy plaintext compatibility state called out in Section 47.
Record type remains inside the encrypted payload; plaintext record metadata is
limited to the reviewed encrypted-record envelope allowlist.

## 15. Error Redaction Readiness

Runtime, presentation, lifecycle, and unlock errors use non-sensitive classes
or fixed descriptions. Future UI errors may explain the next safe action, but
must not contain keys, passphrases, paths, record IDs, record types, search text,
job keys, notes, snippets, drafts, ciphertext, or decrypted JSON.

## 16. Debug Redaction Readiness

Public status and secret-request debug output is redacted. Private presentation
models necessarily contain displayable private values while unlocked, so they
must retain fixed `<redacted>` string and debug descriptions. They must not
receive value-bearing descriptions, reflection logging, analytics, crash
breadcrumbs, or automatic persistence. Debug builds do not relax this rule.

## 17. Test Coverage Inventory

The local Swift suite covers composition, activation, facade state transitions,
lifecycle locking, private-state generations, presentation projection, secret
request lifetime, Keychain protocol behavior, encrypted hydration and saving,
store merging, atomic writes, and public-snapshot exclusions. The final Phase
2D-42 verification ran 470 tests with zero failures. This is strong unit and
temporary-root coverage, not host, simulator, accessibility, or UI coverage.

## 18. Missing macOS CI

The repository workflow currently runs Python checks on Ubuntu. It does not run
Swift Package Manager tests on a macOS runner. Until macOS Swift CI is added,
the Apple suite remains a locally verified manual gate.

## 19. Missing iOS Simulator CI

No iOS simulator build or test job validates the host, concurrency behavior,
protected-data transitions, scene lifecycle, or accessibility. A test-host
phase must precede production host wiring, and simulator CI should become a
required integration check.

## 20. MainActor Boundary

The future presentation owner should be `@MainActor`; the runtime facade,
lifecycle coordinator, private-state store, and unlock coordinator remain
actors outside it. Values must cross this boundary as immutable `Sendable`
snapshots. Secret buffers and runtime service objects must not be published.

## 21. Observable Adapter Boundary

Phase 2D-44 should define a test-only observable presentation protocol and fake
runtime state before selecting a concrete framework mechanism. The owner should
publish one coherent snapshot, not independently mutable status and private
collections. It must not be `Codable` or restored from persistent UI state.

## 22. View-Model Ownership

A dedicated vault presentation owner should own UI tasks and snapshots. Existing
`SearchViewModel` must not acquire vault keys, private state, persistence seams,
or unlock logic. Any eventual public-search coordination requires an explicit
adapter rather than merging public and private storage responsibilities. The
future SwiftUI layer must render vault-private content only from the reviewed
runtime-facade and presentation-adapter boundary. It must not bypass that
boundary by asking `SearchViewModel` or `AtlasAPIClient` to load private state
from compatibility endpoints.

## 23. App Host Ownership

The host should eventually own one process-level runtime graph and the
presentation owner that observes it. `AtlasIOSHostApp` has no AtlasVault
reference today. Phase 2D-45 must decide construction timing, dependency
injection, test replacement, teardown, and which object survives scene changes.

## 24. Scene/Window Ownership

Scene objects may observe process-level presentation state but must not each
construct a runtime, retrieve keys, or own independent decrypted stores. The
host must establish one authority for activation and lock while allowing each
window to clear its local rendered snapshot promptly.

## 25. Multiple-Window Policy

The first integration should use a single process-wide lock state. Any window
may request lock, and every window must stop displaying private state when the
generation changes. Concurrent unlock attempts should converge on the runtime
facade's serialized activation rather than create per-window sessions.

## 26. App Launch Behavior

Launch should construct only side-effect-free AtlasVault composition and show
locked or no-vault presentation. It must not resolve the AtlasVault Application
Support root, access Keychain, open a vault, hydrate records, or prompt for a
secret until an explicit user action reaches the activation boundary. This does
not prohibit `AtlasLocalCache` from resolving its separate public-cache location
to restore reviewed public snapshot or detail-cache data while locked.

## 27. No Automatic Unlock

No launch, scene activation, foreground event, public search, or preview may
automatically unlock. A local Keychain key changes the input source, not the
requirement for explicit activation intent.

## 28. Locked Public-Search Behavior

Public job search, `AtlasPublicLocalSnapshot`, and the public per-job detail
cache may remain available while the vault is locked. Detail cache content,
filenames, requested keys, and warmup behavior must derive only from public job
data, never saved-only membership. Locked state must not reveal private
membership, private counts, saved-only job keys, prior private labels, or
whether a public result has a private record. Public search is ready while
locked only when its view path does not invoke `refreshSidebarData()` or any
equivalent private saved-search or tracker refresh.

## 28.1. Locked-Shell Legacy Panel Gate

`AtlasRootView` is not safe to reuse unchanged as the first locked SwiftUI
shell. Its current hierarchy includes `AtlasSidebarView` and `SavedPanel`.
Those legacy panels can invoke `refreshSidebarData()`, whose current
implementation calls `savedSearches()` and `trackerRecords()` and publishes the
returned private saved-search and tracker state through `SearchViewModel`.

A locked shell must not fetch, publish, hydrate, retain, or display that private
state. Merely hiding labels or panels after the fetch is insufficient: the
private endpoint calls and publication path must be impossible while locked.
Reuse of `AtlasRootView` is therefore blocked until all private panels and every
equivalent refresh path are gated, disabled, or replaced and verified by tests.

The preferred first implementation is a dedicated public-only locked shell. It
may expose public search, `AtlasPublicLocalSnapshot`, and reviewed public job
detail cache data, but must omit legacy saved-search and tracker panels
entirely. Reusing a shared root is permitted only after a separately reviewed
design and implementation prove that private panels cannot be instantiated and
private refresh calls cannot run before unlock.

## 29. Explicit Unlock Flow

A future flow should collect an input source, create a single-use request,
dispatch it through the unlock coordinator, activate through the runtime
facade, then publish one unlocked snapshot only after activation and generation
checks succeed. Every failure returns to a private-state-free status.

## 30. Passphrase/Recovery Secret Lifetime

Secret input should remain in a mutable buffer for the shortest practical
interval, transfer once to an injected derivation seam, and clear on success,
failure, cancellation, timeout, backgrounding, and view dismissal. It must not
enter observable state, task labels, logs, errors, accessibility values, or
crash diagnostics.

## 31. Background Lock Behavior

The reviewed default is to lock on an eligible background or protected-data
event according to a host-supplied lifecycle policy. The host must cancel UI
tasks, clear presentation immediately, remove private panels and their state,
then await runtime teardown. Grace periods, if any, require a separate privacy
review.

## 32. Protected-Data Behavior

Protected-data unavailability should be treated as a lock trigger and should
prevent new activation or save work. The current neutral lifecycle seam does
not subscribe to protected-data notifications; platform mapping and race tests
belong in the test-host phase.

## 33. Save-Progress Behavior

The UI may show a non-sensitive save-in-progress command state while retaining
the currently unlocked projection for that same generation. It must not expose
mutation contents, record types, paths, byte counts that reveal private shape,
or serialized payloads.

## 34. Save-Failure Behavior

A recoverable save failure may leave the current unlocked in-memory projection
available only if the runtime confirms the session remains valid. The UI should
show a fixed non-sensitive error and permit an explicit retry. Authentication,
session, or integrity failures must clear presentation and lock.

## 35. Cancellation Behavior

Cancellation must propagate from the presentation task to the unlock or
runtime operation without publishing partial private state. The owner should
clear transient command state and ignore late snapshots whose generation or
request identity is no longer current.

## 36. UI Task Lifetime

The future presentation owner should own activation, lock, refresh, and save
tasks. Tasks must be cancelled on owner teardown, explicit lock, relevant scene
transition, and superseding command. Detached cleanup may outlive cancellation
only after all secret references have been removed from trusted state.

## 37. Navigation Behavior

Navigation must not define the security lifetime. Moving away from a private
screen may discard that screen's copy, but only the runtime lock transition
ends the unlocked session. Returning to a screen must request a current
generation snapshot rather than reuse stale navigation state.

## 38. Screen Capture/Privacy

Private screens need a later platform-specific screen-capture and app-switcher
policy. Sensitive content should be obscured before background snapshots where
the platform permits it. This phase does not claim that capture prevention or
redaction is implemented.

## 39. Accessibility Privacy

Accessibility labels may identify controls and general status but must not
announce keys, passphrases, recovery material, paths, record IDs, or private
content while locked. Whether unlocked private content is appropriate for an
accessibility value requires per-screen review and user expectations.

## 40. Analytics Prohibition

Do not emit private values, record types, private counts, vault identifiers,
unlock source details, or failure context to analytics. Initial integration
should add no vault analytics. Any later telemetry proposal requires a separate
privacy review and a non-sensitive allowlist.

## 41. Crash Diagnostic Prohibition

Crash breadcrumbs and error attachments must not include presentation
snapshots, secret requests, decrypted state, encrypted envelopes, filesystem
paths, or reflected runtime objects. Fixed error classes are the maximum
diagnostic detail allowed until a reviewed redaction layer exists.

## 42. Preview Fake-Data Policy

Previews and fixtures may use unmistakably fake sentinel data created in memory.
They must not read local stores, Keychain, Application Support, exports, private
repository inputs, or user history. Preview data must never be reused as a
production key, nonce, vault identifier, or path.

## 43. UI Test Strategy

Future UI tests should start with locked and no-vault states under a fake
runtime facade. They should verify no automatic unlock, public-search access,
explicit actions, redacted failures, lock clearing, cancellation, multiple
windows, accessibility labels, and background obscuring without real secrets.
The locked-shell gate requires these explicit tests:

1. The locked shell does not call `refreshSidebarData()`.
2. The locked shell does not invoke the saved-search compatibility endpoints.
3. The locked shell does not invoke the tracker compatibility endpoints.
4. The locked shell does not publish saved-search state.
5. The locked shell does not publish saved-job or tracker state.
6. The locked shell does not instantiate or render legacy private panels.
7. Public job search remains usable while locked.
8. Snapshot loading while locked accepts only `AtlasPublicLocalSnapshot`; any
   independently read detail file must remain within the reviewed public detail
   cache boundary.
9. Transition to unlocked state hydrates private presentation from AtlasVault
   runtime state, never from the public snapshot.
10. Activation failure leaves private panels absent.
11. Lock after unlock removes private panels and clears projected private
    state.
12. Background or protected-data lock leaves no private panel state visible.
13. No saved-only job key enters public detail-cache warmup metadata.
14. Test spies prove no plaintext compatibility endpoint is called while
    locked.
15. Preview fixtures use fake data only and do not instantiate the private
    refresh path.

## 44. Integration-Test Strategy

Phase 2D-46 should provide a test host that composes real runtime-neutral
services with fake root, key, clock, lifecycle, and secret-derivation seams.
Tests should cover actor-to-main ordering, generation rejection, task teardown,
temporary-root encrypted load/save, and zero production app-entry references.
The test host must also spy on `SearchViewModel` or its injected client and
prove that locked public search makes no `savedSearches()`,
`trackerRecords()`, `api/saved-searches`, or `api/tracker` request and publishes
no corresponding private state.

## 45. Failure-Injection Strategy

The test host must inject key unavailable, wrong key, corrupt ciphertext,
unsupported version, path failure, atomic-write failure, stale revision,
cancellation, timeout, background lock, and delayed completion. Each case must
leave public cache intact and expose no partial or stale private presentation.

## 46. App Upgrade Behavior

An app upgrade must not silently activate a vault, create one, move private
state, delete legacy files, or change Keychain accessibility. Unsupported store
versions should remain locked with a non-sensitive status until an explicit,
separately reviewed compatibility or migration flow exists.

## 47. Legacy Plaintext Coexistence

Existing `SearchViewModel` saved-search and saved-job behavior remains on its
legacy path. The encrypted runtime does not consume, rewrite, or delete that
state in this sequence. Coexistence can produce two sources of private truth,
so UI integration must not claim migration or silently prefer one source. The
current plaintext `api/saved-searches` and `api/tracker` endpoints are local
compatibility surfaces only. Their existence does not make them safe for
locked-shell loading, and the locked shell must not call them. Any temporary
unlocked compatibility bridge requires a separate reviewed transition plan.
These compatibility surfaces are not cloud sync endpoints.

## 48. Migration Remains Deferred

No migration runs in these phases. Import validation, duplicate handling,
rollback, user consent, crash recovery, and proof that plaintext cleanup is
safe all require a separate design and implementation sequence.

## 49. Cleanup Remains User-Confirmed

Legacy plaintext deletion must never be an incidental consequence of unlock or
save. Cleanup requires explicit user confirmation after encrypted persistence
has been verified, with recoverability and failure behavior designed first.

## 50. Cloud Sync Remains Deferred

The current system is local Apple vault integration only. It adds no cloud
transport, account identity, remote conflict handling, telemetry, or server
storage and makes no cloud-readiness claim.

## 51. Recovery Remains Deferred

Recovery-key generation, formatting, validation, import, backup guidance, and
recovery UX are not implemented. The request coordinator's recovery input case
is an injected test boundary, not a complete recovery capability.

## 52. Device Onboarding Remains Deferred

No device enrollment, transfer, pairing, trust establishment, or multi-device
key distribution exists. Do not infer onboarding readiness from the local
Keychain or encrypted-store implementations.

## 53. Key Rotation Remains Deferred

There is no vault-key or per-record rotation workflow. Rotation needs resumable
re-encryption, interruption handling, rollback, version negotiation, and proof
that old key material is no longer required before it can be integrated.

## 54. File Protection Unresolved

The atomic writer does not by itself settle iOS file-protection classes,
protection inheritance, availability before first unlock, or behavior while the
device is locked. A platform policy and tests are required before production
store creation.

## 55. Backup Policy Unresolved

The repository has not chosen whether local encrypted stores are included in
device backups or excluded. The choice must account for restore semantics,
Keychain availability, recovery expectations, stale copies, and user consent.

## 56. Memory-Zeroization Limitations

The unlock coordinator minimizes ownership and overwrites coordinator-owned
mutable bytes, but Swift, Foundation, compiler copies, copy-on-write storage,
and external buffers prevent a guarantee that every historical byte is zeroed.
Production review must treat bounded lifetime as risk reduction, not proof of
secure erasure.

## 57. Production Threat-Model Review

Before production integration, review device compromise assumptions, process
memory, screenshots, crash reporting, accessibility, clipboard and keyboard
behavior, Keychain accessibility, file protection, backups, malicious store
replacement, rollback, multiple windows, and denial-of-service paths. Record
accepted risks and platform-specific mitigations.

## 58. Production-Readiness Gate

Production launch is a no-go. Required gates include macOS and iOS CI, an
observable `@MainActor` adapter, host and scene ownership, a test host, a locked
SwiftUI shell, explicit unlock UI, production secret derivation, platform
privacy policy, file protection, backup policy, threat-model approval, and
resolution of legacy private-state coexistence. The current `AtlasRootView` is
not integration-ready unchanged because it can refresh and publish legacy
private state while locked. This audit adds no SwiftUI implementation and makes
no production-readiness claim; migration, legacy cleanup, cloud sync, recovery,
onboarding, and key rotation remain deferred.

## 59. Recommended Staged Phases

1. Phase 2D-44: test-only observable presentation adapter protocol with a fake
   runtime facade. No production view or app-entry wiring.
2. Phase 2D-45: app-host composition design covering process, scene, task, and
   dependency ownership.
3. Phase 2D-46: test-host integration with fake platform inputs and no
   production app entry point. It must prove locked public search performs no
   legacy private endpoint call or state publication.
4. Phase 2D-47: first locked-state-only SwiftUI shell, using one of two reviewed
   strategies:
   - Strategy A, preferred: create a dedicated public-only shell that exposes
     public search/cache and omits legacy saved-search and tracker panels.
   - Strategy B, only after tests: reuse a shared root after gating or replacing
     every private panel, preventing `refreshSidebarData()` and equivalent
     private fetches while locked, and proving no saved-search or tracker state
     is published before unlock.
   The dedicated public-only shell is the required initial choice unless a
   reviewed gating design proves equivalent safety. `AtlasRootView` must not be
   reused unchanged.
5. Later reviewed phases: explicit unlock UI, private-state rendering, save
   actions, migration, cleanup, recovery, and cloud work under separate gates.

## 60. Explicit Go/No-Go Table

`Ready` means the current capability can be consumed within its reviewed
boundary. `Ready with constraints` means the core seam exists but platform or
host policy is still required. `Design required` means the next integration
boundary has not been agreed. `Blocked` means it must not proceed under the
current architecture and evidence.

| Capability | Classification | Evidence and next gate |
| --- | --- | --- |
| Runtime facade | Ready with constraints | Actor API and tests exist; an `@MainActor` owner and host lifetime do not. |
| Presentation adapter | Ready with constraints | Stateless projection exists; observable ownership and stale-update orchestration belong to 2D-44. |
| Lifecycle coordinator | Ready with constraints | Neutral policy is tested; platform event subscription and scene aggregation are absent. |
| Unlock request coordinator | Ready with constraints | Single-use cleanup is tested; production derivation, parsing, and UI capture are absent. |
| Keychain | Ready with constraints | Protocol and adapter exist; explicit host action, accessibility policy, and prompt policy remain. |
| Encrypted store load | Ready with constraints | Activation and hydration are tested; host wiring, file protection, and backup behavior remain. |
| Encrypted save | Ready with constraints | Saver, merger, coordinator, and facade paths are tested; no user action or host integration exists. |
| Atomic writes | Ready with constraints | Atomic replacement is tested; platform protection and crash-recovery policy remain. |
| Public cache while locked | Ready | Public snapshot and per-job detail files are separate; private membership must not affect either format or detail warmup. |
| Public job search while locked | Ready with constraints | It remains available only through a path that cannot call `refreshSidebarData()` or private compatibility endpoints. |
| Reuse `AtlasRootView` unchanged as locked shell | Blocked | Existing sidebar and saved panels can fetch and publish private saved-search and tracker state. |
| Dedicated public-only locked shell | Ready with constraints | It may expose only public search/cache and must omit private panels, private refreshes, and automatic unlock. |
| Reuse `AtlasRootView` after private-panel gating | Design required | Gating or replacement, endpoint-call spies, state-publication tests, lifecycle clearing, and review are required first. |
| Saved-search and tracker panels while locked | Blocked | Visual hiding is insufficient; their fetch, publication, retention, and rendering paths must not run. |
| Private state display | Design required | In-memory projection exists; observable ownership, views, accessibility, and capture policy do not. |
| Lock action | Design required | Runtime lock exists; UI ownership, multi-window propagation, and task cancellation need host design. |
| Activation | Ready with constraints | Explicit runtime activation exists; production input and host orchestration do not. |
| Passphrase UI | Blocked | No secure UI flow or selected production-compatible Swift derivation implementation exists. |
| Recovery-key UI | Blocked | Parsing, validation, format, UX, and recovery policy are deferred. |
| LocalAuthentication | Design required | It is intentionally absent and is not required for the initial locked shell. |
| Migration | Blocked | Consent, validation, rollback, coexistence, and cleanup are not designed or implemented. |
| Cloud sync | Blocked | No account, transport, remote conflict, or server-storage design exists. |
| Onboarding | Blocked | No enrollment, transfer, or device-trust model exists. |
| Key rotation | Blocked | No resumable re-encryption or rollback design exists. |
| Production launch | Blocked | CI, host, UI, platform privacy, migration coexistence, and threat-model gates remain open. |
