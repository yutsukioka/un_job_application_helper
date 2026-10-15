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
and pumps that setup work. Its binding then delivers a focused `ViewFocusEvent`
through `handleViewFocusChanged` **after** `super.postTest`. The suite drains the
queued event work and asserts that the body ran and exactly one event was
injected. It neither catches asynchronous exceptions nor replaces error
handlers. Focus preparation must not occur inside synchronous `postTest`: its
own pending microtask could otherwise produce a misleading failure.

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
`integration_test/` imports
`integration_test/atlas_vault_windows_focus_lifecycle_test.dart` and calls its
`main()`. Run that wrapper with `flutter test` and
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

Native Windows execution, the native negative control, and the full migration
persistence stages have **not yet been run for this change**. Host reproduction
and clean static analysis establish the lifecycle fix's local behavior; they
do not substitute for those candidate-revision CI results.
