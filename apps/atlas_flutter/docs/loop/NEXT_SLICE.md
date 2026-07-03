# Next Slice

Gate state: implementation for persistent cache, full iOS-style filter groups, multi-value
City/Country cascade, Seniority/Grade cascade, and core Cupertino-style icon mapping is in place.
The Android launcher icon now uses the Apple Atlas icon artwork across Android mipmap densities.
Emulator release-app screenshots, source-rendered iOS Simulator references, filter-section/cascade
iOS Simulator references, generated Filter and primary-screen side-by-side comparisons,
keyboard-free location-cascade goldens, offline restart evidence, and Android
Search/filter-sheet/Job Detail/tab golden baselines are captured. PR #10 remains below completion
because physical Pixel in-app screenshots, physical offline-restart verification, and exact
user-provided iOS screenshot pairing are still pending.

## Intent

Capture human-reviewable evidence on the unlocked Pixel 8 Pro, add local copies of the
user-provided iOS screenshots if available, complete the final pixel-paired iOS-vs-Android review
package, and fix any visible regressions found from that evidence. The latest debug APK is already
installed on the connected Pixel with the updated Apple Atlas launcher icon and startup-cache
first-frame fix. Do not start broad backend or JobAgg lifecycle work.

## Acceptance Tests

- Unlock Pixel 8 Pro `38281FDJG001DJ`.
- Confirm `mDreamingLockscreen=false`; the latest check still showed the lock screen while Atlas was
  resumed underneath keyguard.
- Launch installed release APK `com.yutsukioka.jobagg.atlas`.
- With server available, refresh Search and confirm cached dataset is written.
- Kill app, disable/stop server or make it unreachable, relaunch app, and confirm cached Search rows
  appear immediately with `Offline (cached)`/stale state.
- Verify Search, filter sheet, filter chips, sort, saved state, Job Detail, Updates, Sources, and
  Settings still work from cached or live data as appropriate.
- Capture physical Pixel screenshots:
  - Search top and scrolled
  - Filter sheet top
  - Filter Location, Contract, Seniority, Grade, CCOG, Organizations, Work Mode, Capability Tags
  - Filter sheet with Japan selected
  - Filter sheet with Tokyo selected
  - Filter sheet with Entry Junior selected
  - Filter sheet with grade `P1` selected if present
  - Offline restart with cached data visible
  - Settings cache status
  - Job Detail, Saved, Updates, Sources
- Update `ANDROID_SEARCH_UI_AUDIT.md` with physical screenshot paths and human-visible differences.
- Run `dart format --set-exit-if-changed .`, `dart analyze`, `flutter test --coverage`, and the
  Android release build after any visual fixes.

## Known Remaining Technical Gaps

| Gap | Current state | Required next action |
| --- | --- | --- |
| Physical screenshot evidence | Emulator screenshots exist; Pixel capture still shows lock screen only. | Unlock device and capture post-fix app screenshots. |
| Physical offline restart | Covered by controller/cache tests and emulator screenshot evidence, not physical screenshot evidence. | Perform manual USB Pixel restart/offline flow and capture screenshot. |
| Physical capture runbook | Added `PHYSICAL_PIXEL_VERIFICATION.md` with commands, required screenshots, and pass/fail gates. | Use it after unlocking the Pixel. |
| iOS side-by-side package | Source-rendered iOS Simulator references now exist for Search, Filter sheet, Filter section/cascade states, Job Detail, Saved, Updates, Sources, and Settings. Generated Filter and primary-screen side-by-side comparisons exist. Exact user-provided iOS screenshots are not available as local files. | Add/copy the user-provided iOS screenshots, then build final side-by-side review package after physical Android captures. |
| Android goldens | Search-top, filter-sheet top, populated Job Detail top, Saved, Updates, Sources, and Settings goldens exist under `test/goldens/android/`. | Use physical Pixel/iOS review to decide whether additional scrolled-state or component goldens are needed. |
| Multiple city/country selections | Android now supports comma-separated text input plus multi-select pills for multiple cities/countries; values serialize to Search API list fields and filter cached rows as OR within Location. | Human-review whether the comma text display is visually close enough to iOS or should become a dedicated selected-chip editor. |
| Cascade screenshot keyboard | Generated side-by-side comparisons now crop the Android Japan/Tokyo panes above the keyboard, and keyboard-free cascade goldens exist. Source emulator screenshots still include keyboard. | Recapture cleaner full-screen physical/emulator cascade states with keyboard dismissed after the Pixel is unlocked. |
| Backend location/grade facet metadata | Android computes city/country and grade/seniority facets locally from cached rows. | Add smallest API facet metadata only if server-side full-dataset counts are required. |
| Coverage | 92.24% after launcher-icon, Search, filter-sheet, location-cascade, Job Detail, and tab golden coverage. | Add screenshot/widget tests for any follow-up UI fixes; do not claim completion from coverage alone. |
| Integration test | Passed on `emulator-5554` after updating the smoke test for the new `Done` filter-sheet control; latest physical capture is blocked by lock screen, not by an app assertion. | Keep this green after any physical-review fixes. |
| Launcher icon | Android launcher mipmaps now use the Apple Atlas icon and are covered by `test/android_launcher_icon_test.dart`. | Human-review the launcher icon on the unlocked Pixel home/app drawer after install. |

## Last Verification Snapshot

- Format: pass.
- Analyze: pass.
- Full tests: pass, 65 tests after launcher-icon, Search-top, filter-sheet, location-cascade,
  Job Detail, and tab golden coverage.
- Coverage: pass, `3009/3262` lines, `92.24%`.
- Debug APK: pass.
- Release AAB: pass, `build/app/outputs/bundle/release/app-release.aab`.
- Release APK: pass, `build/app/outputs/flutter-apk/app-release.apk`.
- USB Pixel install: pass, latest debug APK installed with updated launcher icon; release APK and
  AAB were also rebuilt successfully.
- Emulator integration: pass, `flutter test integration_test -d emulator-5554`.
- Emulator offline restart: pass, cached Search showed results immediately from local save.
- Current live Android/Search API count evidence: `2,266 searchable results` with `2,420`
  health `open_jobs`.
- Fresh iOS Simulator Search side-by-side review:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-reference-20260703/ios_simulator_android_search_side_by_side.png`.
- Expanded source-rendered iOS references:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_search_reference.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_filter_reference.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_detail_reference.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_saved_reference.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_updates_reference.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_sources_reference.png`, and
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-expanded-20260703/ios_settings_reference.png`.
- Source-rendered iOS filter-section/cascade references:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_location.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_contract_seniority.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_grade_ccog.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_organizations_work_mode.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_capability_tags.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_japan_selected.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_tokyo_selected.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_entry_junior_selected.png`, and
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-filter-sections-20260703/ios_filter_grade_selected.png`.
- Generated iOS-vs-Android Filter side-by-side package:
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_side_by_side_contact_sheet.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_location_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_contract_seniority_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_grade_ccog_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_organizations_work_mode_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_capability_tags_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_japan_selected_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_tokyo_selected_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_entry_junior_selected_ios_android_side_by_side.png`, and
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-filter-side-by-side-20260703/filter_grade_selected_ios_android_side_by_side.png`.
- Generated iOS-vs-Android primary-screen side-by-side package:
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/primary_side_by_side_contact_sheet.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/search_top_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/job_detail_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/saved_tab_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/updates_tab_ios_android_side_by_side.png`,
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/sources_tab_ios_android_side_by_side.png`, and
  `apps/atlas_flutter/docs/loop/screenshots/ios-android-primary-side-by-side-20260703/settings_tab_ios_android_side_by_side.png`.
- Keyboard-free Android location-cascade goldens:
  `apps/atlas_flutter/test/goldens/android/filter_country_jpn.png` and
  `apps/atlas_flutter/test/goldens/android/filter_city_tokyo.png`.
- Android golden baselines:
  `apps/atlas_flutter/test/goldens/android/search_top_compact.png`,
  `apps/atlas_flutter/test/goldens/android/filter_sheet_top.png`, and
  `apps/atlas_flutter/test/goldens/android/job_detail_top.png`,
  `apps/atlas_flutter/test/goldens/android/saved_tab.png`,
  `apps/atlas_flutter/test/goldens/android/updates_tab.png`,
  `apps/atlas_flutter/test/goldens/android/sources_tab.png`, and
  `apps/atlas_flutter/test/goldens/android/settings_tab.png`.
