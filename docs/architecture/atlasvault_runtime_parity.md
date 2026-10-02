# AtlasVault production runtime and private-record parity

## C29 boundary and evidence status

The intended private-record set is `saved_search`, `saved_job`,
`application_note`, `profile_snippet`, and `draft_metadata`.
`saved_text` remains a version-1 cryptographic reference fixture, not a
product record family. The machine-readable matrix is
[atlasvault_private_record_parity_v1.json](../../contracts/sync/atlasvault_private_record_parity_v1.json).

All five families now have actual source-wired create/read/update/delete UI in
native Apple and Flutter. This is not a claim that every optional field has an
editor, that every provisioned platform journey has passed, or that C32 is done.
Family booleans describe core capability; `recovery_display` means a validated
private projection, not full recovery UX. **Scoped C29 production integration is
verified** for all five matrix entries: native Apple, Flutter Android, Flutter
Windows, Flutter iOS, and Flutter macOS. Their integration flags are true based
on inspected production call chains and completed layered behavioral, interop,
UI/controller, native-adapter, and regression handoffs. The original protected
epoch-owner composition gap and pre-first-rotation publication gap are closed.

This is not certification of every full provisioned-user journey, a complete
enrollment/recovery UI, or release readiness. Shared Dart runtime proof and
platform composition checks are distinguished from the three specific native
storage-adapter runs. **C32 physical-device/VM validation is not complete.**

The current verified evidence is recorded below. The policy result was also
observed directly by this bounded task; the latest wider-suite results are
supplied by the parent and retain that provenance in the matrix. Batch totals
can overlap and must not be summed into a unique-test count.

External evidence is identified by `E-C29-006` (RED/GREEN and runtime) and
`E-C29-007` (native and interoperability), with log basenames below. The parent
maintains sanitized logs and a hash manifest under
`evidence/C29/resume-20260905`. Machine-local temporary paths are not part
of the durable evidence references.

| Evidence | Result | Boundary |
| --- | --- | --- |
| C29 policy/schema | 49 passed | Canonical revision/parent UUID constraint fixed; no remaining parent-revision schema gap. |
| Dart epoch | 101 passed, 0 skipped; static analysis clean | Includes exact Swift-to-Dart payload conformance and 13 actual SIGKILL cases. |
| Dart-to-Swift import | Exact payload pass | Concrete encrypted export consumed by Swift, not merely codec availability. |
| Unchanged Python core | 2 passed | Both encrypted Swift and Dart exports verified with authenticated metadata/history. |
| Final Python security/schema/interop | 134 passed | Current paths and actual encrypted-export interop; overlaps narrower batches. |
| Final Swift affected suite and host build | 301 tests; 0 failures; 1 optional-export skip; host build passed | `E-C29-007`; separate concrete-export runs supply interoperability proof. |
| Late Swift initial-registry/iOS batch | 34 executed: 33 passed, 1 optional-export skip | After new-file formatting; protected original-registry binding and reopened publication verified. |
| Existing iOS E2E regression | 6 passed | Adapted to explicit authenticated binding; not fabricated enrollment. |
| Flutter UI/controller | 138 passed | Latest parent-reported batch, not the earlier focused-app-ui run. |
| Final Dart P3-P7 affected regressions | 158 passed | Separate affected-runtime regression batch. |
| Final Dart P5/P6 sync regressions | 27 passed | Queue, snapshot, convergence, and state-commitment regressions. |
| Native storage adapters | macOS 1 passed; iOS 1 passed; Windows 1 passed | Focused synthetic encrypted-record lifecycle through all three real adapters. |
| Windows follow-up | 123 passed | UI/controller/lifecycle batch after the native storage check; no FocusManager error observed. |
| Pre-first-rotation publication | Verified, gap closed | Protected-binding reopen supplies the authenticated original registry; no registry argument needed on publication. |
| R026 final lifecycle batches | 9 of 10 passed; 1 failed | Failure preserved; remains `OPEN_CONTROLLED`. |

The final Dart log is `E-C29-006`, basename `36-final-101-all.log`.
Its 13 actual SIGKILL
cases comprise 12 CRUD publication points and one protected-binding
pre-bridge case followed by two fresh publications after reopening. The
binding-process case uses synthetic secure-slot adapters, not a claim of native
secure-slot crash coverage. The final Swift host build also passed after the
late original-registry and existing iOS regression checks.

The Windows run used Flutter 3.44.4 and Dart 3.12.2, built the actual native
application in 64 seconds, and removed its temporary NTFS checkout afterward.
Its result log is `E-C29-007`, basename `native-windows-short-encoded.log`. This bounded
documentation update neither copies plaintext evidence nor touches that checkout.

The final bounded source-policy/schema rerun is **49 passed**, including the
current `authenticatedRegistry` and protected original-descriptor call edges.
Its log is `E-C29-006`, path `logs/parent-c29-policy-release.log`. This run validates
the final docs/contract against the parent's formatted, unchanged C29 tests;
the completed behavioral evidence is recorded separately above.

The parent already reran the initial policy RED: **3 passed, 1 failed**, with the
failure in `test_production_compositions_bind_sync_outbox_and_epoch_fences`.
This bounded update does not claim a new initial RED run. The replacement
[policy tests](../../tests/security/test_C29_runtime_parity.py) inspect
function-scoped executable call edges, arguments, and publication order.
Comments and quoted marker strings cannot satisfy those edges. These are
lexical architecture checks, not compilation, cryptographic review, runtime
tests, or proof of atomicity under interruption. A policy/schema pass alone
cannot establish C29 integration; the scoped verification above uses the
completed layered evidence, not merely this policy result.

## Provisioned authority only

Production write authority requires a previously provisioned, authenticated
epoch owner with its actual account, vault, device, registry, epoch, signing
authority, and accepted-history context. Activation reopens that authority.
It must not invent enrollment, initialize a fresh root, or fabricate a registry.
Authenticated legacy import into that existing owner is supported; legacy
ciphertext is not subject to a permanent migration fence.

Both importers decrypt the legacy store with the existing secure vault key and
require an existing authenticated runtime binding. All five families can be
restored with their original ID, revision, parent revision, payload, and
tombstone state. New imported operations are encrypted and signed with the
current owner authority; their P5 outbox and projection share one atomic epoch
publication. The legacy source is read, not rewritten or deleted.

Replay checks retained operations for the **original legacy revision**, not
only the current winning record. Payload, parent, and tombstone must match that
revision exactly before skipping it. This allows a later runtime update/delete
to survive reopening without replaying old values or resurrecting deleted
records. An unknown legacy revision for an already-known ID is rejected with
`migrationRequired`, not reconciled by guessing. See Dart
[`importLegacyRuntime`](../../apps/atlas_flutter/lib/src/atlas_vault/runtime_records.dart)
and Swift
[`importLegacyRuntimeForTesting`](../../apps/apple/Sources/AtlasUI/AtlasVaultEpochRuntime.swift).

`migrationRequired` is the contract for **malformed or mismatched legacy
import**, not a mandatory UI category for every missing or un-enrolled runtime
binding. Generic binding errors must fail closed without fabricated authority
or a fallback that bypasses an existing epoch. Identical UI error categories
are not a protocol-parity requirement.

Native `AtlasVaultActivationEnvironment.runtimeServices` permits genuine
pre-enrollment legacy reads only while the binding is absent **and no epoch
directory exists**. `requirePreEnrollment` is rechecked on reads; mutations are
unavailable, hydrated state is marked `isReadOnly`, and the saved-search UI
visibly labels the records read-only. An existing epoch with missing binding
cannot use that path to bypass owner admission. See the
[activation scope](../../apps/apple/Sources/AtlasUI/AtlasVaultActivationController.swift)
and [visible read-only state](../../apps/apple/Sources/AtlasUI/AtlasVaultSavedSearchView.swift).

Dart `AtlasVaultPrivateStateRuntime.activateExisting` instead refuses activation
when runtime binding authority is absent or rejected. Missing legacy decryption
keys and importer failures map to `migrationRequired`; the session is cleared,
and successful import also verifies the old source did not change. See the
[private runtime](../../apps/atlas_flutter/lib/src/atlas_vault/private_state_runtime.dart).
This stricter pre-enrollment behavior is a documented admission difference,
not a protocol-parity failure. Both production write paths require the same
authenticated-owner authority. This documentation does not alter either path.

## Native Apple call chain

`AtlasMacHost` is separate from the diagnostic `AtlasPreviewApp`.
[AtlasMacAppProcessOwner](../../apps/apple/Sources/AtlasUI/AtlasMacAppProcessOwner.swift)
constructs the production composition once. AppKit active/resign/terminate
events are forwarded; application termination waits for the retained composition
to stop. The existing mac host tests are not full real-device lifecycle proof.

The private-record path is:

1. [Production composition](../../apps/apple/Sources/AtlasUI/AtlasVaultProductionCompositionHarness.swift)
   calls `AtlasVaultRuntimeFactory.production`, installs the runtime facade,
   and connects saved-search and additional-record owners to
   `privateMutationHost.applyPrivateMutation`.
2. [Runtime factory](../../apps/apple/Sources/AtlasUI/AtlasVaultRuntimeComposition.swift)
   supplies the Keychain-backed `runtimeBindingLoader`.
   [Runtime binding](../../apps/apple/Sources/AtlasUI/AtlasVaultRuntimeBinding.swift)
   verifies the stored context against device identity and registry, then
   reopens `AtlasVaultEpochVault` and checks pinned history context/root.
   The open path does not call `initialize` or create enrollment.
3. [Production host](../../apps/apple/Sources/AtlasUI/AtlasVaultProductionHost.swift)
   gates `applyPrivateMutation` and calls the
   [facade](../../apps/apple/Sources/AtlasUI/AtlasVaultRuntimeFacade.swift).
   Its save environment calls `saveRuntimeMutations`; the activated scope
   reopens the binding and calls `commitRuntimeMutations`.
4. [Epoch runtime](../../apps/apple/Sources/AtlasUI/AtlasVaultEpochRuntime.swift)
   calls owner `active`, signs/encrypts the runtime record, stages P5 replica
   ingestion and outbox enqueue into the same component state, and calls
   `publishRuntime` once. Staging adapters do not independently persist the
   projection or outbox. Runtime reads and authenticated page ingress also
   recheck owner admission.

[Production root](../../apps/apple/Sources/AtlasUI/AtlasVaultProductionRootView.swift)
keeps the saved-search CRUD view and exposes a private-record sheet.
[AtlasVaultRecordsView](../../apps/apple/Sources/AtlasUI/AtlasVaultRecordsView.swift)
provides the other four families' list, create/edit form, and confirmed delete.
Both owners participate in the combined private-session lifecycle boundary.
Legacy API-backed `SearchScreen` controls are not evidence for this path.

## Flutter call chain

[Default app assembly](../../apps/atlas_flutter/lib/features/app_shell/atlas_app.dart)
injects an `epochSessionFactory` on Android, Windows, iOS, and macOS. Each
uses the appropriate protected key/local-store adapter and disables the
persistent plaintext cache. `_openProductionEpochSession` obtains Application
Support storage and delegates to `AtlasVaultRuntimeBinding.open`; it need not
construct `AtlasVaultEpochVault` directly in the app shell.

[Runtime binding/session](../../apps/atlas_flutter/lib/src/atlas_vault/runtime_binding.dart)
loads the protected binding, storage key, and signing seed, verifies the vault
and registry binding and active signer, and reopens the existing epoch owner.
Explicit provisioning is a separate operation for an already initialized,
authenticated owner. There is no implicit enrollment on the open path.

[Private runtime](../../apps/atlas_flutter/lib/src/atlas_vault/private_state_runtime.dart)
selects the epoch branch before legacy persistence. CRUD methods enqueue epoch
mutations and call the retained session's `commit`, which supplies the protected
signer to `commitRuntimeRecord`.
[Runtime records](../../apps/atlas_flutter/lib/src/atlas_vault/runtime_records.dart)
check admission, encrypt/sign, stage the convergent replica and P5 outbox
against the same state, then publish through one encrypted epoch-file write.
The UI does not call the low-level queue directly.

[AtlasPrivateRecordsPanel](../../apps/atlas_flutter/lib/features/app_shell/atlas_private_records_panel.dart)
renders all five families with read/create/edit/confirmed-delete states. The
saved panel passes committed records, mutation admission, and controller CRUD
callbacks; the controller refreshes the private snapshot after a current
mutation completes. This is no longer just the two legacy search/tracker lists.

Both languages retain the owner guards for `ACTIVATION_PENDING`,
`CATCH_UP_PENDING`, `RECOVERY_PENDING`, `CLEANUP_PENDING`, and revoked
authority. State names belong in owner admission, not artificially in each
platform app entry point. Tests must prove already-unlocked sessions also
respect a later fence.

## Pre-first-rotation publication

The parent's RED control used actual P6 admission to accept the original signed
view and its successor while the previous runtime publication path rejected a
nil rotation journal. The final Dart and Swift handoffs verify the minimal fix,
closing that epoch-owner publication gap without weakening admission or
fabricating an original registry.

`runtimePublication` accepts optional `authenticatedRegistry` in both languages.
Before the P6-to-P7 transition, the protected runtime binding persists only
explicitly supplied original P6 descriptor rows and validates their root against
the last signed view **before provisioning keys**. Reopening supplies that
authenticated original context; descriptor hashes are never reconstructed from
device keys. After the transition, publication continues to use the current
authenticated P7 registry. An existing non-ACTIVE journal remains fenced.

See Dart [`_publicationRegistry` and `_createCommitment`](../../apps/atlas_flutter/lib/src/atlas_vault/epoch_vault.dart),
Swift [`stageCommitment`](../../apps/apple/Sources/AtlasUI/AtlasVaultEpochVault.swift),
and their protected binding paths linked above. The policy checks follow these
argument and validation edges, including validation before key writes. Their
trailing-call-comma tolerance does not omit arguments, nested expressions,
receiver identity, or publication order.

The completed runtime suites confirm original-view/successor admission,
missing or mismatched descriptor rejection without provisioning side effects,
protected-binding reopening, and unchanged post-transition behavior. Swift
persists and root-checks `authenticatedHistoryRegistry` before the transition;
the reopened owner publishes without an explicit registry argument. Dart's
final process control additionally survives SIGKILL and publishes twice from
fresh reopened owners. These behavioral results close the C29 gate; they do
not broaden the claim to every full journey or C32.

## Runtime record schema

[atlasvault_runtime_record_v1.schema.json](../../contracts/sync/atlasvault_runtime_record_v1.schema.json)
describes the decrypted client-side wrapper inside existing signed epoch
ciphertext. It is not a server-visible request or a new plaintext persistence
format. It mirrors operation/author/sequence/lamport/object/revision/parent and
tombstone metadata. Live records contain an existing typed payload envelope;
tombstones contain `payload: null`. Runtime tombstones therefore encrypt an
authenticated wrapper, not the empty plaintext used by legacy record storage.

The policy suite validates Draft 2020-12 schema structure and exercises all
five existing
[synthetic payload vectors](../../contracts/sync/test_vectors/atlasvault_payload_vectors_v1.json)
in memory for create/update/tombstone shapes. Rejection cases cover required
fields, extra fields, UUIDs, counters, types, and tombstone/payload disagreement.
No new payload/evidence files or decrypted diagnostics are written.

The parent resolved the invalid-parent schema case: `revision` and non-null
`parent_revision` now require canonical lowercase UUID-shaped strings. The
49-test policy/schema suite passes, including the formerly RED invalid-parent
case. This is a resolved constraint gap, not a pending native gate.

The schema only validates wrapper structure. Runtime typed-payload validation,
metadata equality to authenticated outer fields, signer binding, P6 admission,
and interruption semantics require behavioral tests. The schema's signed
64-bit counter ceiling is not a claim that every implementation accepts the
same upper bound; native mutation generation currently uses a smaller
JavaScript-safe ceiling.

## Evidence paths

These paths identify the implemented checks behind the completed evidence
summary. The summary's provenance and scope apply; file existence alone never
supplies a pass:

- [Native runtime integration](../../apps/apple/Tests/AtlasUITests/AtlasVaultRuntimeIntegrationTests.swift):
  production-host actions, all families, protected bindings, publication,
  interruption, lineage, authenticated ingress, and pending-state fences.
- [Native mac host](../../apps/apple/Tests/AtlasUITests/AtlasMacProductionHostTests.swift)
  and [saved-search UI owner](../../apps/apple/Tests/AtlasUITests/AtlasVaultSavedSearchViewTests.swift):
  host ownership and existing private-presentation regressions.
- [Existing iOS E2E](../../apps/apple/Tests/AtlasUITests/AtlasIOSPrivateSavedSearchEndToEndTests.swift):
  six existing journey regressions adapted to explicit authenticated binding;
  part of the completed late native handoff, not an implicit bootstrap path.
- [Dart runtime epoch](../../apps/atlas_flutter/test/atlas_vault_runtime_epoch_test.dart):
  CRUD/reopen/outbox, provisioning, subprocess interruption, fences, and signed
  page admission; uses synthetic fixtures and explicitly injected native-channel
  harnesses, not full physical-device proof.
- [Cross-language Python checks](../../tests/security/test_C29_runtime_interop.py):
  the unchanged core consumes both concrete encrypted exports. Swift
  `testImportDartRuntimePageForCrossLanguageConformance` and Dart
  `runtimeInteropTests` additionally assert exact payload preservation, not just
  five-family counts. Exports remain synthetic ciphertext outside the repository.
- [Flutter record UI](../../apps/atlas_flutter/test/atlas_private_records_panel_test.dart):
  all-family CRUD, optional-field preservation, stale actions, fence changes,
  failures, disposal, and compact layout.
- [Flutter controller lifecycle](../../apps/atlas_flutter/test/atlas_vault_runtime_controller_lifecycle_test.dart):
  lock/dispose, overlapping activation and mutation, pending reads, and awaitable
  shutdown ownership.
- [Windows private runtime](../../apps/atlas_flutter/test/atlas_vault_windows_private_state_runtime_test.dart),
  [Android integration](../../apps/atlas_flutter/integration_test/atlas_vault_android_private_state_integration_test.dart),
  [Windows integration](../../apps/atlas_flutter/integration_test/atlas_vault_windows_private_state_integration_test.dart),
  and [shared native storage integration](../../apps/atlas_flutter/integration_test/atlas_vault_runtime_storage_integration_test.dart):
  platform adapter coverage to correlate with the new provisioned owner path,
  not an automatic transfer of old legacy-store results to C29.
  The renamed shared test selects real Apple or Windows adapters and checks
  synthetic encrypted-record create/read/decrypt/replace plus key/store deletion
  and repeated deletion. The reported macOS/iOS/Windows native storage passes
  apply only to that focused test; the Swift registry/iOS E2E regression results
  provide separate evidence for their own scope.

Run policy/schema checks from the repository root with pytest and jsonschema:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider tests/security/test_C29_runtime_parity.py -q
```

Native focused commands run from `apps/apple`:

```bash
swift test --filter AtlasVaultRuntimeIntegrationTests
swift test --filter AtlasMacProductionHostTests
swift test --filter AtlasVaultSavedSearchViewTests
swift test --filter AtlasIOSPrivateSavedSearchEndToEndTests
```

Flutter focused commands run from `apps/atlas_flutter`:

```bash
flutter test --concurrency=1 test/atlas_vault_runtime_epoch_test.dart --exclude-tags runtime-process
flutter test --concurrency=1 test/atlas_vault_runtime_epoch_test.dart --tags runtime-process
flutter test --concurrency=1 test/atlas_vault_runtime_controller_lifecycle_test.dart test/atlas_private_records_panel_test.dart test/atlas_vault_windows_private_state_runtime_test.dart
flutter test test/atlas_vault_lifecycle_residuals_test.dart
```

The saved-search owner cancellation/drain cases remain relevant to controlled
native lifecycle residual R026. Its final batches were **9 passed, 1 failed**;
the failed batch is retained and R026 remains `OPEN_CONTROLLED`. Passing focused
tests or later batches do not erase it. The lifecycle-residual source guard and new
controller lifecycle suite remain relevant to Windows R029; neither proves
the Windows native recovery process journey passed. On Windows, run the
existing integration journey separately:

```bash
flutter test -d windows integration_test/atlas_vault_windows_interoperability_recovery_test.dart
```

The focused storage-adapter test runs with the selected native target, for
example from `apps/atlas_flutter`:

```bash
flutter test -d macos integration_test/atlas_vault_runtime_storage_integration_test.dart
flutter test -d windows integration_test/atlas_vault_runtime_storage_integration_test.dart
```

## Security limits retained

Full device-management, pairing, recovery, conflict, and revocation UX remains
C30-C31. Full physical-device and VM validation remains C32 and is not proved
by these source, synthetic, or focused native-adapter tests. R024's single-instance
backend limitation, R026's preserved failed batch and `OPEN_CONTROLLED` status,
R029 lifecycle risk, and release-blocking external review of the Dart RFC 9180
composition remain outside this completion claim. No new protocol or C30-C32
work is introduced by this evidence update.
