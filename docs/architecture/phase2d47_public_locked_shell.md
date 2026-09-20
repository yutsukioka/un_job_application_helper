# AtlasVault Phase 2D-47 Public Locked Shell

## 1. Purpose

Phase 2D-47 adds the first dedicated SwiftUI shell for the locked AtlasVault
state. It keeps public job search available through injected state and actions
without reusing the legacy root hierarchy or exposing private vault state.

## 2. Scope

This phase adds one public-only value model, one standalone SwiftUI view, and
fake-state tests. It does not wire the shell into an app entry point,
production navigation, a runtime host, or a view model. It adds no automatic
activation, unlock prompt, private panel, private-state rendering, save action,
migration, cloud sync, recovery, onboarding, key rotation, or production
readiness claim.

## 3. Public Model Boundary

`AtlasLockedPublicShellModel` contains only:

- a non-sensitive locked, no-vault, or key-unavailable status;
- public service availability and public-cache freshness classes;
- an in-memory public search query;
- public job result presentation values;
- public search progress; and
- whether an explicit unlock request may be offered.

The model has no private collections, keys, encrypted envelopes, filesystem
paths, compatibility responses, or persistent conformance. Its fixed
description redacts even public query and result values to prevent accidental
diagnostic capture.

## 4. Injected Action Boundary

`AtlasLockedPublicShellActions` accepts async public-search and explicit-unlock
closures. Constructing the action container or view invokes neither closure.
The shell cannot create a service, call an endpoint, inspect a cache, activate
a vault, or obtain an unlock secret. A later reviewed host must own task
results, cancellation, and presentation replacement.

## 5. Dedicated SwiftUI View

`AtlasLockedPublicShellView` renders a locked status bar, public search field,
public result list, and injected unlock command. It is a new hierarchy and does
not reuse `AtlasRootView`, `AtlasSearchViewModel`, legacy sidebar panels, or
their private refresh path.

The view holds only transient public query text and its current injected action
task. Leaving the view cancels that task. It does not persist or restore
presentation state and does not access an API client, cache, Keychain,
filesystem, crypto, runtime actor, or public snapshot directly.

## 6. Private Compatibility Exclusion

The locked shell has no dependency that can load legacy saved-search or tracker
state. The Phase 2D-46 endpoint recorder proves a shell public-search action
records only the public-search category and leaves every private compatibility
category at zero. Hiding a legacy panel would not satisfy this boundary; the
private fetch and publication path is absent from this hierarchy.

## 7. Public Cache Boundary

The shell may display public jobs supplied by a reviewed public-only host
boundary. It does not read or mutate `AtlasPublicLocalSnapshot` itself. Public
detail-cache access remains excluded until provenance, namespace, and
coexistence gates are reviewed. Callers must not supply tracker-derived or
otherwise saved-only job identifiers as public results.

## 8. Lock And Failure Replacement

The shell model cannot hold private presentation. A host replacing unlocked
content after explicit lock, background lock, protected-data loss, failed
activation, or fatal save containment creates a fresh public-only model. The
Phase 2D-40 adapter and Phase 2D-46 host remain responsible for clearing
private projection before this shell is rendered.

## 9. Test Coverage

Tests verify:

- the model exposes only public fields and is not persistent;
- locked, no-vault, and key-unavailable statuses are non-sensitive;
- model, job, and action descriptions redact values;
- construction invokes no action;
- public search and unlock actions are injected;
- locked public search remains usable;
- lock, activation-failure, fatal-save, and background-lock replacements
  contain no private projection;
- Phase 2D-46 integration records only public search, performs no private
  compatibility call, and does not replace public-state bytes; and
- source guards exclude the legacy root, private refresh, private models,
  compatibility routes, API/cache services, Keychain, filesystem, crypto,
  networking, reference capture, persistence, and app entry points.

All fixtures are unmistakably fake and in memory. The phase creates no
`.atlasvault` export.

## 10. Deferred

Deferred work includes:

- production app-host and app-entry wiring;
- observable ownership of public search results;
- production public-search service adaptation;
- public detail-cache provenance and namespace isolation;
- explicit unlock input UI;
- private unlocked views and save actions;
- accessibility and screen-capture review for private views;
- platform lifecycle subscriptions;
- LocalAuthentication;
- migration and user-confirmed plaintext cleanup;
- cloud sync, recovery, onboarding, and key rotation; and
- production threat-model and readiness review.

## 11. Recommended Phase 2D-48

Phase 2D-48 should design explicit unlock UI ownership and secure input
handoff. The public locked shell must remain independently usable and must not
gain direct runtime, private compatibility, or secret-input dependencies.
