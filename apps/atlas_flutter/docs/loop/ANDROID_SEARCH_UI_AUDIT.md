# Android Search UI Audit

Date: 2026-07-03
Branch: `codex/atlas-flutter-android-parity`
Slice: persistent cache, full filters/cascades, icon parity

## Executive Result

The Flutter Android app now has implemented Updates, Sources, Saved, Settings, Job Detail,
persistent local cache, offline local filtering, an iOS-style filter sheet, City/Country and
Seniority/Grade cascade tests, and a shared Cupertino-style `AtlasIcons` mapping for visible Atlas
controls.

This is not a final completion claim. The latest release APK was rebuilt and installed on the USB
Pixel 8 Pro, but post-fix physical in-app screenshots are blocked because the physical device
remained on the lock screen. Emulator screenshots from the release app now provide human-reviewable
evidence for the cache, filter, cascade, icon, tab, and detail slice.

## Implemented This Slice

- Persistent file-backed cache in Android app-private storage through the native `filesDir` method
  channel.
- Cache snapshot now stores the active Search response and the broad default open Search dataset
  separately, so offline filters can be recomputed from cached rows.
- Cache snapshot now also stores fetched Job Detail payloads, so previously opened details can be
  reopened after app restart or while offline.
- Startup cache hydration loads cached Search data before any network refresh.
- Offline refresh failure keeps cached rows visible and marks the app `Offline (cached)`.
- Settings shows cache freshness/status, Clear Local Cache, and the real cached-detail count instead
  of a hardcoded placeholder.
- Local offline filtering mirrors Swift semantics: OR within a group, AND across groups.
- Dark filter modal with drag handle, `Filters` title, `Done`, sticky `Reset` / `Apply filters`,
  compact two-column pills, counts, dimmed unavailable values, and explanatory text.
- Filter groups now render: Status, Location, Scope, Contract, UN Volunteer Category, Seniority,
  Grade, CCOG Family, Organizations, Work Mode, and Capability Tags.
- City/Country cascade test: selecting `JPN` restricts cities to Tokyo/Osaka; selecting `Tokyo`
  restricts countries to `JPN`.
- Multi-location selection is now supported in Android: comma-separated text input and filter-sheet
  pills can select multiple cities/countries, which serialize to Search API list fields and match
  locally as OR within the Location group.
- Seniority/Grade cascade test: `standard_seniority_tier` maps Entry Junior rows to junior grades;
  selecting grade `P1` restricts seniority to Entry Junior.
- Core app icons now route through shared `AtlasIcons` using Cupertino-style symbols for Search,
  filters, bookmark, deadline, location, organization, contract, remote, updates, sources, settings,
  chevron, info/warning, copy/link, refresh, and delete.

## Data Count Reconciliation

| Field | Value |
| --- | --- |
| `health_open_jobs` | `2,420` |
| `search_api_total` | `2,269` |
| `android_displayed_total` | `2,269 searchable results` after a current refresh; older screenshots show `2,271` from the prior cache timestamp. |
| `active_filters` | Default Search: `status=["open"]`, no text query, no source/org filters, sort `closing_date_asc`; Search API default `exclude_expired_open=true`. |
| `local_cache_timestamp` | `2026-07-03 01:16` on the emulator screenshot pass; offline restart reused the cache at `2026-07-03 01:34`. |
| `backend_snapshot_timestamp` | `/api/health last_sync_at=2026-07-02T02:38:47.964722+00:00`. |
| `excluded_count` | `151` |
| `excluded_reason_breakdown` | Rows still marked `status='open'` in health but with passed deadlines, hidden by Search API `exclude_expired_open=true`. |
| `final_decision` | `2,269` is correct for Android default Search at this capture time because it matches `/api/search`; `2,420` is the raw health open count. The difference is time-sensitive as deadlines pass. |

## Verification

Commands run from `apps/atlas_flutter`:

- `dart format --set-exit-if-changed .` passed.
- `dart analyze` passed with no issues.
- Focused tests passed: `flutter test test/atlas_api_client_test.dart test/atlas_filters_test.dart test/atlas_local_cache_test.dart test/atlas_search_controller_test.dart test/widget_test.dart`.
- Full tests passed: `flutter test --coverage`, 44 tests.
- Coverage passed: `2727/3011` lines, `90.57%`.
- `flutter build apk --debug` passed.
- `flutter build apk --release` passed: `build/app/outputs/flutter-apk/app-release.apk` (`50.8MB`).
- `flutter build appbundle --release` passed: `build/app/outputs/bundle/release/app-release.aab` (`49.8MB`).
- `flutter test integration_test -d emulator-5554` passed against the Android emulator after updating
  the smoke test for the new `Done` filter-sheet control.
- Release APK installed on USB Pixel 8 Pro: `adb -s 38281FDJG001DJ install -r build/app/outputs/flutter-apk/app-release.apk` returned `Success`.
- Follow-up multi-location verification: `dart format --set-exit-if-changed .`, `dart analyze`,
  focused `flutter test test/atlas_filters_test.dart test/atlas_search_controller_test.dart`, and
  full `flutter test` passed with 42 tests. The new tests cover comma-separated multi-city/country
  Search request serialization and offline cached City/Country OR filtering.
- Integration smoke was not rerun for this follow-up because `emulator-5554` was unavailable and
  the only attached physical Pixel remained locked.
- Follow-up Job Detail regression verification: `dart format --set-exit-if-changed .`,
  `dart analyze`, focused `flutter test test/widget_test.dart --plain-name "job detail renders
  populated fields and keeps diagnostics hidden"`, full `flutter test`, and `flutter test
  --coverage` passed with 43 tests and `2683/2961` lines (`90.61%`). The new widget test covers
  populated core fields, responsibilities/qualifications sections, apply/source links, diagnostics
  hidden until expansion, and detail save persistence.
- Persistent Job Detail cache verification: `flutter test test/atlas_local_cache_test.dart
  test/atlas_search_controller_test.dart` passed, then full `dart format --set-exit-if-changed .`,
  `dart analyze`, `flutter test --coverage`, `flutter build apk --debug`, `flutter build apk
  --release`, and `flutter build appbundle --release` all passed. The new tests cover detail-cache
  serialization, persistence after fetch, offline restart hydration, and serving a cached detail when
  the transport is unavailable.
- Physical Pixel integration attempt: `flutter test integration_test -d 38281FDJG001DJ` built and
  installed the debug test APK, but the device-driven test did not complete after launch and was
  interrupted after `1:46`; the Pixel still reports keyguard/doze state, so this remains a physical
  automation blocker, not an app assertion failure.

Installed package evidence:

- Package: `com.yutsukioka.jobagg.atlas`
- Version: `versionCode=1`, `versionName=1.0.0`
- Device: `38281FDJG001DJ`
- Package `lastUpdateTime`: `2026-07-03 02:46:54` after the persistent-detail-cache release APK
  install.

## PR Review Feedback

Latest actionable Copilot review clusters addressed:

- Windows runner:
  - Guarded `OnDestroy()` against re-entrant `WM_DESTROY`.
  - Linked `advapi32.lib` for `RegGetValue`.
  - Replaced `UNICODE_STRING_MAX_CHARS` dependency with a local bounded constant.
  - Fixed the duplicated "as as" comment in `win32_window.h`.
- Android networking:
  - Removed global release `android:usesCleartextTraffic="true"`.
  - Changed network security from a cleartext base config to a default-deny base plus an allowlist
    for local development hosts: `10.253.1.43`, `10.0.2.2`, `127.0.0.1`, and `localhost`.
- README:
  - Replaced the stock Flutter template with Atlas-specific setup, test, build, local server, cache,
    screenshot, and physical-device verification instructions.

## Screenshot Evidence

Physical Pixel in-app screenshots are still blocked because the connected Pixel remained on the lock
screen. Captured physical-device files show the lock state only:

- `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-20260703/search_top.png`
- `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-20260703/lock_check.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/current_visibility_check.png`

Physical verification runbook:

- `apps/atlas_flutter/docs/loop/PHYSICAL_PIXEL_VERIFICATION.md`

Post-fix emulator screenshots from the release app are available under
`apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703/`:

- Contact sheet: `android_review_contact_sheet.png`
- Search: `search_top_refreshed.png`, `search_scrolled.png`, `offline_restart_cached.png`
- Search side-by-side: `ios_android_search_top_side_by_side.png`
- Settings/cache status: `settings_after_reload.png`
- Filter sheet: `filter_top.png`, `filter_contract_seniority.png`, `filter_grade_ccog.png`,
  `filter_organizations_workmode.png`, `filter_capability_tags.png`
- Cascade states: `filter_japan_selected.png`, `filter_tokyo_selected.png`,
  `filter_entry_junior_selected.png`, `filter_grade_selected.png`
- Detail/tabs: `job_detail.png`, `saved_tab.png`, `updates_tab.png`, `sources_tab.png`

Dedicated review package:

- `apps/atlas_flutter/docs/loop/IOS_ANDROID_VISUAL_REVIEW.md`
- `apps/atlas_flutter/PR_REPORT.md`

Previously generated evidence remains available for Search top comparison:

- `apps/atlas_flutter/docs/loop/screenshots/ios-reference/iteration-8/ios_search_top.png`
- `apps/atlas_flutter/docs/loop/screenshots/iteration-8/search_top_ios_android_side_by_side.png`

The remaining screenshot gap is physical Pixel in-app evidence after the device is unlocked.

## Manual Emulator Evidence

- Release APK installed on `emulator-5554`.
- Server available at `http://10.253.1.43:8765`; Settings `Save and Reload` refreshed and persisted
  `2,271` cached searchable rows.
- Emulator networking was disabled with `svc wifi disable` and `svc data disable`.
- App was force-stopped and relaunched while offline.
- `offline_restart_cached.png` shows cached Search rows immediately with `2,271 searchable results`
  and local save timestamp instead of an empty screen.
- Search/filter/sort rows remained visible from cache. Full offline interaction depth is covered by
  controller/cache tests; physical offline interaction remains a human-review item.

## Current Remaining Gaps

- Physical in-app screenshots for the new cache/filter/icon slice are missing due to lock-screen
  blocker, though emulator screenshots are now captured.
- Fresh Pixel check on 2026-07-03 still shows the lock screen while Atlas is resumed underneath
  keyguard.
- Filter sheet parity is implemented from Swift source and covered by tests, but true pixel-paired
  human review still needs local copies of the user-provided iOS filter screenshots.
- Local Android supports multiple City/Country values through comma-separated input and multi-select
  filter pills. User-facing visual review is still needed to decide whether the comma text display is
  close enough to iOS or should become a dedicated selected-chip editor.
- Backend facets still do not expose city/country or grade-to-seniority metadata. Android computes
  those facets locally from cached Search rows.
- Coverage remains strong at `90.57%` after adding persistent Job Detail cache coverage.
- The Tokyo reverse-cascade screenshot shows selected `TOKYO` with zero same-group count and `JPN`
  visible as the matching country option. This matches the "selected values remain visible" rule but
  should be reviewed for whether the displayed same-group count is the desired product copy.
- Physical Pixel offline restart still needs human verification after the device is unlocked.

## Scorecard

Strict score under the new user caps: **79/100**.

The screenshot cap is no longer the limiting factor for emulator review, and a Search-top
side-by-side package now exists. The score remains below 80 because physical Pixel in-app screenshots
and user-provided iOS filter/detail side-by-side review are still not complete.

| Category | Score | Notes |
| --- | ---: | --- |
| Persistent cache | 18 / 20 | File cache persists broad Search rows; emulator offline restart shows cached results immediately; physical offline restart still pending. |
| Filter parity | 26 / 30 | All iOS groups render in a dark sheet with counts/dimming and sticky actions; multiple City/Country values now serialize and filter locally as OR selections. |
| Icon parity | 7 / 10 | Visible controls route through shared Cupertino-style `AtlasIcons`; human iOS screenshot comparison still pending. |
| Existing Search/data/detail/tabs | 13 / 15 | Count reconciliation, compact rows, Updates/Sources, Saved, populated Detail, and persistent cached details remain intact; live Search count moved to 2,269 due deadline timing. |
| Tests/builds | 14 / 15 | Format, analyze, 44 Flutter tests, coverage, debug APK, release APK, release AAB, and prior emulator integration test pass; current Pixel integration was attempted but blocked by device state. |
| Evidence/human readiness | 1 / 10 | Emulator evidence, Search side-by-side, and Android contact sheet are captured; physical Pixel app screenshots and iOS filter/detail pairs still need final human review. |

## Next Required Human Action

Unlock the connected Pixel 8 Pro, then rerun physical screenshot capture and offline-restart
verification. Do not claim 80%+ completion until those screenshots prove the Android UI/behavior is
close to the iOS reference.
