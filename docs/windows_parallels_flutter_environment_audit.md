# Windows Parallels Flutter Environment Audit v2

Date: 2026-07-06

Repository: `C:\src\un_job_application_helper`

Branch: `master`

Supersedes: 2026-07-03 Windows Parallels Flutter Environment Audit

Related commits:

- `5774f06 document windows flutter environment readiness` - 2026-07-03 audit history.
- `a76dc6a test(atlas_flutter): skip android goldens on windows` - historical Windows skip policy.
- `5890bff test(atlas_flutter): skip android goldens on windows` - duplicate historical Windows skip policy on the backup line.
- `a8ef069 test(atlas_flutter): skip Android goldens on Windows` - current `master` restoration of the golden skip policy.

## Verdict

Windows Flutter desktop development is production-ready on this Parallels VM using the workspace-local sandbox env posture.

## Environment Summary

Verified from the 2026-07-03 audit history, 2026-07-04 state files, and the 2026-07-06 refresh:

- Hostname: `YUTSUKIOKA2BA5`
- Repo: `C:\src\un_job_application_helper`
- Sandbox identity: `yutsukioka2ba5\codexsandboxoffline`
- Unsandboxed identity: `yutsukioka2ba5\yutsukioka2`
- VM: Parallels ARM Virtual Machine
- System type: ARM64-based PC
- Processor architecture env: `ARM64`
- Windows: Microsoft Windows 11 Pro, 25H2
- Windows build observed by Flutter: `10.0.26200.8655`
- PowerShell: Windows PowerShell `5.1.26100.8655`
- Visual Studio: Visual Studio Community 2026 `18.7.3`
- Visual Studio install path: `C:\Program Files\Microsoft Visual Studio\18\Community`
- Visual Studio version: `18.7.11925.98`
- Windows SDK: `10.0.26100.0`
- MSVC toolset path observed: `VC\Tools\MSVC\14.51.36231`
- Flutter: `3.44.4`, stable channel, revision `ad70ec4617`
- Dart: `3.12.2`
- DevTools: `2.57.0`
- Flutter SDK path: `C:\src\tools\flutter`
- Flutter Windows device: `Windows (desktop)`, `windows-x64`
- Codex desktop/runtime: `26.623.101652`, Arm64

Non-Windows doctor issues remain informational for this audit:

- Android SDK is not installed.
- Chrome executable is not configured for web development.

## Sandbox Posture (Canonical)

Use this workspace-local environment for Flutter and Dart commands from the Codex sandbox:

```powershell
$app = 'C:\src\un_job_application_helper\apps\atlas_flutter'
$root = 'C:\src\un_job_application_helper\build\codex_flutter_sandbox\workspace_pub_cache'

$env:APPDATA = Join-Path $root 'Roaming'
$env:LOCALAPPDATA = Join-Path $root 'Local'
$env:TEMP = Join-Path $root 'Temp'
$env:TMP = $env:TEMP
$env:PUB_CACHE = Join-Path $app '.pub-cache'
$env:FLUTTER_SUPPRESS_ANALYTICS = 'true'

New-Item -ItemType Directory -Force -Path $env:APPDATA,$env:LOCALAPPDATA,$env:TEMP,$env:PUB_CACHE | Out-Null
Set-Location $app
```

Rationale:

- Redirects `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, and `PUB_CACHE` inside the repo/build tree, which is already Codex-writable.
- Avoids Flutter writes to `C:\Users\yutsukioka2\AppData\Roaming\.flutter_tool_state`, `C:\Users\yutsukioka2\AppData\Roaming\.dart-tool`, and `C:\Users\yutsukioka2\AppData\Local\.dartServer`.
- Avoids MSBuild `FileTracker` temp-path access failures by keeping `TEMP` and `TMP` inside the writable tree.
- Requires no cross-user NTFS ACL grants.
- Requires no global Git config changes beyond marking `C:/src/tools/flutter` as safe for Git, because Flutter shells out to Git inside the SDK repo.
- Is reproducible on any Windows machine that has this repo, Flutter SDK, Visual Studio native desktop tooling, and a writable workspace.

Recommended command pattern after the first network-backed cache warm:

```powershell
flutter analyze --no-pub
flutter test --no-pub
flutter build windows --release --no-pub
```

## Golden Test Policy

Android golden baselines are skipped on Windows via `Platform.isWindows` in:

- `apps/atlas_flutter/test/search_golden_test.dart`
- `apps/atlas_flutter/test/tab_golden_test.dart`

Commit reference: `a8ef069 test(atlas_flutter): skip Android goldens on Windows`.

Expected Windows test shape:

```text
58 passed, 9 skipped
```

Do not run this command on Windows for these baselines:

```powershell
flutter test --update-goldens
```

Do not replace or regenerate `apps/atlas_flutter/test/goldens/android/*.png` from Windows output. The Android baselines are renderer parity assets; Windows text and rasterization produce stable but irrelevant pixel diffs.

Classification source: `C:\state\golden_classification.md`.

## Verification Results (2026-07-06)

Commands were run from `apps/atlas_flutter` with the canonical workspace-local sandbox env.

| Check | Result |
| --- | --- |
| `flutter analyze --no-pub` | PASS: `No issues found!` |
| `flutter test --no-pub` | PASS: `00:52 +58 ~9: All tests passed!` |
| `flutter build windows --debug` | PASS |
| `flutter build windows --release` | PASS |
| Release artifact | `apps/atlas_flutter/build/windows/x64/runner/Release/atlas.exe` |
| PE machine | `0x8664` (`34404`, x64) |
| PE subsystem | `2`, Windows GUI |
| Current artifact VersionInfo | `ProductName=atlas`, `FileDescription=atlas`, `FileVersion=1.0.0+1` |

Important metadata note:

- The 2026-07-03 audit recorded `ProductName=Atlas` and `FileDescription=Atlas` after branch-local Windows metadata fixes.
- The current verified `master` artifact and `windows\runner\Runner.rc` report lowercase `atlas`.
- Treat uppercase `Atlas` as the product naming target from the prior audit, not as the current verified VersionInfo on `master`.

Release bundle contents observed:

- `atlas.exe`
- `flutter_windows.dll`
- `native_assets.json`
- `data\`

### Follow-up: Windows Metadata Casing — RESOLVED 2026-07-06

The "Important metadata note" under Verification Results (2026-07-06)
documented a discrepancy: current master emitted lowercase `atlas` in
Windows VersionInfo, while the 2026-07-03 audit recorded uppercase
`Atlas` as the product naming target.

Resolution applied 2026-07-06:
- apps/atlas_flutter/windows/runner/Runner.rc: FileDescription,
  InternalName, and ProductName values updated to `Atlas` (uppercase).
- apps/atlas_flutter/windows/runner/main.cpp: window title updated to
  `L"Atlas"` (wide-string literal required by Win32 CreateWindowExW
  and the FlutterWindow::Create(const std::wstring&, ...) signature).
- Executable filename intentionally preserved as `atlas.exe` (matches
  apps/atlas_flutter/pubspec.yaml `name: atlas`).

Verified via release rebuild:

| Field            | Value       |
|------------------|-------------|
| ProductName      | Atlas       |
| FileDescription  | Atlas       |
| InternalName     | Atlas       |
| OriginalFilename | atlas.exe   |
| FileVersion      | 1.0.0+1     |
| Artifact path    | apps/atlas_flutter/build/windows/x64/runner/Release/atlas.exe |

`git diff --check` passed; only the two expected Windows runner files
were modified.

Casing convention going forward (Windows target):
- Display name in metadata and runtime window title: `Atlas` (uppercase).
- Executable filename: `atlas.exe` (lowercase; matches pubspec name).
- Any future Windows runner strings (menu items, dialog captions, etc.)
  should use the same casing conventions and, in main.cpp, use
  wide-string literals (`L"..."`) per the FlutterWindow/Win32 API
  contract.

Wide-string rationale (recorded for future maintainers): Flutter's
Windows runner uses the Win32 W-suffixed API family (CreateWindowExW,
etc.), which requires UTF-16 (`wchar_t*`) strings. The C++ `L"..."`
prefix produces a `const wchar_t[]` that constructs a `std::wstring`
directly; narrow `"..."` literals would fail to compile against the
FlutterWindow::Create signature, and even where they'd compile they
would not correctly render non-ASCII app titles. This is not merely
a style choice: it is required by the Flutter Windows template and
by correct Unicode handling for international users.

The "atlas vs Atlas" drift noted in the Verification Results table
above is closed as of this subsection.

## Split-Role Collaboration Contract

A separate collaboration-pattern document is not present on this baseline.
Until one is added, the working contract below is the only claim made by this
audit.

Current working contract:

- Windows Codex is the build authority for `flutter doctor -v`, Visual Studio / MSBuild readiness, Windows debug/release builds, PE metadata checks, sandbox posture, and `atlas.exe` artifact validation.
- Mac Codex is the product authority for Flutter behavior, UI/product decisions, iOS/Android parity review, golden intent, and feature implementation.
- Mac Codex can prepare code; Windows Codex certifies that the Windows desktop executable builds and that Windows-only environment assumptions still hold.

## Loop Engineering Checkpoints

Checkpoint and safety tags observed in this session/state:

- `safety/pre-migrate-20260704T072558Z`
- `checkpoint/goldens/20260706T035307Z/pre-cherry-pick`
- `checkpoint/windows-audit/20260704T080848Z/phase1-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase2-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase3-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase4-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase5-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-pubget-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-analyze-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-test-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-build-debug-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-build-release-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-metadata-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-launch-smoke-pre`
- `checkpoint/windows-audit/20260704T080848Z/phase6-baseline-refresh-pre`
- `checkpoint/windows-audit/pre-sandbox-writable-root/20260704T201951Z`

## Remaining Manual Actions

None for the Windows Flutter desktop environment posture.

Product and distribution decisions remain open from the prior audit:

- Decide MSIX packaging approach.
- Define signing certificate and publisher identity.
- Decide whether Windows distribution remains x64-only or needs an ARM64 variant.
- Define crash reporting if this app will be distributed beyond personal testing.
- Define auto-update strategy if self-hosting.
- Confirm/customize Windows icon visually.

## Appendix A: Rollback Recipes

Return to the pre-golden-policy checkpoint:

```powershell
git fetch origin --tags
git switch master
git reset --hard checkpoint/goldens/20260706T035307Z/pre-cherry-pick
```

Return to the pre-migration safety checkpoint:

```powershell
git fetch origin --tags
git switch master
git reset --hard safety/pre-migrate-20260704T072558Z
```

Inspect a Windows audit phase checkpoint without moving `master`:

```powershell
git fetch origin --tags
git switch --detach checkpoint/windows-audit/20260704T080848Z/phase6-build-release-pre
```

Create a recovery branch from any checkpoint:

```powershell
git fetch origin --tags
git switch -c recovery/windows-audit checkpoint/windows-audit/20260704T080848Z/phase6-build-release-pre
```

Use `git reset --hard` only when intentionally discarding local work. Prefer a detached checkout or recovery branch when investigating.

## Appendix B: History - What Changed From 2026-07-03 To 2026-07-06

The 2026-07-03 audit concluded the VM was mostly ready, with blockers around sandbox profile/path behavior, Android goldens failing under Windows rendering, API reachability, and packaging/signing decisions.

By 2026-07-06:

- The canonical path changed from unsandboxed Flutter commands to a workspace-local sandbox environment using redirected `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, and `PUB_CACHE`.
- The Flutter SDK dubious-ownership problem was handled by marking `C:/src/tools/flutter` as a Git safe directory.
- Flutter CLI AppData writes no longer require cross-user ACL grants when using the canonical environment recipe.
- Analyzer and MSBuild temp/cache writes are contained in the repo/build tree.
- The Android golden failures were classified as policy-not-migrated, not real visual regressions.
- Commit `a8ef069` restored the Windows skip policy for Android goldens.
- `flutter test --no-pub` on Windows now has the expected shape: `58 passed, 9 skipped`.
- `flutter build windows --debug` and `flutter build windows --release` both pass.
- The release artifact remains x64: `apps/atlas_flutter/build/windows/x64/runner/Release/atlas.exe`.
- Environment readiness is no longer blocked; remaining work is product/distribution policy: MSIX packaging, signing, and possible ARM64 output.
