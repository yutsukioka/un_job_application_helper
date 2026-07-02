# Atlas Flutter Android PR Report

Status: draft current-state report, not final completion.

Branch: `codex/atlas-flutter-android-parity`  
Latest report update: 2026-07-03  
Latest branch head before this report update: `22114fa`

## Summary

The Android Flutter app has moved beyond the initial Search-only parity slice. It now includes a
persistent local cache, offline startup behavior on emulator, an iOS-style Search screen, full
filter groups, City/Country and Seniority/Grade cascades, multi-value City/Country filter support,
compact result rows, populated and persistently cached Job Detail, Saved/Updates/Sources/Settings
tabs, a Dart port of the Swift ATS detail formatter, a fresh iOS Simulator Search-top side-by-side,
Android Search-top, filter-sheet top, Job Detail top, and implemented-tab goldens, and a reviewable
Android screenshot package.

This is still not a completion claim. Physical Pixel 8 Pro in-app screenshots and offline restart
verification remain blocked by the connected phone being locked. Search, Filter sheet, Job Detail,
Saved, Updates, Sources, and Settings now have source-rendered iOS Simulator references from the
real Swift screens, but final pixel-paired review against the exact user-provided iOS screenshots
remains blocked because those screenshots are not available as local files in this worktree.

## Scope Implemented

- Search top area aligned with iOS structure: centered `Search`, grouped filter/bookmark controls,
  search input, active chips, compact count/status row, and sort control.
- Compact job rows hide diagnostic explanations from Search results.
- Persistent file-backed local cache loads before network refresh and supports offline cached Search.
- Fetched Job Detail payloads are persisted in the same local cache and can be reopened offline after
  restart.
- Search startup from cache no longer shows a large normal-state local-save banner; status is compact
  under the result count.
- Search result source badges now match Swift `SourceMonogram` treatment with deterministic
  per-source color blocks and white initials.
- Settings exposes server URL, cache status, refresh, and clear-cache controls.
- Filter sheet implements Status, Location, Scope, Contract, UN Volunteer Category, Seniority,
  Grade, CCOG Family, Organizations, Work Mode, and Capability Tags.
- City/Country filters support multiple values and OR matching within Location.
- Seniority/Grade facets cascade from cached data using `standard_seniority_tier`.
- Updates tab shows refresh status, count reconciliation, local save, backend snapshot, and recent runs.
- Sources tab shows source health and allows source filtering.
- Job Detail shows core fields, full description, detail sections, apply/source links, save state,
  weak-detail state, and diagnostics behind an expansion panel.
- Job Detail now formats ATS/PageUp sections instead of dumping raw section text: ATS chrome is
  hidden, fact runs become compact rows, bullet/numbered lists become structured blocks, orphan
  fragments are healed, and raw/source-data sections stay behind diagnostics.

## Data Count Reconciliation

Last verified reconciliation from current app/API evidence:

- `health_open_jobs`: `2,420`
- `search_api_total`: `2,266`
- Android displayed count after current refresh: `2,266 searchable results`
- Difference: `154` deadline-past rows still counted by health as open but hidden by Search because
  default Search uses `exclude_expired_open=true`.

Current live refresh status:

- `curl http://10.253.1.43:8765/api/health` succeeded on 2026-07-03 and reported
  `open_jobs=2420`.
- Default `/api/search` with Android open/searchable filters reported `total=2266`.

## Evidence

Primary review docs:

- `apps/atlas_flutter/docs/loop/ANDROID_SEARCH_UI_AUDIT.md`
- `apps/atlas_flutter/docs/loop/IOS_ANDROID_VISUAL_REVIEW.md`
- `apps/atlas_flutter/docs/loop/STATUS.jsonl`
- `apps/atlas_flutter/docs/loop/PHYSICAL_PIXEL_VERIFICATION.md`

Screenshot evidence:

- Android contact sheet:
  `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703/android_review_contact_sheet.png`
- Search top side-by-side:
  `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703/ios_android_search_top_side_by_side.png`
- Offline cached startup:
  `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703/offline_restart_cached.png`
- Current no-banner/cache refresh evidence:
  `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703-current/`
- Source badge parity evidence:
  `apps/atlas_flutter/docs/loop/screenshots/source-badge-parity-20260703/search_badges_64bit.png`
- Detail formatter evidence:
  `apps/atlas_flutter/docs/loop/screenshots/detail-formatter-20260703/job_detail_top_fixed.png`
- Current live count evidence:
  `apps/atlas_flutter/docs/loop/screenshots/detail-formatter-20260703/settings_after_reload.png`
- Fresh iOS Simulator Search-top side-by-side:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-reference-20260703/ios_simulator_android_search_side_by_side.png`
- Fresh iOS Simulator Search-top reference:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-reference-20260703/ios_search_top_simulator.png`
- Expanded source-rendered iOS Simulator references:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_search_reference.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_filter_reference.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_detail_reference.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_saved_reference.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_updates_reference.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_sources_reference.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_settings_reference.png`
- Source-rendered iOS filter-section/cascade references:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_location.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_contract_seniority.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_grade_ccog.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_organizations_work_mode.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_capability_tags.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_japan_selected.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_tokyo_selected.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_entry_junior_selected.png`
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_grade_selected.png`
- Android Search-top golden:
  `apps/atlas_flutter/test/goldens/android/search_top_compact.png`
- Android filter-sheet top golden:
  `apps/atlas_flutter/test/goldens/android/filter_sheet_top.png`
- Android Job Detail top golden:
  `apps/atlas_flutter/test/goldens/android/job_detail_top.png`
- Android implemented-tab goldens:
  `apps/atlas_flutter/test/goldens/android/saved_tab.png`
  `apps/atlas_flutter/test/goldens/android/updates_tab.png`
  `apps/atlas_flutter/test/goldens/android/sources_tab.png`
  `apps/atlas_flutter/test/goldens/android/settings_tab.png`
- Filter, cascade, detail, settings, saved, updates, and sources screenshots:
  `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703/`
- Physical Pixel lock-screen evidence:
  `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/resume_visibility_check.png`

## Verification Snapshot

Latest app-code verification:

- `dart format --set-exit-if-changed .` passed.
- `dart analyze` passed.
- `flutter test --coverage` passed with 55 tests: `2956/3255` lines, `90.81%`.
- `flutter build apk --debug` passed.
- `flutter build apk --release` passed.
- `flutter build appbundle --release` passed.
- `flutter test integration_test -d emulator-5554` passed after simplifying the device smoke test to
  launch plus primary tab navigation. Filter/sort/modal behavior remains covered by widget tests.
- `flutter test integration_test -d 38281FDJG001DJ` built and installed the debug test APK, but the
  device-driven test did not complete after launch and was interrupted after `1:46`; the connected
  Pixel still reports keyguard/doze state.
- `swift build --scratch-path /private/tmp/atlas-previewhost-build` passed for
  `apps/apple/PreviewHost`.
- `xcodebuild -project apps/apple/AtlasIOSHost/AtlasIOSHost.xcodeproj -scheme AtlasIOSHost
  -destination 'platform=iOS Simulator,id=0146410E-539C-43FF-BAE6-159D9E27006D'
  -derivedDataPath /private/tmp/atlas-ioshost-derived CODE_SIGNING_ALLOWED=NO build` passed.
- `xcrun simctl` boot/install/launch/screenshot captured the fresh iOS Simulator Search reference.
- `swift build --scratch-path /private/tmp/atlas-swift-build` passed after adding the
  source-rendered reference capture harness.
- `xcodebuild -project apps/apple/AtlasIOSHost/AtlasIOSHost.xcodeproj -scheme AtlasIOSHost
  -destination 'platform=iOS Simulator,id=0146410E-539C-43FF-BAE6-159D9E27006D'
  -derivedDataPath /private/tmp/atlas-ioshost-derived CODE_SIGNING_ALLOWED=NO build` passed again.
- `xcrun simctl` installed and launched `AtlasIOSHost` with `SIMCTL_CHILD_ATLAS_REFERENCE_CAPTURE`
  modes for Search, Filter sheet, Job Detail, Saved, Updates, Sources, and Settings; screenshots
  were captured under `screenshots/ios-simulator-expanded-20260703/`.
- `swift build --scratch-path /private/tmp/atlas-swift-build` passed after adding scroll-targeted
  filter-section capture modes.
- `xcodebuild -project apps/apple/AtlasIOSHost/AtlasIOSHost.xcodeproj -scheme AtlasIOSHost
  -destination 'platform=iOS Simulator,id=0146410E-539C-43FF-BAE6-159D9E27006D'
  -derivedDataPath /private/tmp/atlas-ioshost-derived CODE_SIGNING_ALLOWED=NO build` passed after
  adding scroll-targeted filter-section capture modes.
- `xcrun simctl` launched `AtlasIOSHost` with the new `SIMCTL_CHILD_ATLAS_REFERENCE_CAPTURE`
  filter-section and cascade modes; nine 1206x2622 PNG screenshots were captured under
  `screenshots/ios-simulator-filter-sections-20260703/`.
- `flutter test test/search_golden_test.dart` passed after generating
  `test/goldens/android/search_top_compact.png`, `test/goldens/android/filter_sheet_top.png`, and
  `test/goldens/android/job_detail_top.png`.
- `flutter test test/tab_golden_test.dart` passed after generating `saved_tab.png`,
  `updates_tab.png`, `sources_tab.png`, and `settings_tab.png`.
- Current `flutter test --coverage` passed with 62 tests: `2994/3255` lines, `91.98%`.

Latest artifacts:

- Release APK:
  `apps/atlas_flutter/build/app/outputs/flutter-apk/app-release.apk`
- Release AAB:
  `apps/atlas_flutter/build/app/outputs/bundle/release/app-release.aab`

Latest physical install evidence:

- Device: Pixel 8 Pro `38281FDJG001DJ`
- Package: `com.yutsukioka.jobagg.atlas`
- Last installed release APK timestamp: `2026-07-03 04:58:56`

## Remaining Gaps

- Physical Pixel in-app screenshots are missing because the device remains locked.
- Physical offline restart with cached data visible is not yet human-verified.
- Human G3 approval is pending.
- Source-rendered iOS references now exist for primary screens and the major Filter
  section/cascade states, but exact user-provided iOS screenshots are not available as local files
  for final pixel-paired side-by-side review.
- Additional Android scrolled-state or component goldens may still be useful after physical/iOS
  review, but primary Search, filter-sheet, Job Detail, Saved, Updates, Sources, and Settings
  baselines now exist.
- Android multi-location filter display uses comma-separated text plus selected pills; human review
  should decide whether this is visually close enough to the iOS reference.
- Backend does not expose full server-side cascade/facet metadata for City/Country or
  Grade/Seniority; Android computes those facets locally from cached rows.

## Required Closeout Actions

1. Unlock Pixel 8 Pro `38281FDJG001DJ` and keep the screen awake.
2. Start or restore the local API at `http://10.253.1.43:8765`.
3. Follow `apps/atlas_flutter/docs/loop/PHYSICAL_PIXEL_VERIFICATION.md`.
4. Capture the required physical screenshots into
   `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/`.
5. Re-run live count reconciliation against `/api/health` and `/api/search`.
6. Copy the user-provided iOS screenshots into the repo or provide accessible paths, then update
   `IOS_ANDROID_VISUAL_REVIEW.md` with true iOS-vs-Android pairs against those exact images.
7. Post final report evidence as a PR comment and wait for `APPROVED: G3`.

## Honest Score

Current strict score: `83/100`.

The score improves because source-rendered iOS references now exist for the primary screens plus
the major Filter section/cascade states. It is still capped below final parity because physical
Pixel in-app evidence and exact user-provided iOS side-by-side review are still missing. Do not
claim final Android parity completion until those gates are satisfied.
