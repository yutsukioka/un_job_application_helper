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
Pixel 8 Pro, but post-fix in-app screenshots are blocked because the physical device remained on the
lock screen. Human-visible review is therefore still incomplete and the score is capped by the
evidence rule.

## Implemented This Slice

- Persistent file-backed cache in Android app-private storage through the native `filesDir` method
  channel.
- Cache snapshot now stores the active Search response and the broad default open Search dataset
  separately, so offline filters can be recomputed from cached rows.
- Startup cache hydration loads cached Search data before any network refresh.
- Offline refresh failure keeps cached rows visible and marks the app `Offline (cached)`.
- Settings shows cache freshness/status and includes Clear Local Cache.
- Local offline filtering mirrors Swift semantics: OR within a group, AND across groups.
- Dark filter modal with drag handle, `Filters` title, `Done`, sticky `Reset` / `Apply filters`,
  compact two-column pills, counts, dimmed unavailable values, and explanatory text.
- Filter groups now render: Status, Location, Scope, Contract, UN Volunteer Category, Seniority,
  Grade, CCOG Family, Organizations, Work Mode, and Capability Tags.
- City/Country cascade test: selecting `JPN` restricts cities to Tokyo/Osaka; selecting `Tokyo`
  restricts countries to `JPN`.
- Seniority/Grade cascade test: `standard_seniority_tier` maps Entry Junior rows to junior grades;
  selecting grade `P1` restricts seniority to Entry Junior.
- Core app icons now route through shared `AtlasIcons` using Cupertino-style symbols for Search,
  filters, bookmark, deadline, location, organization, contract, remote, updates, sources, settings,
  chevron, info/warning, copy/link, refresh, and delete.

## Data Count Reconciliation

| Field | Value |
| --- | --- |
| `health_open_jobs` | `2,420` |
| `search_api_total` | `2,274` |
| `android_displayed_total` | `2,274 searchable results` |
| `active_filters` | Default Search: `status=["open"]`, no text query, no source/org filters, sort `closing_date_asc`; Search API default `exclude_expired_open=true`. |
| `excluded_count` | `146` |
| `excluded_reason_breakdown` | Rows still marked `status='open'` in health but with passed deadlines, hidden by Search API `exclude_expired_open=true`. |
| `final_decision` | `2,274` is correct for Android default Search because it matches `/api/search`; `2,420` is the raw health open count. |

## Verification

Commands run from `apps/atlas_flutter`:

- `dart format --set-exit-if-changed .` passed.
- `dart analyze` passed with no issues.
- Focused tests passed: `flutter test test/atlas_api_client_test.dart test/atlas_filters_test.dart test/atlas_local_cache_test.dart test/atlas_search_controller_test.dart test/widget_test.dart`.
- Full tests passed: `flutter test`, 41 tests.
- Coverage passed: `flutter test --coverage`, 41 tests, `2665/2941` lines, `90.62%`.
- `flutter build apk --debug` passed.
- `flutter build appbundle --release` passed: `build/app/outputs/bundle/release/app-release.aab` (`46.6MB`).
- `flutter build apk --release` passed: `build/app/outputs/flutter-apk/app-release.apk` (`49.2MB`). Gradle emitted an `llvm-strip` warning for `lib/armeabi-v7a/libapp.so`, but the APK was produced.
- Release APK installed on USB Pixel 8 Pro: `adb -s 38281FDJG001DJ install -r build/app/outputs/flutter-apk/app-release.apk` returned `Success`.

Installed package evidence:

- Package: `com.yutsukioka.jobagg.atlas`
- Version: `versionCode=1`, `versionName=1.0.0`
- Device: `38281FDJG001DJ`
- Package `lastUpdateTime`: `2026-07-03 01:07:21`

## Screenshot Evidence

Post-fix in-app screenshots are blocked because the connected Pixel remained on the lock screen.
Captured files show the lock state only:

- `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-20260703/search_top.png`
- `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-20260703/lock_check.png`

Previously generated evidence remains available for Search top comparison:

- `apps/atlas_flutter/docs/loop/screenshots/ios-reference/iteration-8/ios_search_top.png`
- `apps/atlas_flutter/docs/loop/screenshots/iteration-8/search_top_ios_android_side_by_side.png`

The required post-fix screenshots still need to be captured after the device is unlocked:

- Search top and scrolled
- Filter sheet top, Location, Contract, Seniority, Grade, CCOG, Organizations, Work Mode, Capability Tags
- Filter sheet with Japan/Tokyo and Entry Junior/P1 selected
- Offline restart with cached data visible
- Settings cache status
- Job Detail, Saved, Updates, Sources

## Current Remaining Gaps

- Physical in-app screenshots for the new cache/filter/icon slice are missing due to lock-screen
  blocker.
- Filter sheet parity is implemented from Swift source and covered by tests, but still needs human
  visual review against the user-provided iOS screenshots.
- Flutter still models Location as single `city` and single `countryISO3`, matching the checked-in
  Swift model. Multi-country/multi-city selection from the user note is not implemented in this
  slice.
- Backend facets still do not expose city/country or grade-to-seniority metadata. Android computes
  those facets locally from cached Search rows.
- Coverage remains strong but lower than the previous cache-slice number because this slice added a
  large amount of UI code.
- Integration test on `emulator-5554` was not rerun in this slice.

## Scorecard

Strict score under the new user caps: **60/100**.

The underlying implementation is materially higher than the previous 47/100 cache-only state, but
the screenshot cap limits this slice to 60/100 until post-fix app screenshots are captured and
human-visible parity is reviewed.

| Category | Score | Notes |
| --- | ---: | --- |
| Persistent cache | 17 / 20 | Full-dataset file cache, startup hydration, stale/clear UI, and offline filter tests exist; physical offline-restart screenshot evidence pending. |
| Filter parity | 24 / 30 | All iOS groups render in a dark sheet with counts/dimming and sticky actions; multiple city/country selection is not implemented. |
| Icon parity | 7 / 10 | Visible controls route through shared Cupertino-style `AtlasIcons`; screenshot comparison pending. |
| Existing Search/data/detail/tabs | 13 / 15 | Count reconciliation, compact rows, Updates/Sources, Saved, and Detail remain intact. |
| Tests/builds | 12 / 15 | Format, analyze, full tests, coverage, debug APK, release APK, and release AAB pass; integration test not rerun. |
| Evidence/human readiness | 2 / 10 | Audit updated, but post-fix app screenshots are blocked by the locked Pixel. |

## Next Required Human Action

Unlock the connected Pixel 8 Pro, then rerun screenshot capture and offline-restart verification.
Do not claim 80%+ completion until those screenshots prove the Android UI/behavior is close to the
iOS reference.
