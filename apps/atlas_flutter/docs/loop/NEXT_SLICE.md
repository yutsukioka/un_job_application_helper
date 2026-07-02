# Next Slice

Gate state: implementation for persistent cache, full iOS-style filter groups, single-value
City/Country cascade, Seniority/Grade cascade, and core Cupertino-style icon mapping is in place.
Emulator release-app screenshots, Search-top side-by-side review, and offline restart evidence are
captured. PR #10 remains below completion because physical Pixel in-app screenshots, physical
offline-restart verification, and full iOS filter/detail side-by-side review are still pending.

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
| iOS side-by-side package | Search-top side-by-side exists in `IOS_ANDROID_VISUAL_REVIEW.md`; full user-provided iOS filter/detail screenshots are not available as local files. | Add/copy local iOS references, then build final side-by-side review package after physical Android captures. |
| Multiple city/country selections | Flutter matches current Swift model with single `city` and `countryISO3`. | Decide whether product wants to extend both iOS and Android to multi-select. |
| Backend location/grade facet metadata | Android computes city/country and grade/seniority facets locally from cached rows. | Add smallest API facet metadata only if server-side full-dataset counts are required. |
| Coverage | 90.62% after large filter UI addition. | Add screenshot/widget tests for any follow-up UI fixes; do not claim completion from coverage alone. |
| Integration test | Passed on `emulator-5554` after updating the smoke test for the new `Done` filter-sheet control. | Keep this green after any physical-review fixes. |

## Last Verification Snapshot

- Format: pass.
- Analyze: pass.
- Full tests: pass, 41 tests.
- Coverage: pass, `2665/2941` lines, `90.62%`.
- Debug APK: pass.
- Release AAB: pass, `build/app/outputs/bundle/release/app-release.aab`.
- Release APK: pass, `build/app/outputs/flutter-apk/app-release.apk`.
- USB Pixel install: pass, `lastUpdateTime=2026-07-03 01:07:21`.
- Emulator integration: pass, `flutter test integration_test -d emulator-5554`.
- Emulator offline restart: pass, cached Search showed `2,271 searchable results` immediately from
  local save.
- Search side-by-side review: `apps/atlas_flutter/docs/loop/IOS_ANDROID_VISUAL_REVIEW.md`.
