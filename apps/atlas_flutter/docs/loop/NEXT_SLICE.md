# Next Slice

Gate state: implementation for persistent cache, full iOS-style filter groups, multi-value
City/Country cascade, Seniority/Grade cascade, and core Cupertino-style icon mapping is in place.
Emulator release-app screenshots, a fresh iOS Simulator Search-top side-by-side review, and offline
restart evidence are captured. PR #10 remains below completion because physical Pixel in-app
screenshots, physical offline-restart verification, and full iOS filter/detail side-by-side review
are still pending.

## Intent

Capture human-reviewable evidence on the unlocked Pixel 8 Pro, add local copies of the
user-provided iOS filter/detail screenshots if available, complete the final iOS-vs-Android
side-by-side review package, and fix any visible regressions found from that evidence. Do not start
broad backend or JobAgg lifecycle work.

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
| iOS side-by-side package | Fresh Search-top side-by-side exists in `IOS_ANDROID_VISUAL_REVIEW.md` from `AtlasIOSHost` on iPhone 17 Pro Simulator; full user-provided iOS filter/detail screenshots are not available as local files. | Add/copy local iOS filter/detail references, then build final side-by-side review package after physical Android captures. |
| Android goldens | Search-top compact layout golden exists at `test/goldens/android/search_top_compact.png`. | Add filter sheet, detail, and tab goldens only after the physical/iOS reference evidence confirms the target layouts. |
| Multiple city/country selections | Android now supports comma-separated text input plus multi-select pills for multiple cities/countries; values serialize to Search API list fields and filter cached rows as OR within Location. | Human-review whether the comma text display is visually close enough to iOS or should become a dedicated selected-chip editor. |
| Backend location/grade facet metadata | Android computes city/country and grade/seniority facets locally from cached rows. | Add smallest API facet metadata only if server-side full-dataset counts are required. |
| Coverage | 90.81% after ATS detail formatter coverage. | Add screenshot/widget tests for any follow-up UI fixes; do not claim completion from coverage alone. |
| Integration test | Passed on `emulator-5554` after updating the smoke test for the new `Done` filter-sheet control. | Keep this green after any physical-review fixes. |

## Last Verification Snapshot

- Format: pass.
- Analyze: pass.
- Full tests: pass, 56 tests after Search-top golden coverage.
- Coverage: pass, `2956/3255` lines, `90.81%`.
- Debug APK: pass.
- Release AAB: pass, `build/app/outputs/bundle/release/app-release.aab`.
- Release APK: pass, `build/app/outputs/flutter-apk/app-release.apk`.
- USB Pixel install: pass, `lastUpdateTime=2026-07-03 04:11:11`.
- Emulator integration: pass, `flutter test integration_test -d emulator-5554`.
- Emulator offline restart: pass, cached Search showed results immediately from local save.
- Current live Android/Search API count evidence: `2,266 searchable results` with `2,420`
  health `open_jobs`.
- Fresh iOS Simulator Search side-by-side review:
  `apps/atlas_flutter/docs/loop/screenshots/ios-simulator-reference-20260703/ios_simulator_android_search_side_by_side.png`.
