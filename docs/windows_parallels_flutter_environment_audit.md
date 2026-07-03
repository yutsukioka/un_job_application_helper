# Windows Parallels Flutter Environment Audit

Date: 2026-07-03

Repository: `C:\src\un_job_application_helper`
Branch: `codex/atlas-flutter-android-parity`
Checkpoint commit before verification: `8e185c0`

## Recommendation

Status: mostly ready.

The Windows VM is ready for Flutter Windows desktop build, test, and launch validation when Flutter commands are run outside the Codex sandbox. The remaining blockers are not the Visual Studio or Flutter Windows toolchain: they are API availability from the VM, Codex sandbox profile/path behavior, Android golden images failing under the Windows renderer, and production packaging/signing decisions.

## Environment Summary

- Hostname: `YUTSUKIOKA2BA5`
- Sandboxed identity: `yutsukioka2ba5\codexsandboxoffline`
- Unsandboxed identity: `yutsukioka2ba5\yutsukioka2`
- OS: Microsoft Windows 11 Pro, version `10.0.26200.8457`, 25H2
- VM: Parallels ARM Virtual Machine
- System type: ARM64-based PC
- VM IP: `10.211.55.4`
- PowerShell: Windows PowerShell `5.1.26100.8457`
- Processor architecture env: `ARM64`
- Repo path check: `C:\src\un_job_application_helper`
- Parallels shared folder check: not under `\\Mac`, `Z:\`, or `Y:\`
- `winget`: not installed / not on PATH
- Execution policies: all scopes `Undefined`

`systeminfo` and WMI/CIM queries were blocked in the Codex sandbox but succeeded unsandboxed for read-only diagnostics.

## Git Summary

- Git version: `2.55.0.windows.1`
- Remote: `https://github.com/yutsukioka/un_job_application_helper`
- Branch: `codex/atlas-flutter-android-parity`
- Git top level: `C:/src/un_job_application_helper`
- `core.autocrlf=false`
- `core.longpaths=true`
- Initial status: no tracked changes; existing untracked `apps/atlas_flutter/test/failures/`
- Checkpoint: empty commit `8e185c0 checkpoint: before windows flutter environment verification`

## Flutter And Dart Summary

- Flutter path: `C:\src\tools\flutter\bin\flutter.bat`
- Dart path: `C:\src\tools\flutter\bin\dart.bat`
- Flutter: `3.44.4`, stable channel, revision `ad70ec4617`
- Dart: `3.12.2`
- DevTools: `2.57.0`
- Flutter SDK path is local Windows storage: `C:\src\tools\flutter`
- Flutter config: Windows desktop is enabled by default
- Devices:
  - `Windows (desktop) - windows-x64`
  - `Edge (web) - web-javascript`

Important Codex sandbox finding:

- Sandboxed Flutter commands hang or fail because the process runs as `CodexSandboxOffline` while `%USERPROFILE%`, `%APPDATA%`, and `%LOCALAPPDATA%` point to `C:\Users\yutsukioka2`.
- Direct Flutter snapshot invocation failed with: unable to write `C:\Users\yutsukioka2\AppData\Roaming\.flutter_tool_state`.
- `C:\src\tools\flutter` also appears as a dubious-ownership Git repo to the sandbox user.
- Unsandboxed Flutter commands run correctly as `yutsukioka2`.

## Flutter Doctor Summary

`flutter doctor -v` passed for Windows desktop development:

- Flutter: OK
- Windows Version: OK
- Visual Studio: OK, Visual Studio Community 2026 `18.7.3`
- Windows SDK: `10.0.26100.0`
- Connected Windows device: OK
- Network resources: OK

Non-Windows blockers:

- Android SDK not installed.
- Chrome executable not found for web development.

These are not blockers for this VM's Windows desktop purpose.

## Visual Studio And Toolchain Summary

Visual Studio detected:

- Install path: `C:\Program Files\Microsoft Visual Studio\18\Community`
- Display name: Visual Studio Community 2026
- Version: `18.7.11925.98`
- Native desktop workload: present
- MSVC version: `14.51.36231`
- Windows 11 SDK `10.0.26100.0`: present
- CMake tools: present
- Ninja: present
- MSBuild: present
- `devenv.exe`: present
- x64 and ARM64 VC tools: present

Plain PowerShell PATH note:

- `where.exe cl`, `where.exe cmake`, `where.exe ninja`, `where.exe msbuild`, and `where.exe devenv` did not find tools on PATH.
- This is acceptable for Flutter because Flutter locates Visual Studio through vswhere and Visual Studio metadata.

## VS Code And Codex Summary

- VS Code: `1.126.0`, arm64
- VS Code command resolved through: `C:\Users\yutsukioka2\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd`
- Extensions installed:
  - `dart-code.dart-code`
  - `dart-code.flutter`
- Codex app package:
  - Name: `OpenAI.Codex`
  - Version: `26.623.13972.0`
  - Architecture: `Arm64`
  - Install location: `C:\Program Files\WindowsApps\OpenAI.Codex_26.623.13972.0_arm64__2p2nqsd0c76g0`
- `codex --version` fails with access denied from this shell, even unsandboxed.
- This thread is running in Windows PowerShell mode, not WSL2.
- Codex workspace access is correctly limited to the repo and temp paths, but that sandbox profile is too constrained/misconfigured for Flutter CLI execution without unsandboxed approval.

## Repo Structure Summary

Expected app paths exist:

- `apps\apple`
- `apps\atlas_flutter`
- `apps\atlas_flutter\pubspec.yaml`
- `apps\atlas_flutter\windows`
- `apps\atlas_flutter\lib`
- `apps\atlas_flutter\README.md`

Flutter project metadata:

- `pubspec.yaml` name: `atlas`
- Version: `1.0.0+1`
- Updated description: `Atlas Flutter client for UN job search and offline cache parity.`
- Windows executable name remains `atlas.exe`.
- User-facing Windows metadata and title now use `Atlas`.

## Fixes Applied

1. Windows user-facing app metadata:
   - `windows\runner\Runner.rc` now uses `Atlas` for `FileDescription`, `InternalName`, and `ProductName`.
   - `windows\runner\main.cpp` now opens the window with title `Atlas`.

2. Windows local cache fallback:
   - Windows now uses `%APPDATA%\Atlas\atlas-local-cache-v1.json` or `%LOCALAPPDATA%\Atlas\atlas-local-cache-v1.json`.
   - Non-Windows fallback remains under system temp for development/test fallback.

3. API default and docs:
   - Controller default now supports `--dart-define=ATLAS_API_BASE_URL=...`.
   - Fallback default is `http://127.0.0.1:8765`.
   - Settings copy now tells Windows/desktop users to use the Mac LAN URL and `job-api --host 0.0.0.0`.
   - README now describes Windows desktop validation plus Android parity.

## Flutter Verification Results

Passed:

- `flutter pub get`
- `flutter analyze`
- Non-golden test run: 55/55 passed
- Targeted changed tests: 25/25 passed
- `flutter build windows --debug`
- `flutter build windows --release`
- Rebuilt release executable launched, responded, and closed cleanly

Full test suite:

- `flutter test` final result: 55 passed, 9 failed
- All 9 failures are golden-image comparisons against `test/goldens/android`.
- Failure range observed: about 1.91% to 5.23% pixel diff.
- Classification: non-blocking for Windows environment readiness, but blocking for a green full Flutter test command on Windows unless Windows-specific goldens or renderer-tolerant comparison strategy is added.

## Windows Build Outputs

Debug executable:

- Path: `C:\src\un_job_application_helper\apps\atlas_flutter\build\windows\x64\runner\Debug\atlas.exe`
- Size: `1266688` bytes

Release executable:

- Path: `C:\src\un_job_application_helper\apps\atlas_flutter\build\windows\x64\runner\Release\atlas.exe`
- Size: `90624` bytes
- Modified: `2026-07-03 13:01:26`
- ProductName: `Atlas`
- FileDescription: `Atlas`
- FileVersion: `1.0.0+1`
- PE machine type: `8664 machine (x64)`
- Subsystem: Windows GUI

Architecture result:

- Build output is x64, not ARM64.
- This matches Flutter's detected Windows desktop device: `windows-x64`.
- On this ARM64 Parallels VM, x64 output runs through Windows x64 emulation.

Launch validation:

- Command used `Start-Process`.
- Process started as `atlas`, stayed alive for 5 seconds, was responding, and exited after `CloseMainWindow()`.

## API Connectivity Result

Docs and code show the API health endpoint:

- `GET /api/health`
- Default local API: `http://127.0.0.1:8765`
- Mac LAN URL format: `http://<mac-lan-ip>:8765`

Environment variables:

- `ATLAS_API_BASE_URL`: not set
- `JOB_API_BASE_URL`: not set

Connectivity tests:

- `Invoke-WebRequest http://10.253.1.43:8765/api/health`: failed, unable to connect.
- `Test-NetConnection 10.253.1.43 -Port 8765`: ping succeeded, TCP failed.
- `Test-NetConnection 127.0.0.1 -Port 8765`: TCP failed.
- `Test-NetConnection 10.211.55.1 -Port 8765`: ping succeeded, TCP failed.
- `Test-NetConnection 10.211.55.2 -Port 8765`: ping succeeded, TCP failed.

Interpretation:

- Basic network reachability exists to the documented LAN IP and Parallels addresses.
- Nothing tested was listening on TCP port `8765`.
- Likely causes: `job-api` is not running, it is bound only to `127.0.0.1` on the Mac, Mac firewall blocks inbound port `8765`, or the Mac LAN IP has changed.
- Recommended Mac-side command from repo root:
  - `uv run --with-editable ./packages/jobagg --with-editable ./services/job-api --module uvicorn job_api.app:app --host 0.0.0.0 --port 8765`

## Production Packaging Readiness

Current state:

- MSIX/package installer: not configured.
- Microsoft Store identity: not configured.
- Self-hosted installer strategy: not configured.
- Signing/certificate strategy: not configured.
- Crash logging: not configured.
- Auto-update strategy: not configured.
- App icon: `windows\runner\resources\app_icon.ico` exists; customization was not visually validated.
- Version metadata: follows Flutter `version: 1.0.0+1` and now embeds `Atlas`.
- Desktop sizing: initial window size is `1280x720`.
- DPI awareness: `PerMonitorV2`.
- Windows cache storage: fixed to use roaming/local app data when available.
- API defaults: fixed to allow build-time override and generic local fallback.

Recommended next commits:

1. Add MSIX packaging only after choosing Store vs self-hosted distribution.
2. Define signing certificate and publisher identity.
3. Decide x64-only vs ARM64 support. Current release is x64.
4. Add Windows-specific golden baselines or skip Android goldens on Windows CI.
5. Add crash reporting if this will be distributed outside personal testing.
6. Add update strategy if self-hosting.
7. Confirm/customize Windows `.ico` visually.

## Cross-Platform Safety Findings

Blockers:

- None for Windows build/launch.

Warnings:

- `apps/atlas_flutter` imports `dart:io`; acceptable for Windows/Android desktop/mobile, but not web-portable without conditional abstractions.
- Full `flutter test` is not green on Windows because Android golden baselines differ under the Windows renderer.
- `apps/apple\Sources\AtlasUI\AtlasAPIClient.swift` contains a hardcoded LAN fallback `http://192.168.50.208:8765`; acceptable for Apple prototype context, but should be configurable for production.
- `apps/atlas_flutter\android\app\src\main\res\xml\network_security_config.xml` allows cleartext local dev hosts; acceptable for Android local development, not a production-network strategy.

Acceptable local defaults:

- `services/job-api` default host `127.0.0.1:8765`.
- Tests using `127.0.0.1`, `localhost`, and example LAN IPs.
- Android emulator `10.0.2.2`.

Documentation-only:

- Old `/Users/yutsukioka2/...` paths appear in `apps/atlas_flutter\PR_REPORT.md`.
- Local server examples in docs.

## Exact Commands That Passed

System and repo:

- `pwd`
- `hostname`
- `whoami`
- `systeminfo` (unsandboxed)
- `$PSVersionTable`
- `Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsName, OsVersion, OsBuildNumber, OsArchitecture, CsSystemType, CsManufacturer, CsModel, BiosFirmwareType`
- `$env:PROCESSOR_ARCHITECTURE`
- `Get-ExecutionPolicy -List`
- `Get-Location`
- `Test-Path -LiteralPath 'C:\src\un_job_application_helper'`
- `Resolve-Path -LiteralPath 'C:\src\un_job_application_helper'`
- `git --version`
- `git config --global --list`
- `git status`
- `git remote -v`
- `git branch --show-current`
- `git rev-parse --show-toplevel`
- `git config core.autocrlf`
- `git config core.longpaths`
- `git commit --allow-empty -m "checkpoint: before windows flutter environment verification"` (unsandboxed)

Flutter and Dart:

- `where.exe flutter`
- `where.exe dart`
- `& 'C:\src\tools\flutter\bin\cache\dart-sdk\bin\dart.exe' --version`
- `flutter --version` (unsandboxed)
- `dart --version` (unsandboxed)
- `flutter config` (unsandboxed)
- `flutter config --list` (unsandboxed)
- `flutter doctor -v` (unsandboxed)
- `flutter devices` (unsandboxed)

Visual Studio:

- `Test-Path -LiteralPath "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"`
- `& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -all -prerelease -products * -format json -utf8`
- `& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -all -prerelease -products * -requires Microsoft.VisualStudio.Workload.NativeDesktop -property installationPath`
- `& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -all -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`
- `& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -all -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.ARM64 -property installationPath`
- `& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -all -prerelease -products * -requires Microsoft.VisualStudio.Component.Windows11SDK.26100 -property installationPath`
- `& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -all -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.CMake.Project -property installationPath`
- `Get-ChildItem -Path 'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\*\bin\Host*\*\cl.exe'`
- `Get-ChildItem -Path 'C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'`
- `Get-ChildItem -Path 'C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe'`
- `Get-ChildItem -Path 'C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe'`
- `Get-ChildItem -Path 'C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\devenv.exe'`

VS Code and Codex:

- `code --version`
- `Get-Command code | Format-List CommandType,Source,Path,Definition`
- `code --list-extensions` (unsandboxed)
- `where.exe codex`
- `Get-AppxPackage -Name OpenAI.Codex | Select-Object Name, Version, Architecture, InstallLocation, PackageFullName` (unsandboxed)

Flutter app:

- `tree /F /A apps` (output was too long and truncated)
- `Get-ChildItem -LiteralPath 'apps'`
- `Get-ChildItem -LiteralPath 'apps\apple'`
- `Get-ChildItem -LiteralPath 'apps\atlas_flutter'`
- `Test-Path -LiteralPath 'apps\atlas_flutter\pubspec.yaml'`
- `Test-Path -LiteralPath 'apps\atlas_flutter\windows'`
- `Test-Path -LiteralPath 'apps\atlas_flutter\lib'`
- `Test-Path -LiteralPath 'apps\atlas_flutter\README.md'`
- `flutter pub get` (unsandboxed)
- `flutter analyze` (unsandboxed, final run passed)
- `flutter test test/android_network_config_test.dart test/atlas_api_client_test.dart test/atlas_detail_formatter_test.dart test/atlas_domain_coverage_test.dart test/atlas_filters_test.dart test/atlas_local_cache_test.dart test/atlas_search_controller_test.dart test/atlas_settings_panel_test.dart test/widget_test.dart` (unsandboxed)
- `flutter test test/atlas_settings_panel_test.dart test/atlas_search_controller_test.dart test/widget_test.dart` (unsandboxed)
- `flutter build windows --debug` (unsandboxed)
- `flutter build windows --release` (unsandboxed)
- `Start-Process` launch check for `build\windows\x64\runner\Release\atlas.exe` (unsandboxed)
- `dumpbin.exe /headers` on the release executable

API and safety:

- `Invoke-WebRequest -Uri 'http://10.253.1.43:8765/api/health' -UseBasicParsing -TimeoutSec 5` (failed to connect but command ran)
- `Test-NetConnection -ComputerName '10.253.1.43' -Port 8765`
- `Test-NetConnection -ComputerName '127.0.0.1' -Port 8765`
- `Test-NetConnection -ComputerName '10.211.55.1' -Port 8765`
- `Test-NetConnection -ComputerName '10.211.55.2' -Port 8765`
- `rg "/Users/|C:\\|127\.0\.0\.1|localhost|Platform\.isMacOS|dart:io|\\\\" .` (output too broad/truncated)
- narrowed `rg` scans for local URLs, `dart:io`, and production packaging markers

## Exact Commands That Failed Or Were Blocked

- `systeminfo` in sandbox: access denied; passed unsandboxed.
- `Get-CimInstance -ClassName Win32_OperatingSystem`: access denied in sandbox.
- `Get-CimInstance -ClassName Win32_ComputerSystem`: access denied in sandbox.
- `Get-CimInstance -ClassName Win32_Processor`: access denied in sandbox.
- `winget --version`: `winget` not recognized.
- `dart --version` in sandbox: hung; stopped its PowerShell wrapper.
- `flutter --version` in sandbox: hung; stopped its PowerShell wrapper.
- Direct Flutter snapshot in sandbox: failed writing `C:\Users\yutsukioka2\AppData\Roaming\.flutter_tool_state`.
- `git -C 'C:\src\tools\flutter' rev-parse HEAD` in sandbox: dubious ownership.
- `where.exe cl`: not found in plain PowerShell PATH.
- `where.exe cmake`: not found in plain PowerShell PATH.
- `where.exe ninja`: not found in plain PowerShell PATH.
- `where.exe msbuild`: not found in plain PowerShell PATH.
- `where.exe devenv`: not found in plain PowerShell PATH.
- `where.exe code`: not found by native `where`, though `Get-Command code` resolved `code.cmd`.
- `code --list-extensions` in sandbox: failed creating `C:\Users\yutsukioka2\AppData\Roaming\Code\User`; passed unsandboxed.
- `codex --version`: access denied, including unsandboxed shell.
- `git commit --allow-empty -m "checkpoint: before windows flutter environment verification"` in sandbox: permission denied creating `.git\index.lock`; passed unsandboxed.
- `flutter test`: 55 passed, 9 Android golden-image tests failed on Windows.
- `Invoke-WebRequest -Uri 'http://10.253.1.43:8765/api/health' -UseBasicParsing -TimeoutSec 5`: unable to connect to remote server.
- `Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/health' -UseBasicParsing -TimeoutSec 3`: unable to connect to remote server.

## Remaining Manual Actions

1. Start `job-api` on the Mac with `--host 0.0.0.0 --port 8765`.
2. Confirm the current Mac LAN IP with `ipconfig getifaddr en0` or `ipconfig getifaddr en1`.
3. From Windows, rerun `Invoke-WebRequest http://<mac-lan-ip>:8765/api/health` and `Test-NetConnection <mac-lan-ip> -Port 8765`.
4. Decide whether Windows builds should be x64-only or whether ARM64 Windows output is required.
5. Decide packaging channel: MSIX Store, MSIX self-hosted, or another installer.
6. Define certificate/signing/publisher identity.
7. Add Windows-specific golden baselines or make Android goldens conditional outside Android/Linux CI.
8. Fix Codex sandbox profile environment if Flutter should run without unsandboxed approvals.
9. Investigate why `codex --version` cannot execute the Store app binary from PowerShell.
10. Verify/customize the Windows icon visually.

