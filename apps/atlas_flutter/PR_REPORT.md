# Atlas Flutter Android PR Report

Status: draft current-state report, not final completion.

Branch: `codex/atlas-flutter-android-parity`  
Latest report update: 2026-07-03  
Latest app-code head covered: `12f0599`
Completion audit: `apps/atlas_flutter/docs/loop/COMPLETION_AUDIT.md`

## Summary

The Android Flutter app now includes persistent local cache, offline startup behavior, iOS-style
Search, full filter groups, City/Country and Seniority/Grade cascades, multi-value location filters,
compact result rows, Saved/Updates/Sources/Settings tabs, populated and cached Job Detail, a Dart
port of the Swift ATS detail formatter, Apple Atlas Android launcher mipmaps, Android goldens, and
source-rendered iOS Simulator reference captures.

The latest physical Pixel pass found two real defects and both were fixed:

- Search ANR: caused by eagerly building more than 2,000 result rows during controller rebuilds.
  Fixed with lazy `ListView.builder` construction and covered by a widget regression test.
- Cached `Open only` mismatch: cached/offline filtering showed past-deadline open rows that the
  Search API excludes with `exclude_expired_open=true`. Fixed so cached filtering mirrors the API.

This is still not a final completion claim. Physical screenshots exist for Search, scrolled Search,
Filter sheet, Sort menu, Saved, Updates, Sources, and Settings, but corrected physical Job Detail
capture and physical offline-restart verification remain pending because the Pixel was detached
before recapture. Human G3 approval is also still pending.

## Scope Implemented

- Search top area aligned with iOS structure: centered `Search`, grouped filter/bookmark controls,
  search input, active chips, compact count/status row, sort control, and compact list rows.
- Persistent file-backed cache loads before network refresh and keeps cached Search rows usable
  offline.
- Fetched Job Detail payloads persist in the local cache and can be reopened offline after restart.
- Full filter sheet groups render: Status, Location, Scope, Contract, UN Volunteer Category,
  Seniority, Grade, CCOG Family, Organizations, Work Mode, and Capability Tags.
- City/Country and Seniority/Grade cascade behavior is implemented and tested.
- Updates tab shows refresh status, count reconciliation, local save, backend snapshot, and recent
  runs.
- Sources tab shows source health and supports source filtering.
- Job Detail shows core fields, full description, detail sections, apply/source links, save state,
  weak-detail state, and diagnostics behind an expansion panel.
- ATS/PageUp detail text is formatted into structured content instead of dumping raw source payloads
  into the main detail body.
- Android launcher mipmaps now use `apps/apple/Design/AppIcon/AppIcon-iOS-1024.png`.
- Source badges now match the Swift `SourceMonogram` treatment with deterministic source colors and
  white initials.

## Data Count Reconciliation

Last verified reconciliation from current app/API/physical Settings evidence:

- `health_open_jobs`: `2,452`
- `search_api_total`: `2,355`
- `android_displayed_total`: `2,355 searchable results`
- `active_filters`: default open/searchable Search, no text query, sort `closing_date_asc`,
  Search API default `exclude_expired_open=true`
- `local_cache_timestamp`: physical Settings showed `Last updated 2026-07-03 13:26`,
  `Cache status Fresh`, and `Cached jobs 2,355`
- `backend_snapshot_timestamp`: `/api/health last_sync_at=2026-07-03T04:17:28.239578+00:00`
- `excluded_count`: `97`
- `excluded_reason_breakdown`: rows still counted by health as open but with passed deadlines,
  intentionally hidden by Search API `exclude_expired_open=true`
- `final_decision`: `2,355` is the correct Android default Search count for this snapshot because
  it matches POST `/api/search`; `2,452` is the raw health open count.

The count is time-sensitive because more deadlines can pass between refreshes. The app label remains
`searchable results` to distinguish Search results from raw health `open_jobs`.

## Evidence

Primary review docs:

- `apps/atlas_flutter/docs/loop/ANDROID_SEARCH_UI_AUDIT.md`
- `apps/atlas_flutter/docs/loop/IOS_ANDROID_VISUAL_REVIEW.md`
- `apps/atlas_flutter/docs/loop/STATUS.jsonl`
- `apps/atlas_flutter/docs/loop/PHYSICAL_PIXEL_VERIFICATION.md`

Physical Pixel screenshots captured before detach:

- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/search_top_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/search_scrolled_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/filter_sheet_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/sort_menu_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/saved_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/updates_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/sources_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/settings_tab_final.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/anr_visible_latest.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/anr_fix_sort_check.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/open_only_cache_deadline_fix.png`

The untracked `physical-pixel-20260703/job_detail_final.png` is not valid evidence because it is a
launcher/app-drawer capture, not a Job Detail screen.

iOS and side-by-side references:

- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-reference-20260703/ios_simulator_android_search_side_by_side.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_search_reference.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_filter_reference.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_detail_reference.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_saved_reference.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_updates_reference.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_sources_reference.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_settings_reference.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_side_by_side_contact_sheet.png`
- `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/primary_side_by_side_contact_sheet.png`

Android golden baselines:

- `apps/atlas_flutter/test/goldens/android/search_top_compact.png`
- `apps/atlas_flutter/test/goldens/android/filter_sheet_top.png`
- `apps/atlas_flutter/test/goldens/android/filter_country_jpn.png`
- `apps/atlas_flutter/test/goldens/android/filter_city_tokyo.png`
- `apps/atlas_flutter/test/goldens/android/job_detail_top.png`
- `apps/atlas_flutter/test/goldens/android/saved_tab.png`
- `apps/atlas_flutter/test/goldens/android/updates_tab.png`
- `apps/atlas_flutter/test/goldens/android/sources_tab.png`
- `apps/atlas_flutter/test/goldens/android/settings_tab.png`

## Verification Snapshot

Latest app-code verification after the ANR and cached deadline fixes:

- `dart analyze` passed.
- `flutter test test/widget_test.dart` passed with the lazy Search result-row regression.
- `flutter test test/atlas_search_controller_test.dart` passed with cached expired-open filtering
  coverage.
- `flutter test --coverage` passed with 67 tests.
- `flutter build apk --release` passed:
  `apps/atlas_flutter/build/app/outputs/flutter-apk/app-release.apk` (`50.8MB`).
- `adb install -r build/app/outputs/flutter-apk/app-release.apk` returned `Success` on Pixel 8 Pro
  `38281FDJG001DJ` before the device was detached.
- Physical sort interaction passed: `Sort: Closing soon` opened and no ANR dialog appeared after
  waiting beyond Android's 5-second input timeout.
- PR #10 checks were green after the latest push: `GitGuardian Security Checks` and `python` passed.
- Thread-aware Copilot review query found no current non-outdated unresolved actionable threads.

Previously verified and still relevant:

- `dart format --set-exit-if-changed .` passed.
- `flutter build apk --debug` passed.
- `flutter build appbundle --release` passed:
  `apps/atlas_flutter/build/app/outputs/bundle/release/app-release.aab`.
- `flutter test integration_test -d emulator-5554` passed.
- `swift build` and `xcodebuild` passed for the iOS reference capture harness.

## Latest Artifacts

- Release APK:
  `apps/atlas_flutter/build/app/outputs/flutter-apk/app-release.apk`
- Release AAB:
  `apps/atlas_flutter/build/app/outputs/bundle/release/app-release.aab`
- Installed package before detach: `com.yutsukioka.jobagg.atlas`
- Device: Pixel 8 Pro `38281FDJG001DJ`
- Package `lastUpdateTime`: `2026-07-03 04:58:56`

## Remaining Gaps

- Corrected physical Job Detail screenshot is pending.
- Physical offline restart with cached data visible is pending after the final cache/deadline fix.
- Human G3 physical-device approval is pending.
- Exact user-provided iOS screenshots are not available as local files for final pixel-paired
  comparison; source-rendered iOS Simulator references are available.
- Android multi-location filter display uses comma-separated text plus selected pills; human review
  should decide whether this is visually close enough to iOS or should become a dedicated
  selected-chip editor.
- Backend does not expose full server-side cascade/facet metadata for City/Country or
  Grade/Seniority; Android computes those facets locally from cached rows.

## Required Closeout Actions

1. Reconnect and unlock Pixel 8 Pro `38281FDJG001DJ`.
2. Start or restore the local API at `http://10.253.1.43:8765`.
3. Follow `apps/atlas_flutter/docs/loop/PHYSICAL_PIXEL_VERIFICATION.md`.
4. Capture corrected physical Job Detail and offline-restart screenshots into
   `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/`.
5. Re-run live count reconciliation against `/api/health` and `/api/search`.
6. Copy the user-provided iOS screenshots into the repo or provide accessible paths if exact
   user-screenshot pairing is required.
7. Post final report evidence as a PR comment and wait for `APPROVED: G3`.

## Honest Score

Current strict score: `86/100`.

The score reflects implemented cache/filter/icon/Search/data/detail/tab work, green automated
verification, source-rendered iOS references, side-by-side packages, and partial physical Pixel
evidence. It remains below final parity because corrected physical Job Detail, physical offline
restart, exact user-provided iOS screenshot pairing, and human G3 approval are still incomplete.
