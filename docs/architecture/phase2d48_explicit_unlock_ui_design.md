# Phase 2D-48 AtlasVault Explicit Unlock UI Design

## 1. Purpose

Phase 2D-48 defines an explicit, capability-driven unlock UI boundary for the
dedicated public-only locked shell. It records what the Apple runtime can
actually perform today and prevents future views from advertising an unlock
method that has only a contract, Python reference, or injected test seam.

## 2. Explicit Design-Only Scope

This phase adds documentation only. It adds no SwiftUI panel, capability
model, wrapped-key model, key-unwrapping implementation, cryptographic
dependency, runtime call site, navigation, app-entry wiring, migration, cloud
sync, onboarding, recovery UX, key rotation, or production-readiness claim.

## 3. Existing Public-Only Locked Shell

`AtlasLockedPublicShellView` is a dedicated hierarchy that exposes public job
search and an injected unlock-request action. It does not reuse
`AtlasRootView`, load private compatibility state, or activate the vault. The
future unlock panel is presented beside or above this shell by an unwired host;
it must not replace the shell's independent public-search boundary.

## 4. Existing Runtime Facade

`AtlasVaultRuntimeFacade.activate(_:)` accepts a non-semantic vault ID and an
optional already-unwrapped 32-byte vault key. A `nil` supplied key delegates to
the activation controller's stored-key loader. The facade does not derive a
key from passphrase or recovery material and must not receive those inputs.

## 5. Existing Unlock Request Coordinator

`AtlasVaultUnlockRequestCoordinator` owns single-use request state and accepts
passphrase, recovery-key, local-key, and module-internal supplied-test-key
inputs. Passphrase and recovery processing are injected closures. Their
existence is a protocol/test seam, not evidence of a production provider.

## 6. Existing Keychain Key-Store Capability

`AtlasKeychainVaultKeyStore` is a reviewed `AtlasVaultKeyStore`
implementation. Production composition can load a 32-byte key through that
protocol after explicit activation. The view must never instantiate or call
the adapter, expose `SecItem` details, or probe whether an item exists.

## 7. Current Passphrase Unwrap Capability Audit

The Python `vaultsync` reference implements Argon2id derivation and
AES-256-GCM vault-key unwrap. Apple Swift code has no reviewed Argon2id
provider, wrapped-key parser connected to activation, or production
passphrase-unwrapping dependency. Passphrase unlock is therefore unavailable
in production today.

## 8. Current Recovery-Key Capability Audit

The encrypted-vault contract describes recovery material as a future wrapping
source, but the current Python `WrappedKey` accepts only type `passphrase`.
There is no reviewed recovery-key format, parser, checksum policy, Swift
provider, or activation integration. Recovery-key unlock is unavailable in
production today.

## 9. Current Supplied-Test-Key Capability

The runtime and unlock coordinator support an already-unwrapped fake 32-byte
key for deterministic tests. Its request initializer is module-internal. This
capability is test-only and must never appear as a production method, text
field, accessibility action, preview command, or fallback.

## 10. Capability Matrix

| Method | Current implementation | Production UI status | Boundary |
| --- | --- | --- | --- |
| Local Keychain key | Reviewed Swift key-store and runtime path | Available with constraints | Explicit request through presentation/coordinator/facade only |
| Passphrase-wrapped key | Python reference only; injected Swift test seam | Unavailable | Requires reviewed Swift metadata/parser/unwrap provider |
| Recovery-key-wrapped key | Contract intent only | Unavailable | Requires reviewed format, parser, vectors, and Swift provider |
| Supplied raw fake key | Module-internal test path | Prohibited | Tests only; never production UI |

Capability availability means the method is implemented by the injected
runtime composition. It does not reveal whether a credential or vault exists.

## 11. Local Keychain Unlock Option

The production panel may offer a neutral "Use local key" action only when its
injected capability snapshot marks the runtime path available. Dispatch
creates a `.localKey` request. Missing item, inaccessible item, invalid key, or
store failure map to a non-sensitive status without revealing Keychain
details or automatically trying another method.

## 12. Passphrase Option

The production panel must not render or enable a passphrase field while the
capability is unavailable. A future reviewed provider may enable it through
the same capability boundary; the view then transfers one-shot UTF-8 bytes to
the coordinator and never invokes Argon2id or AES-GCM itself.

## 13. Recovery-Key Option

The production panel must not render or enable a recovery-key field while the
capability is unavailable. Enabling it requires a separately reviewed format,
parser, unwrap provider, vectors, and generic failure behavior. It must never
be treated as a passphrase by fallback.

## 14. Unsupported Or Unavailable Method Behavior

Unavailable methods are absent from selectable production controls. If
capability changes invalidate the selected method, the owner clears local
input, cancels pending work, selects no method, and exposes a fixed
non-sensitive unavailable status. Submission is rejected before secret access.

## 15. No Automatic Unlock

Launch, foregrounding, public search, Keychain availability, panel appearance,
preview creation, and capability refresh must not activate the vault. Every
attempt begins with an explicit user command.

## 16. No LocalAuthentication

This plan adds no `LocalAuthentication`, policy evaluation, context, or prompt.
Any future use requires a separate threat model, capability, lifecycle, error,
and cancellation design.

## 17. No Biometric Prompt

No face, fingerprint, watch, or device-passcode prompt is triggered or
advertised. Keychain accessibility remains the existing
after-first-unlock-this-device-only policy unless a later reviewed phase
changes it.

## 18. Unlock Screen Entry Action

The locked shell's injected unlock action asks an unwired presentation owner
to show the panel. Entering the panel changes presentation only; it does not
load metadata, resolve a root, read Keychain, derive a key, or start activation.

## 19. Cancel Action

Cancel clears local secret input before dismissing the panel, cancels any
owned task/request, closes presentation authorization for that request, and
returns to the current public-only locked shell. Cancellation publishes no
secret or private state. If activation has already committed despite
cancellation, the host treats that runtime outcome as authoritative but
immediately locks it and awaits the private-free barrier; the cancelled panel
must not publish unlocked presentation.

## 20. Lock Action

Lock remains a host/runtime command. If received while the panel is visible or
submitting, it invalidates the request, clears local input, dismisses private
progress, and renders a fresh public-only locked state.

## 21. Method Selection

Selection is limited to methods marked available in one immutable capability
snapshot. Changing selection first clears input and cancels pending method
work. A test-only raw-key method is filtered before presentation.

## 22. Secret Input Ownership

The secure input control owns one transient Swift `String` until submit. It
then constructs one narrowly owned mutable byte buffer, clears its binding,
and transfers the buffer to the presentation/controller boundary. No view
model retains a second copy.

## 23. One-Shot Secret Dispatch

Each submit creates one `AtlasVaultUnlockRequest`. The coordinator commits
single-use ownership before dependency work. Repeated or concurrent submission
of the same request is rejected by the coordinator. That per-request guarantee
does not serialize distinct request IDs created by different views or windows.

### Process-Wide Unlock Admission

A future process host must own one process-global unlock-admission token before
any view creates or dispatches a request. The first host has one runtime and
one active-vault slot, so requests for different vault IDs must not derive or
activate concurrently. The global token serializes distinct requests across
vault IDs, windows, and presentation owners, remains held through derivation,
activation, cancellation, timeout reconciliation, and private-free lock
acknowledgement, and is released only after one authoritative terminal state.
Per-view disabling and any per-vault bookkeeping are defense in depth, not the
concurrency authority. Supporting parallel vault runtimes would require a
separate reviewed architecture.

If admission is already held, another submit fails with a fixed busy state
before reading or transferring input. It must not start a second derivation,
dispatch another activation, or infer which method owns the token.

## 24. No Secret In Public Presentation State

Public state contains only capabilities, selected-method identifiers,
non-sensitive progress, and fixed failure categories. It contains no input
text, buffer, raw key, wrapped key, salt, nonce, KDF parameters, or request.

## 25. No Secret In Observable Snapshots

`AtlasVaultPresentationSnapshot` and observable updates never carry unlock
input. The future UI owner publishes one sanitized state after input has been
transferred and cleared.

## 26. No Secret In Errors

Errors use fixed categories such as unavailable, failed, cancelled, expired,
or corrupt vault. They do not echo input, method-sensitive details, lengths,
vault IDs, KDF parameters, Keychain status, paths, or underlying errors.

## 27. No Secret In Logs

The view, controller, coordinator, and provider do not log secret values,
request objects, wrapped metadata, or raw keys. Fixed lifecycle categories may
be logged only under a separately reviewed logging policy.

## 28. No Secret In Analytics

No analytics event records input, method selection, credential availability,
attempt count, failure subtype, vault existence, or secret length. This phase
adds no analytics integration.

## 29. No Secret In Accessibility Labels

Labels identify only the control purpose. Accessibility values,
announcements, focus restoration, UI tests, and debug trees must not expose
entered text or distinguish which credential bytes failed.

## 30. No Secret Persistence

Input, requests, buffers, presentation states, and capability states are not
`Codable` and are never written to disk, state restoration, preferences,
clipboard history, cache, crash reports, or navigation payloads.

## 31. No AppStorage

The future panel does not use `@AppStorage` for method selection, input,
attempt state, or capability state.

## 32. No SceneStorage

The future panel does not use `@SceneStorage`. Scene reconstruction returns to
a private-free locked shell and requires fresh explicit input.

## 33. No UserDefaults

The panel, presentation owner, and unlock boundary do not read or write
`UserDefaults`.

## 34. SecureField Usage

An enabled passphrase or recovery method uses `SecureField` or an equivalent
platform-protected entry control. Obscured rendering is not memory
zeroization, persistence protection, or permission to retain input.

## 35. Input Clearing On Submit

Submission copies input into the one-shot buffer and clears the SwiftUI
binding before awaiting asynchronous work whenever practical. Dispatch
failure does not restore the old text.

## 36. Input Clearing On Cancel

Cancel clears the binding and any untransferred buffer before task
cancellation or panel dismissal completes.

## 37. Input Clearing On Method Change

Changing method clears the old binding and buffer before installing the new
selection. Text is never carried between passphrase and recovery controls.

## 38. Input Clearing On Disappearance

`onDisappear` or an equivalent owner callback clears local input and cancels
the panel-owned task, closes that request's presentation authorization, and
leaves the public-only shell private-free. If cancellation loses to committed
activation, the host immediately locks the runtime and awaits the private-free
barrier. A disappeared panel cannot receive a late completion and restore input
or private state.

## 39. Input Clearing On Lifecycle Lock

Background, protected-data, explicit, or fatal-containment lock closes unlock
admission, clears the binding, cancels requests, and replaces presentation with
a public-only locked snapshot before rendering resumes. A late committed
activation remains a real runtime outcome, but it cannot reopen presentation;
the host immediately locks it and keeps admission closed until every
presentation owner and the observable adapter acknowledge private-free state.

## 40. Input Clearing On Timeout

Timeout requests cancellation and clears UI-owned input immediately, but it
does not by itself prove that activation failed to commit. The process host
keeps unlock admission closed and awaits the coordinator's authoritative
terminal result. If an injected activation ignores cancellation and commits,
the coordinator's success remains authoritative. The host may publish the
unlocked result only when presentation and lifecycle authorization for that
request are still open. If either authorization has closed, it immediately
locks the committed runtime and awaits the private-free barrier instead.
Otherwise the host may publish an expired locked result only after runtime
status confirms locked/private-free state.

If the UI owner cannot await that terminal result, it delegates cleanup to the
process host, which must execute and await the existing runtime lock plus
private-free observable acknowledgement before exposing a retry-safe state or
releasing admission. Timeout never reveals which operation was in flight.

## 41. Wrong-Secret Behavior

Authentication or unwrap failure maps to one generic non-sensitive failure.
Input remains cleared. No automatic local-key fallback, retry, partial
activation, or private projection is permitted.

## 42. Retry Behavior

Retry is an explicit new request with freshly entered input. The prior request,
buffer, key, and task are not reused. Capability is revalidated before
selection and submission. Retry remains disabled until the process host has
reconciled the prior request to authoritative unlocked state or completed the
locked/private-free barrier and released its admission token.

## 43. Rate-Limit Design Boundary

Rate limiting, delay, lockout, and denial-of-service limits require a later
policy informed by the actual provider and platform. The UI may serialize
submissions but must not invent security claims or leak retry counters.

## 44. Clipboard Policy

The app does not automatically read, copy, clear, or monitor the clipboard.
Explicit user paste may be considered later; system clipboard history and
cross-device behavior remain outside AtlasVault clearing guarantees.

## 45. Keyboard And Autocorrection Policy

Secret fields disable autocorrection and inappropriate capitalization,
suggestion, and content-type behavior where platform APIs permit. No custom
keyboard or extension is trusted to provide memory erasure.

## 46. Screen-Capture And Background Privacy

The panel must obscure or replace secret-bearing content before background
snapshots where supported. Screen-capture detection and blocking policy remain
a platform integration decision; the design does not claim universal capture
prevention.

## 47. Accessibility Behavior

Controls have stable purpose labels, non-sensitive hints, predictable focus,
and generic failures. Assistive-technology testing must verify that secret
values are neither spoken nor retained while preserving usable entry.

## 48. macOS Keyboard Interaction

Return submits only when one available method is selected, input policy is
satisfied, and no request is active. Escape cancels and clears. Key commands
must not bypass capability or duplicate-submission checks.

## 49. iOS Software Keyboard Behavior

The submit label may reflect unlock intent, but submission follows the same
single-use action. Keyboard dismissal, scene loss, and panel dismissal clear
the local binding and do not persist it.

## 50. MainActor Boundary

The future SwiftUI owner and local input state are `@MainActor`. Immutable
non-sensitive capability and presentation values cross from runtime actors.
Secret buffers transfer directly to an actor boundary and are never published.
Process-wide admission and terminal-state reconciliation belong to a
host-owned actor outside each window's `@MainActor` presentation owner.

## 51. Async Task Cancellation

The presentation owner stores one task/request identity. Cancel, disappearance,
method change, lock, timeout, and replacement submission invalidate it.
Generation checks prevent a stale view from restoring input or panel-local
state, but they must not discard an authoritative runtime outcome. The
process host continues terminal reconciliation. A committed activation may
publish unlocked state only while the matching presentation and lifecycle
authorization remain open. Cancel, disappearance, method replacement, explicit
lock, or lifecycle closure revoke that authorization; if any of them loses to a
committed activation, the host immediately locks the runtime and completes the
private-free presentation barrier. Any abandoned or failed attempt must reach
the same locked/private-free barrier before retry is enabled.

## 52. Runtime Facade Interaction

The view never calls the facade. A presentation/controller layer creates the
single-use request and delegates activation through the existing coordinator,
which alone constructs the already-unwrapped runtime activation request.

## 53. Observable Presentation Interaction

The panel renders immutable non-sensitive state from a future observable owner.
Locked, activating, failed, cancelled, key-unavailable, and corrupt states
contain no private projection. Input is separate local state, never part of
`AtlasVaultPresentationSnapshot`.

## 54. No Direct Keychain Call From View

The view does not import Security, reference `SecItem`, instantiate
`AtlasKeychainVaultKeyStore`, or probe credential presence. Local-key action is
an injected command.

## 55. No Direct Crypto Call From View

The view does not derive, wrap, unwrap, decrypt, hash, parse KDF metadata, or
select cryptographic parameters. It transfers one-shot input only to a
reviewed provider boundary.

## 56. No Direct Filesystem Call From View

The view does not resolve Application Support, locate a vault, inspect
metadata, create a directory, or read/write files.

## 57. No Private Compatibility Endpoint Call

The view and owner do not call saved-search, tracker, sidebar-refresh, or any
equivalent plaintext private endpoint. Unlock uses only the AtlasVault
runtime/controller boundary.

## 58. No AtlasRootView Reuse

The unlock panel is attached to the dedicated public-only shell or a new
unwired flow. It does not reuse `AtlasRootView` or instantiate its legacy
private sidebar hierarchy.

## 59. No refreshSidebarData

Neither panel entry nor unlock completion invokes `refreshSidebarData()`.
Future unlocked private state comes from AtlasVault hydration and presentation,
not compatibility refresh.

## 60. Public Search Remains Available

Opening, cancelling, failing, or timing out of unlock leaves injected public
search available unless an independent public-service state disables it.
Public search does not trigger unlock or private refresh.

## 61. Fake Preview Policy

Previews use unmistakably fake, non-secret capability and status values. They
do not instantiate runtime composition, Keychain, providers, requests,
endpoints, or production navigation.

## 62. Future Tests

Future phases must test:

- only available production methods render;
- raw supplied keys never render;
- unsupported selection and submission fail before secret access;
- local-key action delegates without direct Keychain work;
- input clears on submit, cancel, method change, disappearance, lock, and
  timeout;
- wrong secret produces one generic failure and no fallback;
- repeated and concurrent submit are serialized;
- distinct request IDs from separate windows admit only one derivation and
  activation through the process host;
- requests for different vault IDs still admit only one derivation and
  activation through the single process-global runtime;
- an activation that ignores timeout cancellation and commits is reported as
  unlocked only while matching presentation and lifecycle authorization remain
  open, never as retry-safe locked;
- an owner that stops awaiting timeout completes the runtime lock and
  private-free acknowledgement before retry admission reopens;
- cancel, disappearance, method replacement, explicit lock, and lifecycle lock
  that lose to committed activation immediately lock the runtime and keep every
  presentation owner private-free;
- late completion cannot restore input or private presentation;
- accessibility, descriptions, and errors contain no fake secret;
- public search remains usable and private endpoint counters stay zero;
- no `AtlasRootView`, `refreshSidebarData()`, persistence, API, app-entry,
  LocalAuthentication, migration, or cloud coupling exists.

## 63. Go/No-Go Matrix

| Capability | Decision | Prerequisite |
| --- | --- | --- |
| Explicit local-key request through runtime | Ready with constraints | Injected capability, explicit action, generic missing-key result |
| Production passphrase UI | No-go | Reviewed Swift wrapped-key parser and Argon2id/AES-GCM provider |
| Production recovery-key UI | No-go | Reviewed recovery format, vectors, parser, and provider |
| Raw-key production UI | Prohibited | Test-only forever |
| Secure input view-state helper | Design ready | Phase 2D-50 controller and Phase 2D-51 tests |
| Dedicated unwired unlock panel | Design ready | Capability-driven controls and clearing tests |
| Multi-window, multi-vault-ID, or distinct-request unlock | Design/implementation required | One process-global admission token for the single runtime plus terminal reconciliation |
| Locked-shell test-host flow | Design required | Phase 2D-52 endpoint, admission, timeout, and lifecycle tests |
| Production navigation/app entry | Blocked | Phase 2D-53 or later reviewed host-wiring design |
| Automatic unlock | Prohibited | No current planned exception |
| LocalAuthentication | Blocked | Separate threat model and implementation phase |

## 64. Production-Readiness Statement

AtlasVault explicit unlock UI is not implemented or production-ready. Local
Keychain activation is technically present only behind runtime protocols and
still lacks production host/navigation integration and process-wide unlock
admission for the single runtime. Passphrase and recovery unlock are
unavailable without reviewed Swift providers. Memory zeroization, screen
capture, accessibility, file protection, backup, migration, cleanup, cloud,
recovery, onboarding, and key rotation remain unresolved or deferred.

## 65. Recommended Phase 2D-49

Phase 2D-49 should add a non-sensitive capability model, Swift wrapped-key
metadata models, a one-shot key-unwrapping protocol boundary, and shared fake
Python/Swift compatibility vectors. It must not implement Argon2id or add an
unreviewed dependency. In the absence of a reviewed Swift provider,
passphrase and recovery production capabilities remain unavailable.
