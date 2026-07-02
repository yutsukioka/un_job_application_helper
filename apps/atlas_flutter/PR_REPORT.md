# Atlas Flutter Android PR Report

Status: draft current-state report, not final completion.

Branch: `codex/atlas-flutter-android-parity`  
Latest report update: 2026-07-03  
Latest branch head before this report update: `47a0d5d`

## Summary

The Android Flutter app has moved beyond the initial Search-only parity slice. It now includes a
persistent local cache, offline startup behavior on emulator, an iOS-style Search screen, full
filter groups, City/Country and Seniority/Grade cascades, multi-value City/Country filter support,
compact result rows, populated and persistently cached Job Detail, Saved/Updates/Sources/Settings
tabs, and a reviewable Android screenshot package.

This is still not a completion claim. Physical Pixel 8 Pro in-app screenshots and offline restart
verification remain blocked by the connected phone being locked. Full pixel-paired review against
the user-provided iOS filter/detail screenshots also remains blocked because those screenshots are
not available as local files in this worktree.

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

## Data Count Reconciliation

Last verified reconciliation from current app/API evidence:

- `health_open_jobs`: `2,420`
- `search_api_total`: `2,268`
- Android displayed count after current refresh: `2,268 searchable results`
- Difference: `152` deadline-past rows still counted by health as open but hidden by Search because
  default Search uses `exclude_expired_open=true`.

Current live refresh status:

- `curl http://10.253.1.43:8765/api/health` succeeded on 2026-07-03 and reported
  `open_jobs=2420`.
- Default `/api/search` with Android open/searchable filters reported `total=2268`.

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
- Filter, cascade, detail, settings, saved, updates, and sources screenshots:
  `apps/atlas_flutter/docs/loop/screenshots/filter-cache-icons-emulator-20260703/`
- Physical Pixel lock-screen evidence:
  `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/resume_visibility_check.png`

## Verification Snapshot

Latest app-code verification:

- `dart format --set-exit-if-changed .` passed.
- `dart analyze` passed.
- `flutter test --coverage` passed with 45 tests: `2733/3016` lines, `90.62%`.
- `flutter build apk --debug` passed.
- `flutter build apk --release` passed.
- `flutter build appbundle --release` passed.
- `flutter test integration_test -d emulator-5554` passed after simplifying the device smoke test to
  launch plus primary tab navigation. Filter/sort/modal behavior remains covered by widget tests.
- `flutter test integration_test -d 38281FDJG001DJ` built and installed the debug test APK, but the
  device-driven test did not complete after launch and was interrupted after `1:46`; the connected
  Pixel still reports keyguard/doze state.

Latest artifacts:

- Release APK:
  `apps/atlas_flutter/build/app/outputs/flutter-apk/app-release.apk`
- Release AAB:
  `apps/atlas_flutter/build/app/outputs/bundle/release/app-release.aab`

Latest physical install evidence:

- Device: Pixel 8 Pro `38281FDJG001DJ`
- Package: `com.yutsukioka.jobagg.atlas`
- Last installed release APK timestamp: `2026-07-03 03:38:08`

## Remaining Gaps

- Physical Pixel in-app screenshots are missing because the device remains locked.
- Physical offline restart with cached data visible is not yet human-verified.
- Human G3 approval is pending.
- User-provided iOS filter/detail screenshots are not available as local files for true pixel-paired
  side-by-side review.
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
6. Copy the user-provided iOS filter/detail screenshots into the repo or provide accessible paths,
   then update `IOS_ANDROID_VISUAL_REVIEW.md` with true iOS-vs-Android pairs.
7. Post final report evidence as a PR comment and wait for `APPROVED: G3`.

## Honest Score

Current strict score: `79/100`.

The score is capped below 80 because physical Pixel in-app evidence and full iOS filter/detail
side-by-side evidence are still missing. Do not claim final Android parity completion until those
gates are satisfied.
