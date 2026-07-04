### Android Atlas parity status update

Latest app-code head covered: `12f0599`
Completion audit: `apps/atlas_flutter/docs/loop/COMPLETION_AUDIT.md`
Status: not final; strict score `91/100`

#### What is now fixed

- Fixed the physical Pixel ANR: Search results now use lazy row construction instead of eagerly
  building more than 2,000 rows during controller rebuilds.
- Fixed cached/offline `Open only`: cached filtering now excludes past-deadline open rows the same
  way the Search API does with `exclude_expired_open=true`.
- Completed the corrected physical Pixel evidence pass for refreshed Search/Settings, offline
  restart, and real Job Detail.

#### Current data reconciliation

- `health_open_jobs`: `2,392`
- `search_api_total`: `2,304`
- Android displayed count after physical refresh: `2,304 searchable results`
- Difference: `88` deadline-past open rows are still counted by health but intentionally hidden by
  Search.
- Backend snapshot: `2026-07-04T00:52:18.058256+00:00`

#### Physical Pixel evidence captured

- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/search_top_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/search_scrolled_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/filter_sheet_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/sort_menu_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/saved_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/updates_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/sources_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/settings_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/settings_after_refresh.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/search_top_after_refresh.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/offline_restart_cached.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/job_detail_corrected_clean.png`

Invalid evidence excluded:

- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/job_detail_final.png` is an
  untracked launcher/app-drawer screenshot, not a Job Detail screen. It has been replaced by
  `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/job_detail_corrected_clean.png`.

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
- Physical 2026-07-04 offline restart: passed. Wi-Fi was disabled, Atlas was force-stopped and
  relaunched, and cached Search rows appeared immediately with `2,304 searchable results`.
- Physical 2026-07-04 Job Detail: passed. A Search row opened real Job Detail with title, bookmark,
  metadata chips, weak-detail state, full description, and core fields.
- G3 physical-device gate: passed. `APPROVED: G3` was posted by `yutsukioka` on PR #10 at
  `2026-07-04T01:07:37Z`.

#### Remaining before merge

- Exact `APPROVED: G2` design-review comment is still missing from PR #10.
- Recheck PR checks and Copilot/human review threads after this evidence update lands.
- If required by the reviewer, add exact user-provided iOS screenshots as local files for final
  screenshot pairing.

I do not recommend merging PR #10 until the exact G2 design-review approval is present or explicitly
waived.
