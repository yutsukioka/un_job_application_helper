### Android Atlas parity status update

Latest app-code head covered: `12f0599`
Latest evidence/docs head covered before the completion audit: `a21b2d2`
Status: not final; strict score `86/100`

#### What is now fixed

- Fixed the physical Pixel ANR: Search results now use lazy row construction instead of eagerly
  building more than 2,000 rows during controller rebuilds.
- Fixed cached/offline `Open only`: cached filtering now excludes past-deadline open rows the same
  way the Search API does with `exclude_expired_open=true`.
- Updated the reports so they no longer claim lock-screen-only physical evidence or older live
  counts.

#### Current data reconciliation

- `health_open_jobs`: `2,452`
- `search_api_total`: `2,355`
- Android displayed count after physical refresh: `2,355 searchable results`
- Difference: `97` deadline-past open rows are still counted by health but intentionally hidden by
  Search.
- Backend snapshot: `2026-07-03T04:17:28.239578+00:00`

#### Physical Pixel evidence captured

- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/search_top_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/search_scrolled_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/filter_sheet_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/sort_menu_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/saved_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/updates_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/sources_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/settings_tab_final.png`

Invalid evidence excluded:

- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/job_detail_final.png` is an
  untracked launcher/app-drawer screenshot, not a Job Detail screen.

#### Verification

- `dart analyze`: passed
- `flutter test test/widget_test.dart`: passed with lazy Search row regression
- `flutter test test/atlas_search_controller_test.dart`: passed with cached expired-open filtering
- `flutter test --coverage`: passed, 67 tests
- `flutter build apk --release`: passed
- Release APK: `apps/atlas_flutter/build/app/outputs/flutter-apk/app-release.apk`
- Release AAB: `apps/atlas_flutter/build/app/outputs/bundle/release/app-release.aab`
- PR checks after latest push: `GitGuardian Security Checks` passed, `python` passed
- Thread-aware review check: no current non-outdated unresolved actionable review threads

#### Remaining before merge / G3

- Reconnect/unlock Pixel 8 Pro `38281FDJG001DJ`.
- Capture corrected physical Job Detail screenshot.
- Run and capture physical offline-restart cache verification.
- Re-run live `/api/health` vs `/api/search` count reconciliation after the next refresh.
- Get explicit human `APPROVED: G3` on PR #10.

I do not recommend merging PR #10 until those physical-device items are complete.
