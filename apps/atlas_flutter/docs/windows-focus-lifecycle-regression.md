# Windows post-test focus regression

The Windows persistence job [116232491885](https://github.com/yutsukioka/un_job_application_helper/actions/runs/38676912332/job/116232491885)
failed after the migration-finalization-waiter's persistence assertions passed:
`A FocusManager was used after being disposed`, from
`FocusManager.applyFocusChangesIfNeeded`. This is separate from the earlier
Windows temporary-directory file-lock failure.

Flutter 3.44.4 mounts its final `View` after the test callback returns, then runs
user teardowns before `binding.postTest` disposes the focus manager.
`registerAtlasViewCleanup` therefore unmounts the view in a user teardown and
pumps pending focus work while the manager is alive. It is registered before
the existing resource teardown, so resources close first under reverse teardown
ordering. The migration stages, persistence assertions, process coordination,
and `leaveForFreshProcess` behavior are unchanged.

## Deterministic reproduction

The shared regression parks focus on the root in an asynchronous user teardown
and pumps that setup work. Its suite teardown then delivers a focused
`ViewFocusEvent` through `handleViewFocusChanged` **after** the framework's actual
`postTest`. Before injection it asserts that the body ran, `inTest` is false,
and the focus manager has been replaced. It injects exactly one event and drains
the queued work before the integration binding's final reporter. It neither
catches asynchronous exceptions nor replaces error handlers. Focus preparation
must not occur inside synchronous `postTest`: its own pending microtask could
otherwise produce a misleading failure.

The native fixture reuses `IntegrationTestWidgetsFlutterBinding.ensureInitialized()`.
Flutter 3.44.4's native launcher initializes that binding before invoking the
fixture's `main`, so constructing a custom binding there is invalid. Suite
teardown provides the required lifecycle position with the existing binding.

Observed on macOS with Flutter **3.44.4**, framework
`ad70ec4617166f1c38e5d2bfd388af71fda14f06`, Dart **3.12.2**:

| Binding on the host Flutter tester | Cleanup omitted | Cleanup enabled |
| --- | --- | --- |
| `LiveTestWidgetsFlutterBinding` | Exit 1, disposed-manager error | Exit 0, 1 test passed |
| `IntegrationTestWidgetsFlutterBinding` | Exit 1, disposed-manager error | Exit 0, 1 test passed |

Both negative controls followed `WidgetsBinding.handleViewFocusChanged` →
`_ViewState.didChangeViewFocus` → `FocusManager.applyFocusChangesIfNeeded`,
including the asynchronous focus microtask. Only the cleanup
registration/import were omitted for the control, then restored.

From `apps/atlas_flutter`, run the committed host regression with:

```sh
flutter test test/atlas_view_focus_lifecycle_test.dart
```

For the integration-binding host probe, a temporary Dart test outside
`integration_test/` imports `package:integration_test/integration_test.dart` and
`integration_test/atlas_vault_windows_focus_lifecycle_test.dart`. Its asynchronous
`main` calls `IntegrationTestWidgetsFlutterBinding.ensureInitialized()` before
`await Future(native.main)`, matching the native launcher's initialization order.
Run that wrapper with `flutter test` and
`--dart-define=INTEGRATION_TEST_SHOULD_REPORT_RESULTS_TO_NATIVE=false`, matching
Flutter's tool-collected reporting mode. This exercises the integration
binding on the host tester; it does not run a native Windows executable.

Static analysis passed and all five changed Dart files passed formatting.
The full host suite finished with **1,034 passes, 7 skips, 4 failures**. Its only
failures were unchanged tab goldens, reproduced standalone with identical
12/8/8/4-pixel differences; the tab test, golden assets and application Dart
sources match base revision `4aa8b8f0`. The seven skips were five search parity
goldens and two cross-language tests requiring explicit export paths. No golden
baselines or comparison tolerances were changed.

## Native coverage and remaining validation

The PR Windows script and the scheduled/manual Windows persistence workflow
now run this standalone command and check its process exit status:

```powershell
flutter test integration_test\atlas_vault_windows_focus_lifecycle_test.dart -d windows
```

The existing Windows persistence workflow also retains the real migration
recovery process pairs, including migration-finalization-waiter. It runs on
schedule or manual dispatch; PR coverage uses
`scripts/ci/atlasvault_windows.ps1` through the cross-platform security workflow.

The initial candidate `7760b0ec` reached the native fixture in
[Windows persistence job 116379920903](https://github.com/yutsukioka/un_job_application_helper/actions/runs/38722404220/job/116379920903),
where the original custom binding failed to initialize because the launcher had
already created one. Storage and private-state prepare/verify passed; migration
recovery stages were not reached. The fixture now reuses that binding. The
revised host probe with launcher preinitialization passes, and cleanup-disabled
controls for both host bindings still fail with the original disposed-manager
stack.

Native Windows validation of the revised bootstrap, the native negative
control, and full migration persistence stages remain pending. Host reproduction
and clean static analysis do not substitute for candidate-revision CI results.
