# Android Atlas Completion Audit

Date: 2026-07-03
Branch: `codex/atlas-flutter-android-parity`
Latest app-code head audited: `12f0599`
Latest evidence/docs head audited before this file: `a21b2d2`

This audit treats completion as unproven unless there is direct current evidence. It does not mark
PR #10 as merge-ready.

## Result

Strict score remains **86/100**.

The Android app has strong implementation, test, build, data-reconciliation, side-by-side, and
partial physical Pixel evidence. The remaining gaps are narrow but still blocking:

- Corrected physical Job Detail screenshot is missing.
- Physical offline-restart cache verification after the final cached-deadline fix is missing.
- Human `APPROVED: G3` is missing.
- Exact user-provided iOS screenshots are not local files; source-rendered iOS Simulator references
  are available instead.

## Requirement Matrix

| Requirement | Current status | Evidence | Missing / next proof |
| --- | --- | --- | --- |
| Visual review evidence | Partial, not final | Source-rendered iOS references in `screenshots/ios-simulator-expanded-20260703/`; generated side-by-side packages in `screenshots/ios-android-filter-side-by-side-20260703/` and `screenshots/ios-android-primary-side-by-side-20260703/`; physical Search/filter/sort/tab screenshots in `screenshots/physical-pixel-20260703/`. | Corrected physical Job Detail screenshot, physical offline restart screenshot, and exact user-provided iOS screenshot pairing if required by reviewer. |
| Search count vs health count reconciliation | Proven for last verified snapshot | `ANDROID_SEARCH_UI_AUDIT.md` and `STATUS.jsonl` iteration 36 record `2,355 searchable results`, `2,452 health open_jobs`, and `97` deadline-past open rows hidden by Search. | Re-run `/api/health` and `/api/search` after the next physical refresh because counts are time-sensitive. |
| Updates tab is not placeholder | Proven | `test/widget_test.dart` covers live Updates/Sources data; `test/tab_golden_test.dart` and `test/goldens/android/updates_tab.png` lock the implemented Updates view. Physical `updates_tab_final.png` exists. | None for implementation; human visual review may still request refinements. |
| Sources tab is not placeholder | Proven | `test/widget_test.dart` covers live Updates/Sources data; `test/tab_golden_test.dart` and `test/goldens/android/sources_tab.png` lock the implemented Sources view. Physical `sources_tab_final.png` exists. | None for implementation; human visual review may still request refinements. |
| Job Detail improved beyond stub | Implementation proven; physical evidence incomplete | `test/widget_test.dart` covers populated fields and hidden diagnostics; `test/search_golden_test.dart` and `test/goldens/android/job_detail_top.png` cover the populated layout; emulator/detail screenshot `screenshots/detail-formatter-20260703/job_detail_top_fixed.png` exists. | Corrected physical Job Detail screenshot. The untracked `physical-pixel-20260703/job_detail_final.png` is invalid because it captured the launcher/app drawer. |
| Persistent cache and offline startup | Implementation proven; latest physical offline proof incomplete | `test/atlas_local_cache_test.dart` covers write/read/corruption/clear; controller tests cover cached detail/offline serving; emulator `offline_restart_no_banner.png` shows cached rows immediately. | Physical offline restart screenshot after final cached-deadline fix. |
| Android Search ANR fixed | Proven for observed failure | `dumpsys lastanr` diagnosed input timeout; commit `9d68958` changed Search results to lazy row construction; `test/widget_test.dart` covers lazy result-row construction; physical `anr_fix_sort_check.png` shows sort menu opening without ANR. | Continue watching human testing for recurrence. |
| Cached `Open only` deadline behavior | Proven in tests and physical Search/Settings count | Commit `12f0599` excludes past-deadline open rows in cached/offline filtering; `test/atlas_search_controller_test.dart` covers expired-open exclusion; physical Settings shows final 2,355/2,452/97 reconciliation. | Re-run count reconciliation after next backend refresh. |
| Android launcher icon parity | Proven by asset test; physical launcher review partial | `test/android_launcher_icon_test.dart` verifies Atlas artwork across Android mipmap densities; Android mipmaps use `apps/apple/Design/AppIcon/AppIcon-iOS-1024.png`. | Human can still review icon visually on the Pixel launcher/app drawer after reconnect. |
| Automated tests/builds | Proven at last app-code verification | Latest app-code verification: `dart analyze`, focused lazy-list/cache tests, `flutter test --coverage` with 67 tests, `flutter build apk --release`, and prior `flutter build appbundle --release` passed. PR checks after docs pushes are green. | Re-run full app verification if any app code changes. |
| Copilot/review feedback | Currently clear | Thread-aware `gh api graphql` review-thread query returned no current non-outdated unresolved actionable threads; PR checks are green. | Recheck after every push. |
| Physical Pixel G3 gate | Not complete | Partial physical screenshots exist for Search, scrolled Search, Filter sheet, Sort, Saved, Updates, Sources, Settings. | Reconnect device, capture Job Detail and offline restart, then obtain explicit `APPROVED: G3` PR comment. |

## Current Physical Evidence

Tracked physical Pixel evidence:

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

Invalid physical evidence:

- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/job_detail_final.png` is
  untracked and invalid because it captured the launcher/app drawer.

## Next Required Actions

1. Reconnect and unlock Pixel 8 Pro `38281FDJG001DJ`.
2. Launch the latest release APK.
3. Refresh from `http://10.253.1.43:8765` and re-run `/api/health` vs `/api/search` count
   reconciliation.
4. Capture a real Job Detail physical screenshot.
5. Force-stop Atlas, make the server unreachable, relaunch, and capture physical offline-restart
   cached Search evidence.
6. Update `ANDROID_SEARCH_UI_AUDIT.md`, `PR_REPORT.md`, and `PR_COMMENT_LATEST.md` with final paths.
7. Post the final PR evidence comment and wait for explicit `APPROVED: G3`.
