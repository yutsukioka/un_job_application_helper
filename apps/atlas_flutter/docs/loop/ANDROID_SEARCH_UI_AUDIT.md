# Android Search UI Audit

Date: 2026-07-03
Branch: `codex/atlas-flutter-android-parity`
Slice: persistent cache, full filters/cascades, icon parity

## Executive Result

The Flutter Android app now has implemented Updates, Sources, Saved, Settings, Job Detail,
persistent local cache, offline local filtering, an iOS-style filter sheet, City/Country and
Seniority/Grade cascade tests, and a shared Cupertino-style `AtlasIcons` mapping for visible Atlas
controls. Job Detail now also uses a Dart port of the Swift ATS detail formatter, so ATS chrome and
raw source payloads no longer dominate the main detail screen.

This is not a final completion claim. The latest release APK was rebuilt and installed on the USB
Pixel 8 Pro, but post-fix physical in-app screenshots are blocked because the physical device
remained on the lock screen. Emulator screenshots from the release app now provide human-reviewable
evidence for the cache, filter, cascade, icon, tab, and detail slice. Source-rendered iOS Simulator
captures now provide local references for Search, Filter sheet, Job Detail, Saved, Updates, Sources,
and Settings.

## Implemented This Slice

- Persistent file-backed cache in Android app-private storage through the native `filesDir` method
  channel.
- Cache snapshot now stores the active Search response and the broad default open Search dataset
  separately, so offline filters can be recomputed from cached rows.
- Cache snapshot now also stores fetched Job Detail payloads, so previously opened details can be
  reopened after app restart or while offline.
- Startup cache hydration loads cached Search data before any network refresh.
- Offline refresh failure keeps cached rows visible and marks the app `Offline (cached)`.
- Normal cache hydration no longer emits a large Search banner; the Search top area uses only the
  compact `Local save · updated ...` status line unless there is an actual error/manual action.
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
- Search result source badges now match the Swift `SourceMonogram` pattern: 34px rounded squares,
  deterministic per-source colors, and white source initials instead of pale cyan generic badges.
- Job Detail now formats section payloads through `AtlasATSDetailFormatter`, matching Swift
  behaviors for ATS/PageUp chrome trimming, orphan-fragment healing, canonical section titles, fact
  extraction, bullet/numbered-list blocks, long paragraph splitting, and hidden-boilerplate
  reporting.
- Raw source/source-data/raw-record sections are hidden from the main detail body and remain
  available only under the diagnostics expansion panel.
- Search-top Android golden coverage now exists at
  `apps/atlas_flutter/test/goldens/android/search_top_compact.png`; it locks the compact header,
  chips, count/status row, bottom navigation, and dense result-row layout at a 393x852 viewport.
- Filter-sheet top Android golden coverage now exists at
  `apps/atlas_flutter/test/goldens/android/filter_sheet_top.png`; it locks the dark modal sheet,
  drag handle/header, Done action, Status and Location controls, compact option grids, counts, and
  sticky Reset/Apply footer at the same 393x852 viewport.
- Populated Job Detail top Android golden coverage now exists at
  `apps/atlas_flutter/test/goldens/android/job_detail_top.png`; it locks the useful detail header,
  metadata chips, full description/core details, ATS-formatted content, and the absence of raw source
  diagnostics from the main detail body.
- Implemented tab Android golden coverage now exists at:
  - `apps/atlas_flutter/test/goldens/android/saved_tab.png`
  - `apps/atlas_flutter/test/goldens/android/updates_tab.png`
  - `apps/atlas_flutter/test/goldens/android/sources_tab.png`
  - `apps/atlas_flutter/test/goldens/android/settings_tab.png`
  These baselines lock non-placeholder Saved, Updates, Sources, and Settings content using seeded
  operational data.

## Data Count Reconciliation

| Field | Value |
| --- | --- |
| `health_open_jobs` | `2,420` |
| `search_api_total` | `2,266` |
| `android_displayed_total` | `2,266 searchable results` after the current detail-formatter screenshot refresh; older screenshots show `2,268`, `2,269`, or `2,271` from prior cache timestamps. |
| `active_filters` | Default Search: `status=["open"]`, no text query, no source/org filters, sort `closing_date_asc`; Search API default `exclude_expired_open=true`. |
| `local_cache_timestamp` | `2026-07-03 04:06` on the latest emulator refresh; offline restart reused the persisted cache immediately. |
| `backend_snapshot_timestamp` | `/api/health last_sync_at=2026-07-02T02:38:47.964722+00:00`. |
| `excluded_count` | `154` |
| `excluded_reason_breakdown` | Rows still marked `status='open'` in health but with passed deadlines, hidden by Search API `exclude_expired_open=true`. |
| `final_decision` | `2,266` is correct for Android default Search at this capture time because it matches `/api/search`; `2,420` is the raw health open count. The difference is time-sensitive as deadlines pass. |

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
- Cache-load banner regression verification: focused controller/widget tests passed after confirming
  the cache-load test failed first while `connectionMessage` was set to `Loaded local save from this
  device.`.
- Full current verification passed: `dart analyze`; `flutter test --coverage` with 44 tests and
  `2727/3011` lines (`90.57%`); `flutter build apk --debug`; `flutter build apk --release`;
  `flutter build appbundle --release`; and `flutter test integration_test -d emulator-5554`.
- Source-badge parity verification passed: fail-first widget test for the pale cyan badge, then
  `dart analyze`, `flutter test --coverage` with 45 tests and `2733/3016` lines (`90.62%`),
  `flutter build apk --debug`, `flutter build apk --release`, `flutter build appbundle --release`,
  and `flutter test integration_test -d emulator-5554`.
- ATS detail formatter verification passed: fail-first `flutter test
  test/atlas_detail_formatter_test.dart` failed before the formatter API existed; after
  implementation, `flutter test test/atlas_detail_formatter_test.dart` passed with 10 formatter
  tests, and `flutter test test/widget_test.dart --plain-name "job detail renders populated fields
  and keeps diagnostics hidden"` passed.
- Latest full verification passed: `dart format --set-exit-if-changed .`, `dart analyze`,
  `flutter test --coverage` with 55 tests and `2956/3255` lines (`90.81%`), `flutter build apk
  --debug`, `flutter build apk --release`, `flutter build appbundle --release`, `flutter test
  integration_test -d emulator-5554`, and `.venv/bin/python -m pytest tests` with 7 passed and 1
  skipped.
- Search-top golden verification passed: fail-first `flutter test test/search_golden_test.dart`
  failed because `goldens/android/search_top_compact.png` did not exist; `flutter test
  --update-goldens test/search_golden_test.dart` generated the baseline; normal `flutter test
  test/search_golden_test.dart` passed; `dart format --set-exit-if-changed test/search_golden_test.dart`,
  `dart analyze`, and `flutter test --coverage` passed with 56 tests and `2956/3255` lines
  (`90.81%`).
- Filter-sheet golden verification passed: after isolating the existing Search fixture, fail-first
  `flutter test test/search_golden_test.dart` failed only because
  `goldens/android/filter_sheet_top.png` did not exist; `flutter test --update-goldens
  test/search_golden_test.dart` generated the baseline; normal `flutter test
  test/search_golden_test.dart`, `dart format --set-exit-if-changed test/search_golden_test.dart`,
  `dart analyze`, and `flutter test --coverage` passed with 57 tests and `2994/3255` lines
  (`91.98%`).
- Job Detail golden verification passed: fail-first `flutter test test/search_golden_test.dart`
  failed only because `goldens/android/job_detail_top.png` did not exist; `flutter test
  --update-goldens test/search_golden_test.dart` generated the baseline; normal `flutter test
  test/search_golden_test.dart`, `dart format --set-exit-if-changed test/search_golden_test.dart`,
  `dart analyze`, and `flutter test --coverage` passed with 58 tests and `2994/3255` lines
  (`91.98%`).
- Tab golden verification passed: fail-first `flutter test test/tab_golden_test.dart` failed only
  because `saved_tab.png`, `updates_tab.png`, `sources_tab.png`, and `settings_tab.png` did not
  exist; `flutter test --update-goldens test/tab_golden_test.dart` generated all four baselines;
  normal `flutter test test/tab_golden_test.dart`, `dart format --set-exit-if-changed
  test/tab_golden_test.dart`, `dart analyze`, and `flutter test --coverage` passed with 62 tests
  and `2994/3255` lines (`91.98%`).
- Location cascade golden verification passed: fail-first `flutter test
  test/search_golden_test.dart --name "filter sheet country cascade"` failed only because
  `goldens/android/filter_country_jpn.png` did not exist; `flutter test --update-goldens
  test/search_golden_test.dart --name "filter sheet (country|city) cascade"` generated
  `filter_country_jpn.png` and `filter_city_tokyo.png`; normal `flutter test
  test/search_golden_test.dart`, `dart analyze`, and `flutter test --coverage` passed with 64 tests
  and `3002/3255` lines (`92.23%`).
- Physical Pixel integration attempt: `flutter test integration_test -d 38281FDJG001DJ` built and
  installed the debug test APK, but the device-driven test did not complete after launch and was
  interrupted after `1:46`; the Pixel still reports keyguard/doze state, so this remains a physical
  automation blocker, not an app assertion failure.

Installed package evidence:

- Package: `com.yutsukioka.jobagg.atlas`
- Version: `versionCode=1`, `versionName=1.0.0`
- Device: `38281FDJG001DJ`
- Package `lastUpdateTime`: `2026-07-03 04:58:56` after rebuilding and installing the current
  branch release APK.

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
- iOS reference capture:
  - Built `apps/apple/PreviewHost` with a temporary SwiftPM scratch path to verify the preview host
    is buildable.
  - Built `apps/apple/AtlasIOSHost` for iOS Simulator with derived data under `/private/tmp`.
  - Booted iPhone 17 Pro Simulator, installed and launched `AtlasIOSHost`, and captured a fresh iOS
    Search-top reference screenshot.
  - Generated a fresh iOS Simulator vs Android Search side-by-side image for human review.
  - Added an environment-gated `ATLAS_REFERENCE_CAPTURE` harness in `AtlasIOSHost` and
    source-rendered iOS references for Search, Filter sheet, Job Detail, Saved, Updates, Sources,
    and Settings. Normal iOS app launches still use `AtlasRootView()`.

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

Current refreshed emulator screenshots after the cache-load banner fix are available under
`apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703-current/`:

- `search_initial.png`: before-fix capture from the same emulator session showing the large
  `Loaded local save from this device.` banner and stale `2,271` count.
- `search_startup_no_banner.png`: startup from persisted cache, compact status, no large local-save
  banner.
- `settings_before_refresh.png`: stale cached count before refresh (`2,271`).
- `settings_after_refresh.png`: refreshed live count (`2,269`) and reconciliation (`151`
  deadline-past open rows hidden by Search).
- `search_refreshed_no_banner.png`: refreshed Search top with `2,269 searchable results`, no
  deadline-passed rows at top, and no large banner.
- `offline_restart_no_banner.png`: offline restart with cached `2,269 searchable results` visible
  immediately and no large banner.

Source-badge parity screenshots are available under
`apps/atlas_flutter/docs/loop/screenshots/source-badge-parity-20260703/`:

- `search_badges.png`: clean emulator startup before local cache refresh.
- `search_badges_64bit.png`: refreshed Search results from the rebuilt release APK with
  Swift-style source-colored badges, current `2,268 searchable results`, and the Unicode-scalar
  source-color hash.
- `settings_after_reload_64bit.png`: live reload from the rebuilt release APK showing
  `2,268 searchable results`, `2,420` health open
  jobs, and `152` deadline-past open rows hidden by Search.
- `settings_before_reload_64bit.png`: emulator Settings state before the same refresh.

Detail formatter screenshots are available under
`apps/atlas_flutter/docs/loop/screenshots/detail-formatter-20260703/`:

- `settings_after_reload.png`: live reload from the rebuilt release APK showing `2,266 searchable
  results`, `2,420` health open jobs, and `154` deadline-past open rows hidden by Search.
- `search_after_relaunch.png`: Search top after relaunch with the current persisted count.
- `job_detail_top_fixed.png`: corrected Job Detail top view after formatter/rendering fixes; raw
  source data is absent from the main detail body and remains available through diagnostics.

Fresh iOS Simulator reference screenshots are available under
`apps/atlas_flutter/docs/loop/screenshots/ios-simulator-reference-20260703/`:

- `ios_search_top_simulator.png`: fresh Search-top screenshot from a locally built `AtlasIOSHost`
  running on the iPhone 17 Pro Simulator.
- `ios_simulator_android_search_side_by_side.png`: fresh iOS Simulator Search top beside the latest
  Android Search screenshot.

Expanded source-rendered iOS Simulator reference screenshots are available under
`apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/`:

- `ios_search_reference.png`: normal-state iOS Search reference with compact local-save status and
  no large cache-progress banner.
- `ios_filter_reference.png`: dark iOS Filter sheet reference with Done, sticky Reset/Apply,
  compact count pills, status/location/scope/contract/seniority sections, and explanatory text.
- `ios_detail_reference.png`: populated iOS Job Detail reference with apply/source/save controls,
  deadline panel, match details, and classification.
- `ios_saved_reference.png`, `ios_updates_reference.png`, `ios_sources_reference.png`, and
  `ios_settings_reference.png`: source-rendered iOS tab references for the implemented non-Search
  screens.

Source-rendered iOS Filter-section and cascade reference screenshots are available under
`apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/`:

- `ios_filter_location.png`: Location section with City, Country, Include uncertain matches, and
  Scope visible.
- `ios_filter_contract_seniority.png`: Contract and Seniority sections with compact count pills.
- `ios_filter_grade_ccog.png`: Grade and CCOG Family sections.
- `ios_filter_organizations_work_mode.png`: Organizations and Work Mode sections.
- `ios_filter_capability_tags.png`: Work Mode plus Capability Tags with query and selected chips.
- `ios_filter_japan_selected.png`: Country-selected cascade state.
- `ios_filter_tokyo_selected.png`: City-selected reverse cascade state.
- `ios_filter_entry_junior_selected.png`: Seniority-selected cascade state.
- `ios_filter_grade_selected.png`: Grade-selected reverse cascade state.

Generated iOS-vs-Android Filter side-by-side screenshots are available under
`apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/`:

- `filter_side_by_side_contact_sheet.png`: one-page review sheet for Location, Contract/Seniority,
  Grade/CCOG, Organizations/Work Mode, Capability Tags, Country -> City, City -> Country,
  Seniority -> Grade, and Grade-selected states. The Japan/Tokyo Android panes are cropped above
  the keyboard so the cascade state remains visible in the review sheet.
- `filter_location_ios_android_side_by_side.png`
- `filter_contract_seniority_ios_android_side_by_side.png`
- `filter_grade_ccog_ios_android_side_by_side.png`
- `filter_organizations_work_mode_ios_android_side_by_side.png`
- `filter_capability_tags_ios_android_side_by_side.png`
- `filter_japan_selected_ios_android_side_by_side.png`
- `filter_tokyo_selected_ios_android_side_by_side.png`
- `filter_entry_junior_selected_ios_android_side_by_side.png`
- `filter_grade_selected_ios_android_side_by_side.png`

Generated iOS-vs-Android primary-screen side-by-side screenshots are available under
`apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/`:

- `primary_side_by_side_contact_sheet.png`: one-page review sheet for Search, Job Detail, Saved,
  Updates, Sources, and Settings.
- `search_top_ios_android_side_by_side.png`
- `job_detail_ios_android_side_by_side.png`
- `saved_tab_ios_android_side_by_side.png`
- `updates_tab_ios_android_side_by_side.png`
- `sources_tab_ios_android_side_by_side.png`
- `settings_tab_ios_android_side_by_side.png`

Android golden baseline:

- `apps/atlas_flutter/test/goldens/android/search_top_compact.png`: Flutter golden for Search-top
  layout, compact control hierarchy, and dense result rows at a 393x852 viewport. This uses the
  Flutter test renderer, so it is a regression artifact rather than a replacement for human
  screenshots.
- `apps/atlas_flutter/test/goldens/android/filter_sheet_top.png`: Flutter golden for the dark
  filter-sheet top, compact option-grid/count layout, and sticky bottom actions at a 393x852
  viewport. This also uses the Flutter test renderer and should supplement, not replace, human
  screenshots.
- `apps/atlas_flutter/test/goldens/android/filter_country_jpn.png` and
  `apps/atlas_flutter/test/goldens/android/filter_city_tokyo.png`: Flutter goldens for keyboard-free
  Country -> City and City -> Country cascade states.
- `apps/atlas_flutter/test/goldens/android/job_detail_top.png`: Flutter golden for populated Job
  Detail top content, metadata chips, core rows, and formatted detail body at a 393x852 viewport.
  This is a regression artifact and not a replacement for physical Pixel/iOS paired screenshots.
- `apps/atlas_flutter/test/goldens/android/saved_tab.png`,
  `apps/atlas_flutter/test/goldens/android/updates_tab.png`,
  `apps/atlas_flutter/test/goldens/android/sources_tab.png`, and
  `apps/atlas_flutter/test/goldens/android/settings_tab.png`: Flutter goldens for the implemented
  non-placeholder Atlas tabs at a 393x852 viewport.

Dedicated review package:

- `apps/atlas_flutter/docs/loop/IOS_ANDROID_VISUAL_REVIEW.md`
- `apps/atlas_flutter/PR_REPORT.md`

Previously generated evidence remains available for Search top comparison:

- `apps/atlas_flutter/docs/loop/screenshots/ios-reference/iteration-8/ios_search_top.png`
- `apps/atlas_flutter/docs/loop/screenshots/iteration-8/search_top_ios_android_side_by_side.png`

The remaining screenshot gaps are physical Pixel in-app evidence after the device is unlocked and
exact user-provided iOS screenshot files for pixel-paired comparison. Local source-rendered iOS
references now exist for the main review screens plus the major filter sections and cascade states,
and generated side-by-side Filter plus primary-screen comparisons are available for human review.

## Manual Emulator Evidence

- Release APK installed on `emulator-5554`.
- Server available at `http://10.253.1.43:8765`; Settings `Save and Reload` refreshed and persisted
  `2,266` cached searchable rows in the latest detail-formatter pass.
- Emulator networking was disabled with `svc wifi disable` and `svc data disable`.
- App was force-stopped and relaunched while offline.
- `offline_restart_no_banner.png` shows cached Search rows immediately with the prior `2,269`
  searchable-results cache and local save timestamp instead of an empty screen or large normal-state
  banner. The current live cache evidence is `search_after_relaunch.png` with `2,266 searchable
  results`.
- Search/filter/sort rows remained visible from cache. Full offline interaction depth is covered by
  controller/cache tests; physical offline interaction remains a human-review item.

## Current Remaining Gaps

- Physical in-app screenshots for the new cache/filter/icon slice are missing due to lock-screen
  blocker, though emulator screenshots are now captured.
- Fresh Pixel check on 2026-07-03 still shows the lock screen while Atlas is resumed underneath
  keyguard.
- Search, Filter sheet, the major Filter sections/cascade states, Job Detail, Saved, Updates,
  Sources, and Settings now have source-rendered iOS Simulator reference screenshots. True
  pixel-paired human review still needs local copies of the user-provided iOS screenshots and
  physical Android captures.
- Search-top, filter-sheet top, populated Job Detail top, and implemented Saved/Updates/Sources/
  Settings tabs now have Android golden regression tests. Physical Pixel and iOS reference review
  are still required.
- Local Android supports multiple City/Country values through comma-separated input and multi-select
  filter pills. User-facing visual review is still needed to decide whether the comma text display is
  close enough to iOS or should become a dedicated selected-chip editor.
- The generated Filter side-by-side contact sheet makes the current Android cascade behavior
  reviewable against local iOS references. The Japan/Tokyo side-by-side panes are cropped above the
  keyboard, and the keyboard-free selected states are also covered by Flutter golden baselines.
  Physical recapture should still include full-screen non-keyboard states.
- The generated primary-screen side-by-side contact sheet now pairs Search, Job Detail, Saved,
  Updates, Sources, and Settings against local source-rendered iOS references. This improves local
  reviewability but still does not replace physical Pixel screenshots.
- Backend facets still do not expose city/country or grade-to-seniority metadata. Android computes
  those facets locally from cached Search rows.
- Normal cache-load banner was removed after screenshot review found `Loaded local save from this
  device.` occupying the Search result area.
- Source badges were changed from generic pale-cyan blocks to deterministic source colors with white
  monograms, matching Swift `SourceMonogram`.
- Coverage remains strong at `92.23%` after adding Search, filter-sheet, location-cascade,
  Job Detail, and tab golden coverage.
- The Tokyo reverse-cascade screenshot shows selected `TOKYO` with zero same-group count and `JPN`
  visible as the matching country option. This matches the "selected values remain visible" rule but
  should be reviewed for whether the displayed same-group count is the desired product copy.
- Physical Pixel offline restart still needs human verification after the device is unlocked.

## Scorecard

Strict score under the new user caps: **84/100**.

The local iOS evidence gap is reduced because source-rendered iOS references now exist for Search,
Filter sheet sections/cascades, Job Detail, Saved, Updates, Sources, and Settings, with generated
iOS-vs-Android side-by-side Filter and primary-screen comparisons. The score remains below final
completion because physical Pixel in-app screenshots/offline restart and exact user-provided iOS
screenshot pairing are still not complete.

| Category | Score | Notes |
| --- | ---: | --- |
| Persistent cache | 18 / 20 | File cache persists broad Search rows; emulator offline restart shows cached results immediately; physical offline restart still pending. |
| Filter parity | 26 / 30 | All iOS groups render in a dark sheet with counts/dimming and sticky actions; multiple City/Country values now serialize and filter locally as OR selections. |
| Icon parity | 8 / 10 | Visible controls route through shared Cupertino-style `AtlasIcons`; source monograms now match Swift color treatment; human iOS screenshot comparison still pending. |
| Existing Search/data/detail/tabs | 14 / 15 | Count reconciliation, compact rows, Updates/Sources, Saved, populated Detail, persistent cached details, and formatted ATS details remain intact; live Search count moved to 2,266 due deadline timing. |
| Tests/builds | 15 / 15 | Format, analyze, focused Search/filter golden tests, full Flutter tests with coverage, debug APK, release APK, release AAB, and current emulator integration pass; Pixel integration was attempted but blocked by device state. |
| Evidence/human readiness | 7 / 10 | Emulator evidence, Android contact sheet, source-rendered iOS references for primary screens and Filter subsections/cascade states, Search/Filter/primary-screen side-by-side comparisons are captured; physical Pixel app screenshots and exact user-provided iOS screenshot pairing still need final human review. |

## Next Required Human Action

Unlock the connected Pixel 8 Pro, then rerun physical screenshot capture and offline-restart
verification. Do not claim 80%+ completion until those screenshots prove the Android UI/behavior is
close to the iOS reference.
