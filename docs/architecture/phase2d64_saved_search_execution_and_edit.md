# Phase 2D-64: Saved-Search Execution and Edit

## 1. Purpose

Phase 2D-64 completes the currently planned Apple saved-search surface with
encrypted edit and rename plus an explicit lock-before-public-search handoff.

## 2. Scope

The phase is restricted to the exact 17-file allowlist. It changes only the
Apple saved-search feature, its private view, narrow production host and
composition boundaries, the existing public adapter and locked shell, focused
tests, and this architecture record.

It does not change Python, contracts, vectors, package manifests, Xcode
projects, app entry, process ownership, navigation, the runtime facade, record
crypto, the record saver, the hydrator, payload models, public snapshots,
recovery flows, private data, cloud behavior, or migration behavior.

## 3. Final Apple Saved-Search Closure

After this phase, Apple saved searches support list, create, delete, edit,
rename, and explicit execution through a lock-first public handoff. Another
Apple-only saved-search phase is not planned. Advanced filter editing,
scheduling, alerts, notifications, and other private record families remain
deferred.

## 4. Phase 2D-63 Baseline

Git and GitHub reconstruction identified Phase 2D-63 as merged through PR #84.
Its reviewed head was `2c8c7754e5a257b709489c96e85949fffe8ebed8`,
its squash merge was `beac8d4f1b8e6543ad222cad1cdbd4700f994b18`,
and its reviewed and merged trees were identical. The exact 14-file scope,
Python and GitGuardian checks, Codex review, one final Copilot review, and all
review threads were clean. The independent JobAgg date-stability follow-up
merged separately through PR #85 and is not part of this Apple phase.

## 5. Checkpoint A Boundary

Checkpoint A added only encrypted saved-search edit and rename. Its red commit
preceded its implementation commit, and its focused, full Swift, and iOS build
gates were green before Checkpoint B began. It did not add public request
expansion, public filter forwarding, lock-before-search behavior, origin
indicators, or Run and Lock UI.

## 6. Checkpoint B Boundary

Checkpoint B adds strict public request preparation, a generation-bound owner
claim, a harness-owned handoff coordinator, a dedicated host handoff protocol,
the full-drain lock-before-network barrier, public criteria forwarding, a fixed
origin indicator, manual reset behavior, and explicit Run and Lock UI.

## 7. Existing Encrypted Update Primitive

Edit uses the existing `AtlasVaultUpdateMutation` and host-owned private
mutation path. The reviewed record saver encrypts the replacement payload,
creates a new revision, records the old revision as its parent, atomically
commits the local store, reloads it, and rehydrates runtime private state.

## 8. Editable Fields

The edit draft contains only saved-search name and search text. Both use the
existing strict normalization and validation rules.

## 9. Preserved Fields

Edit preserves record ID, key ID, payload creation time, envelope
client-created time, description, status, every filter array, closing date,
low-confidence and facet settings, limit, and sort.

## 10. Revision Lineage

The update mutation supplies the current record ID, revision, and key ID. A
committed refresh must contain the same record ID, a new revision, and the old
revision as parent.

## 11. No-Op Edit

When normalized name and search text are unchanged, the coordinator creates no
timestamp, host mutation, or encrypted record. It returns the current committed
snapshot and preserves the current revision.

## 12. Runtime Refresh After Edit

The UI is not updated optimistically. After commit, the coordinator rereads the
already hydrated runtime state, projects the current generation, verifies
identity, lineage, timestamps, key ID, edited values, and preserved filters,
then replaces its internal metadata and publishes the committed snapshot.

## 13. Opaque-ID Stability

An update preserves the saved search's opaque presentation ID within the
current unlock generation because record identity is unchanged. Lock and a
subsequent unlock create a new generation, so prior IDs become invalid.

## 14. Run-and-Lock Decision

Saved-search execution is an explicit private-to-public declassification. The
user confirms `Run & Lock`; the app never executes automatically and never
sends saved criteria while the vault remains unlocked.

## 15. Declassification Boundary

The private coordinator resolves the current opaque ID and validates a public
request before presentation clearing. The owner then completes a current
generation claim and synchronously hides all private presentation. A separate
harness-owned coordinator invokes the host handoff.

## 16. Allowed Public Criteria

The handoff may transfer text, open status, organizations, source IDs, cities,
countries, national or international values, grade codes, CCOG families,
capability tags, contract groups, seniority groups, work modalities, volunteer
kinds, UNV categories, UNV volunteer types, closing-date upper bound, sort,
and a bounded page limit.

## 17. Excluded Private Metadata

The handoff excludes name, summary, description, record ID, revision, parent
revision, key ID, opaque presentation ID, vault ID, timestamps, tombstone
state, encrypted envelopes, and local paths. Descriptions and errors are fixed
and redacted.

## 18. Public Request Model

`AtlasPublicJobSearchRequest` carries a fixed origin, a Boolean indicating
additional criteria, and an internal validated API request. The internal
request is module-private and is absent from descriptions, the shell, the
public snapshot, and errors.

## 19. Manual Compatibility

The historical query, limit, and offset initializer remains source- and
behavior-compatible. It creates a text-only request with manual origin,
facets disabled, and no additional criteria. The ordinary public-search host
entry point accepts only manual-origin requests. A saved-origin request is
rejected before service access so it cannot bypass the dedicated lock-first
handoff boundary. While the retained saved-search handoff owns the host, the
ordinary entry point also rejects manual searches. This keeps network
admission exclusive through the post-lock proof and saved-search request
completion. Before private request preparation crosses its actor boundary, the
harness-owned coordinator obtains an opaque host reservation. The host stores
that reservation synchronously, so manual and private-mutation admission remain
closed throughout private criteria resolution. Preparation or owner-claim
failure cancels the exact reservation without hiding the committed private
list. After owner completion clears the list, the same reservation is consumed
to create the retained handoff operation before its first runtime-status await,
closing actor-reentrancy admission from preparation through completion. The
retained operation also keeps the public unlock affordance false and rejects
direct unlock-panel or submission requests while the saved-origin service
request is in flight. Ordinary manual and unlock behavior resume only after
reservation cancellation or handoff ownership clears.

## 20. Saved-Search Validation

Saved requests require exactly open status, low-confidence exclusion,
`closing_date_asc`, positive stored limit, nonnegative stored offset, and a
configured maximum from 1 through 200. Text and every filter value are bounded
and control-free; filter values are trimmed, nonempty, and unique per
dimension. Closing dates require an exact valid Gregorian `YYYY-MM-DD`.
Canonical handoff resets offset to zero, disables facets, and caps the limit.
Unsupported criteria fail before private clearing, runtime lock, or network.

## 21. Public Adapter Forwarding

`AtlasAPIClientPublicJobAdapter` forwards the request's validated internal
`AtlasSearchRequest`. Manual requests retain their historical text-only
semantics, while saved handoffs preserve every supported validated criterion.
Projection, provenance, bounded result validation, and fixed errors remain
shared.

## 22. Owner Handoff Claim

The owner creates a redacted claim containing only its generation and a random
operation identifier. Preparation failure preserves the committed list and
publishes a fixed failure. Completion is accepted only for the current claim,
advances the generation, cancels private work, clears items synchronously, and
publishes hidden.

## 23. Synchronous Private Hide

The owner completes its claim before the host handoff begins. Items are empty
and the generation is invalidated synchronously, before any barrier await.
Lock, stop, hide, reactivation, or a stale completion invalidates the claim.

## 24. Separate Harness-Owned Handoff Operation

The host reservation and retained handoff operation belong to the
harness-owned handoff coordinator, not to the private presentation owner or
private-session coordinator. The coordinator reserves host admission before
private request preparation, cancels the exact reservation on preparation or
claim failure, and consumes it only after synchronous owner hide. Caller
cancellation cannot orphan either stage, duplicate handoffs are rejected, and
harness stop cancels and drains reservation and execution work.

## 25. Full-Drain Barrier Contract

The concrete host implements a narrow saved-search handoff protocol. After
strict admission it runs the ordinary private-free barrier with private-session
drain enabled. The barrier hides presentation, cancels and drains private
mutation work, stops and drains the private coordinator, locks the runtime, and
publishes an acknowledged locked-public state. The non-draining committed-save
containment path is not used. Barrier completion does not reopen unlock
admission while the retained saved-search handoff remains active. Successful
or public-search-failed completion clears handoff ownership before using the
ordinary acknowledged publication path to reopen unlock admission.
An explicit lock invalidates any unconsumed reservation and synchronously
cancels a retained handoff before it starts or joins the full barrier. If the
handoff already owns that barrier, explicit lock still awaits the shared full
drain, but the cancelled handoff cannot proceed from barrier completion into
public service access.

## 26. Lock-Failure Contract

The host calls no public service unless runtime locked status, inactive private
session, completed drain, hidden unlock panel, locked-public presentation,
current operation identity, lifecycle safety, and public-operation admission
are all proven. Failure returns a fixed lock, cancellation, or stop outcome.
The private owner remains hidden and is not automatically restored or
unlocked.

## 27. Terminal Supersession

Terminal stop supersedes a nonterminal handoff barrier, retains full
private-session drain behavior, cancels public work, and produces no new public
request. A terminal barrier never inherits the non-draining containment flag.
When termination arrives through the lifecycle coordinator, the host enters
terminal-stop ownership and synchronously cancels the retained handoff and
retained public-search tasks before awaiting lifecycle handling. This prevents
a lifecycle-owned runtime lock from suspending while network work remains
uncancelled. After lifecycle handling returns, the existing terminal operation
drains those exact tasks and completes the full terminal barrier.

## 28. Public-Search Failure After Lock

If the public service fails after authoritative lock, the app remains locked,
the owner remains hidden, and the locked shell publishes its existing fixed
unavailable state with saved-search origin. No automatic retry or unlock
occurs.

## 29. Saved-Search Origin Indicator

The locked shell stores only fixed origin and additional-criteria values. A
saved handoff displays `Saved search criteria applied`; it never displays the
saved-search name, filter arrays, vault identity, or record metadata.

## 30. Manual-Search Reset

A later manual search creates a fresh historical text-only request, publishes
manual origin, clears the additional-criteria Boolean, and retains no prior
saved filters. Public snapshot restoration also starts in manual state.

## 31. Edit Sheet

`Edit` is explicit. The sheet is titled `Edit Saved Search`, prepopulates only
the UI-safe name and search text, and offers explicit Cancel and Save actions.
It implements no advanced filter editing.

## 32. Run Confirmation

`Run Search` opens `Run Saved Search and Lock Vault?`. The message states that
the vault locks first and only then are saved criteria sent to the configured
public job service. The only committing action is `Run & Lock`.

## 33. Local Field Clearing

Create and edit fields and run/delete candidates are scene-local state. Fields
clear before mutation awaits and on cancel, lock, owner hide, and view
disappearance. The observable owner stores no editable private draft.

## 34. Local-Key Journey

The deterministic end-to-end route creates and locally unlocks a vault, creates
a search, updates it through encrypted mutation, relaunches, and executes a
full-filter lock-first handoff.

## 35. Recovery-Key Journey

The recovery route removes the local Keychain key, unlocks with the saved
recovery key, edits the encrypted saved search, and executes the same lock-first
handoff. The provider remains session-only and does not recreate the local key.

## 36. Public-Cache Immutability

Saved-search private values are never written to `AtlasPublicLocalSnapshot` or
the compatibility cache. End-to-end tests compare public snapshot bytes across
private mutations.

## 37. Plaintext Persistence Exclusion

Create and update tests inspect canonical encrypted local-store bytes and
require both old and new names, queries, and summaries to be absent. Delete
continues to use the encrypted tombstone path.

## 38. Real Simulator Smoke Evidence

The loop found an available iOS 26.5 iPhone 17 Pro simulator and booted it.
Xcode build settings resolved the product as `AtlasIOSHost.app` with bundle
identifier `un-applications.AtlasIOSHost`. The exact-device build succeeded,
the prior app was uninstalled, and the new product was installed and launched
with only `ATLAS_API_BASE_URL=http://127.0.0.1:8765`. No
`ATLAS_REFERENCE_CAPTURE` or auto-action variable was present.

The launch returned PID 85889. Process checks at 22 and 52 seconds both found
the process alive, and the screenshot showed the normal locked AtlasVault
public-search route with no immediate crash. Evidence is stored under the
persistent Phase 2D-64 checkpoint. No tap-level edit or Run and Lock claim is
made because no existing supported UI automation performed those actions. The
loop shut down the simulator after capture.

## 39. Multi-Window Authority

All production roots share one saved-search owner, private coordinator, and
harness-owned handoff coordinator. Scene-local sheets remain independent, but
process-global operation admission prevents duplicate mutation or handoff.

## 40. Existing Create/Export/Import Coexistence

The existing local creation, recovery export, recovery import, unlock, public
search, and explicit lock contexts remain assembled by the same harness. The
root and app entry are unchanged.

## 41. Error Redaction

Draft, preparation, lock, cancellation, stop, and public-service failures map
to fixed values. No error or description includes saved criteria, private
metadata, vault identity, local path, raw request, or service detail.

## 42. Merge-Stable Tests

Phase tests do not pin the current branch, HEAD, blob hashes, app-entry source,
or a hardcoded Git commit. Repository scope is enforced externally against the
17-file allowlist.

## 43. TDD Checkpoint A Evidence

Checkpoint A red commit `c1e9e2bc8ceaa49345bd016d0f0b733e9347d0ef`
proved edit APIs, encrypted update behavior, owner action, edit sheet, no-op
handling, and identity verification were absent. Implementation commit
`1dcd76bdebb6b89d9131630ea74a4144b51634d5` made feature, view,
end-to-end, harness, full Swift, and both generic iOS builds green.

## 44. TDD Checkpoint B Evidence

Checkpoint B red commit `5cf8e44f9d1164e8bd0b30ebb22b48b405449904`
proved strict saved request preparation, filter forwarding, owner claim,
harness coordinator, dedicated host handoff, full-drain ordering, lock-failure
semantics, Run and Lock UI, origin indication, and manual reset were absent.
Deterministic tests gate private drain and runtime lock and require a zero
public-service call count until both complete.

The first exact-head Codex review identified that the ordinary public-search
entry point also needed an explicit manual-origin gate. Its deterministic red
test observed one public-service call while the private session remained
unlocked. The correction rejects saved-origin requests before service access
while preserving the internal shared helper used after the dedicated handoff
barrier.

The second exact-head Codex review identified a cross-window admission race:
after locked-public publication but before the handoff's post-lock proof, an
ordinary manual search could enter and consume the host's public-search
operation. A deterministic status gate reproduced a successful manual service
call, a displaced saved request, and a failed handoff. The correction rejects
manual searches while the retained saved-search handoff operation owns the
host. The regression requires zero service calls from the manual attempt and
then proves the saved request alone completes after the gate releases.

The third exact-head Codex review identified the remaining initial-admission
window: the host awaited runtime status before storing the handoff operation,
allowing actor reentrancy to admit a manual search. A deterministic initial
runtime-status gate reproduced the premature manual call and displaced saved
request. The correction creates and stores the retained handoff task before
the first await, then revalidates operation identity, generation, lifecycle
admission, and unlocked runtime status inside that task. The regression
requires zero service calls during the initial proof and permits only the
saved request after release.

The fourth exact-head Codex review identified that the completed lock barrier
reopened ordinary unlock admission while the saved-origin service request was
still in flight. A deterministic service gate reproduced a visible unlock
affordance, an admitted unlock panel, and cancellation of the saved handoff.
The correction includes retained handoff ownership in every barrier,
lifecycle, transient, and direct unlock admission gate. The handoff clears its
ownership and republishes ordinary unlock availability only after saved-search
completion or a post-lock public-service failure. The regression requires
locked-public mode and closed direct unlock admission while the service is
gated, then requires saved completion and reopened admission after release.

The fifth exact-head Codex review identified the remaining harness preparation
window: the owner claim was established before the private coordinator actor
resolved the saved request, but the production host was not reserved until
after that suspension. A manual search from another window could therefore
start while the vault remained unlocked and displace the later handoff after
the owner cleared. The correction adds an opaque host reservation acquired
before request preparation, blocks manual and private admission while held,
cancels it on preparation or claim failure, and atomically consumes it into
the retained handoff after owner hide. Deterministic regressions require a
manual attempt during reservation to make zero service calls, prove the saved
request alone completes, and prove cancellation reopens ordinary manual
search without locking.

The sixth exact-head Codex review identified two retained-operation edges.
First, reservation consumption removed the reservation fence before the
retained handoff completed, so private-mutation admission also requires no
retained handoff. Second, an ordinary successful lock could supersede the
handoff while its initial runtime proof was suspended; the resulting cancelled
handoff cleared ownership after the barrier had published unlock admission
closed. Cancellation now reopens ordinary admission only after revalidating
that the host is started, the private session is inactive, locked-public state
is current, and the runtime authoritatively reports locked. Lock failure,
reconciliation, unsafe lifecycle, and terminal state do not reopen admission.

## 45. Full Verification

Before the Checkpoint B implementation commit, the feature suite passed 22
tests, the combined view/host/harness/adapter/shell/root/end-to-end matrix
passed 319 tests, the Phase 2D-60 through 2D-63 and supporting runtime matrix
passed 278 tests, and full Swift passed 1,281 tests. Both generic iOS builds
and the exact-device simulator build passed.

The complete Python CI mirror passed JobAgg with 272 passed and one skipped,
VaultSync with 236 passed, and repository root tests with eight passed and one
skipped. The runtime JSON schema and 71 YAML files also validated. Source
guards were clean, and the transient Python environment was removed. GitHub
checks, exact-head reviews, thread resolution, final scope, protected-path
cleanliness, and artifact scans remain merge-time gates.

After the first Codex review correction, the focused Phase 2D-64 matrix passed
342 tests and full Swift passed 1,282 tests. The complete Python mirror,
both generic iOS builds, and the exact-device normal-route smoke were repeated
successfully. The ordinary-entry regression specifically verifies zero public
calls, zero runtime-lock calls, and an unchanged unlocked private session when
a saved-origin request is presented outside the dedicated handoff.

After the second Codex review correction, the production-host suite passed
127 tests, the combined Checkpoint A/B matrix passed 343 tests, the explicit
earlier-phase and supporting regression matrix passed 237 tests, and full
Swift passed 1,283 tests. The complete Python mirror, both generic iOS builds,
and the exact-device normal-route smoke were repeated successfully. The
handoff-ownership regression specifically holds post-lock runtime proof,
requires a concurrent manual attempt to make zero service calls, then proves
the saved request alone completes after release.

After the third Codex review correction, the production-host suite passed
128 tests, the combined Checkpoint A/B matrix passed 344 tests, the explicit
earlier-phase and supporting regression matrix passed 237 tests, and full
Swift passed 1,284 tests. The complete Python mirror, both generic iOS builds,
and the exact-device normal-route smoke were repeated successfully. The
initial-admission regression specifically holds the first runtime-status
proof, requires a concurrent manual attempt to make zero service calls, then
proves the reserved saved request alone completes after release.

After the fourth Codex review correction, the production-host suite passed
129 tests, the combined Checkpoint A/B matrix passed 345 tests, the explicit
earlier-phase and supporting regression matrix passed 237 tests, and full
Swift passed 1,285 tests. The complete Python mirror passed again, both generic
iOS builds succeeded, and an iOS 26.5 iPhone 17 Pro normal-route smoke kept the
AtlasIOSHost process alive for 42 seconds without an immediate crash. No
tap-level edit or handoff automation was claimed. The unlock-admission
regression holds the saved-origin service request, requires locked-public mode
and closed direct unlock admission, then proves normal unlock admission
reopens only after the saved request completes.

The fifth Codex review correction reserves the production host before private
request preparation. Its focused regressions prove manual service access is
blocked while the reservation is held, the reserved saved request remains the
only request admitted after owner hide, and cancellation of an unconsumed
reservation restores ordinary manual admission without a lock or saved-origin
request. Final suite, build, simulator, and GitHub evidence is recorded in the
persistent Phase 2D-64 archive.

The sixth Codex review regressions hold the retained handoff on its initial
runtime proof. One requires a concurrent private mutation to fail with zero
runtime mutation calls while the saved handoff completes. The other completes
an ordinary full-drain lock, releases the now-stale handoff, requires fixed
cancelled outcome and zero public calls, then proves ordinary unlock admission
reopens only from authoritatively locked state.

The single final Copilot review identified a terminal ownership gap: terminal
host stop could complete while a retained saved-search handoff remained
suspended in cancellation-ignoring dependency work. Terminal intent now
cancels the retained handoff synchronously. The stop operation retains that
exact task, completes the ordinary terminal full-drain barrier, and then drains
the handoff before returning stopped state. Search operations are still
cancelled before any drain await, so a handoff already inside public search
cannot create a circular wait. Ordinary nonterminal lock and committed-failure
containment keep their established behavior. A deterministic first-event
regression proves handoff cancellation precedes stop completion and that no
public request occurs.

The subsequent exact-head Codex review identified the lifecycle-entry variant
of that terminal gap. `.willTerminate` previously awaited the lifecycle
coordinator before `stop()` established terminal ownership, so a
lifecycle-owned runtime lock could suspend while an already-running saved
handoff network operation remained uncancelled. The correction enters terminal
ownership, releases suspended selection callers, and synchronously cancels the
retained handoff and search tasks before awaiting lifecycle handling. A
deterministic regression holds both the public service and lifecycle handler,
requires service cancellation while lifecycle handling is still suspended,
then proves the handoff returns stopped and no late public result is published.
After this correction, the production-host suite passed 135 tests, the focused
Phase 2D-64 matrix passed 351 tests, and full Swift passed 1,291 tests. The
complete Python CI mirror, both generic iOS builds, and an exact-device iOS
26.5 iPhone 17 Pro normal-route smoke also passed. The installed app remained
alive for 29 seconds without an immediate crash; no tap-level edit or handoff
automation was performed or claimed.

The next exact-head Codex review identified an explicit-lock supersession edge:
when the handoff already owned the full barrier and private-session drain was
suspended, `lock()` joined that barrier without cancelling the handoff. The
handoff could therefore continue to public service access after the explicit
lock completed. Explicit lock now invalidates any reservation and synchronously
cancels the retained handoff before joining or starting the barrier. The
deterministic regression suspends private-session drain, requests explicit
lock, releases the barrier, and requires a cancelled handoff, authoritative
locked runtime, and zero public-service calls. The complete production-host
suite passed 136 tests after this correction. The focused Phase 2D-64 matrix
passed 352 tests, full Swift passed 1,292 tests, the complete Python CI mirror
passed, and both generic iOS builds passed. An exact-device iOS 26.5 iPhone 17
Pro normal-route smoke kept the freshly installed AtlasIOSHost process alive
for 57 seconds without an immediate crash. No tap-level edit or handoff
automation was performed or claimed.

## 46. Go/No-Go

- Apple saved-search create/list/delete: implemented previously.
- Apple saved-search edit/rename: implemented.
- Encrypted update revision: implemented.
- Apple saved-search execution: implemented.
- Full-drain lock-before-search: implemented.
- Lock-failure no-network behavior: implemented.
- Full supported filter handoff: implemented.
- Public saved-origin indicator: implemented.
- Manual-search reset: implemented.
- Apple saved-search feature closure: complete for the current roadmap.
- Advanced filter editing: deferred.
- Scheduled execution and notifications: deferred.
- Saved jobs and other Apple private families: not the default next work.
- Flutter AtlasVault parity: next priority after master reconstruction.
- Cloud and migration: not implemented in this Apple phase.
- Production readiness: not claimed.

## 47. Apple Saved-Search Closure Declaration

This phase is the final planned Apple-only saved-search phase for the current
roadmap. Future Apple changes are limited to cross-platform compatibility,
defects, and cryptographic or privacy corrections discovered during parity
work.

## 48. Post-Merge Flutter Parity Reconstruction

After merge, the loop performs a read-only audit of Dart AtlasVault vectors,
record crypto, recovery wrapping, encrypted export/import, Android and Windows
secure-key storage, plaintext saved-search and tracker authorities, and
bidirectional iOS-Flutter encrypted exchange. It creates no branch or files.

## 49. Deferred Work

The next priority is Flutter AtlasVault parity in dependency order: Dart
compatibility vectors; Android Keystore and Windows secure-key boundaries;
removal or explicitly reviewed migration of plaintext saved-search and tracker
storage; then bidirectional encrypted iOS-Flutter export/import. Another Apple
private-record family is not the default next phase.
