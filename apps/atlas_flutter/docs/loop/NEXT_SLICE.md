# Next Slice

Gate state: implementation for persistent cache, full iOS-style filter groups, single-value
City/Country cascade, Seniority/Grade cascade, and core Cupertino-style icon mapping is in place.
PR #10 remains below completion because post-fix in-app screenshots and physical offline-restart
verification are blocked by the locked Pixel.

## Intent

Capture human-reviewable evidence on the unlocked Pixel 8 Pro and fix any visible regressions found
from that evidence. Do not start broad backend or JobAgg lifecycle work.

## Acceptance Tests

- Unlock Pixel 8 Pro `38281FDJG001DJ`.
- Launch installed release APK `com.yutsukioka.jobagg.atlas`.
- With server available, refresh Search and confirm cached dataset is written.
- Kill app, disable/stop server or make it unreachable, relaunch app, and confirm cached Search rows
  appear immediately with `Offline (cached)`/stale state.
- Verify Search, filter sheet, filter chips, sort, saved state, Job Detail, Updates, Sources, and
  Settings still work from cached or live data as appropriate.
- Capture screenshots:
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
- Update `ANDROID_SEARCH_UI_AUDIT.md` with screenshot paths and human-visible differences.
- Run `dart format --set-exit-if-changed .`, `dart analyze`, `flutter test --coverage`, and the
  Android release build after any visual fixes.

## Known Remaining Technical Gaps

| Gap | Current state | Required next action |
| --- | --- | --- |
| Screenshot evidence | Latest capture shows Pixel lock screen only. | Unlock device and capture post-fix app screenshots. |
| Physical offline restart | Covered by controller/cache tests, not physical screenshot evidence. | Perform manual USB Pixel restart/offline flow and capture screenshot. |
| Multiple city/country selections | Flutter matches current Swift model with single `city` and `countryISO3`. | Decide whether product wants to extend both iOS and Android to multi-select. |
| Backend location/grade facet metadata | Android computes city/country and grade/seniority facets locally from cached rows. | Add smallest API facet metadata only if server-side full-dataset counts are required. |
| Coverage | 90.62% after large filter UI addition. | Add screenshot/widget tests for any follow-up UI fixes; do not claim completion from coverage alone. |
| Integration test | Not rerun in the latest slice. | Run `flutter test integration_test -d emulator-5554` or document emulator blocker. |

## Last Verification Snapshot

- Format: pass.
- Analyze: pass.
- Full tests: pass, 41 tests.
- Coverage: pass, `2665/2941` lines, `90.62%`.
- Debug APK: pass.
- Release AAB: pass, `build/app/outputs/bundle/release/app-release.aab`.
- Release APK: pass, `build/app/outputs/flutter-apk/app-release.apk`.
- USB Pixel install: pass, `lastUpdateTime=2026-07-03 01:07:21`.
