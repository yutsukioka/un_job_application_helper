# Android Atlas Completion Audit

Date: 2026-07-04
Branch: `codex/atlas-flutter-android-parity`
Latest app-code head audited: `12f0599`
Latest evidence/docs head audited before this file: `e626195`

This audit treats completion as unproven unless there is direct current evidence. It does not mark
PR #10 as merge-ready.

## Result

Strict score is **91/100**.

The Android app has strong implementation, test, build, data-reconciliation, side-by-side, and
physical Pixel evidence. The remaining gaps are narrow but still blocking:

- Exact `APPROVED: G2` design-review gate comment is missing.
- Exact user-provided iOS screenshots are not local files; source-rendered iOS Simulator references
  are available instead.

## Requirement Matrix

| Requirement | Current status | Evidence | Missing / next proof |
| --- | --- | --- | --- |
| Visual review evidence | Reviewable, pending exact G2 comment | Source-rendered iOS references in `screenshots/ios-simulator-expanded-20260703/`; generated side-by-side packages in `screenshots/ios-android-filter-side-by-side-20260703/` and `screenshots/ios-android-primary-side-by-side-20260703/`; physical Search/filter/sort/tab screenshots in `screenshots/physical-pixel-20260703/`; physical refreshed Search, Settings, offline restart, and Job Detail screenshots in `screenshots/physical-pixel-20260704/`; `APPROVED: G3` comment exists at `https://github.com/yutsukioka/un_job_application_helper/pull/10#issuecomment-4880106432`. | Exact `APPROVED: G2` design-review PR comment and exact user-provided iOS screenshot pairing if required by reviewer. |
| Search count vs health count reconciliation | Proven for latest physical snapshot | `ANDROID_SEARCH_UI_AUDIT.md` and `STATUS.jsonl` iteration 41 record `2,304 searchable results`, `2,392 health open_jobs`, and `88` deadline-past open rows hidden by Search. | Re-run `/api/health` and `/api/search` after any later backend refresh because counts are time-sensitive. |
| Updates tab is not placeholder | Proven | `test/widget_test.dart` covers live Updates/Sources data; `test/tab_golden_test.dart` and `test/goldens/android/updates_tab.png` lock the implemented Updates view. Physical `updates_tab_final.png` exists. | None for implementation; human visual review may still request refinements. |
| Sources tab is not placeholder | Proven | `test/widget_test.dart` covers live Updates/Sources data; `test/tab_golden_test.dart` and `test/goldens/android/sources_tab.png` lock the implemented Sources view. Physical `sources_tab_final.png` exists. | None for implementation; human visual review may still request refinements. |
| Job Detail improved beyond stub | Proven | `test/widget_test.dart` covers populated fields and hidden diagnostics; `test/search_golden_test.dart` and `test/goldens/android/job_detail_top.png` cover the populated layout; emulator/detail screenshot `screenshots/detail-formatter-20260703/job_detail_top_fixed.png` exists; physical screenshot `screenshots/physical-pixel-20260704/job_detail_corrected_clean.png` shows a real Job Detail screen. | Human review may still request refinements. The untracked `physical-pixel-20260703/job_detail_final.png` is invalid because it captured the launcher/app drawer. |
| Persistent cache and offline startup | Proven | `test/atlas_local_cache_test.dart` covers write/read/corruption/clear; controller tests cover cached detail/offline serving; emulator `offline_restart_no_banner.png` shows cached rows immediately; physical `screenshots/physical-pixel-20260704/offline_restart_cached.png` shows cached Search rows after force-stop/relaunch with Pixel Wi-Fi disabled. | Human review may still request repeat verification. |
| Android Search ANR fixed | Proven for observed failure | `dumpsys lastanr` diagnosed input timeout; commit `9d68958` changed Search results to lazy row construction; `test/widget_test.dart` covers lazy result-row construction; physical `anr_fix_sort_check.png` shows sort menu opening without ANR. | Continue watching human testing for recurrence. |
| Cached `Open only` deadline behavior | Proven in tests and physical Search/Settings count | Commit `12f0599` excludes past-deadline open rows in cached/offline filtering; `test/atlas_search_controller_test.dart` covers expired-open exclusion; physical Settings shows final 2,304/2,392/88 reconciliation. | Re-run count reconciliation after next backend refresh. |
| Android launcher icon parity | Proven by asset test; physical launcher review partial | `test/android_launcher_icon_test.dart` verifies Atlas artwork across Android mipmap densities; Android mipmaps use `apps/apple/Design/AppIcon/AppIcon-iOS-1024.png`. | Human can still review icon visually on the Pixel launcher/app drawer after reconnect. |
| Automated tests/builds | Proven at last app-code verification | Latest app-code verification: `dart analyze`, focused lazy-list/cache tests, `flutter test --coverage` with 67 tests, `flutter build apk --release`, and prior `flutter build appbundle --release` passed. PR checks after docs pushes are green. | Re-run full app verification if any app code changes. |
| Copilot/review feedback | Currently clear | Thread-aware `gh api graphql` review-thread query returned no current non-outdated unresolved actionable threads; PR checks are green. | Recheck after every push. |
| Physical Pixel G3 gate | Complete | Physical screenshots exist for Search, scrolled Search, Filter sheet, Sort, Saved, Updates, Sources, Settings, refreshed Settings, refreshed Search, offline restart, and Job Detail. `APPROVED: G3` was posted on PR #10 at `2026-07-04T01:07:37Z`. | None. |

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
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/settings_after_refresh.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/search_top_after_refresh.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/offline_restart_cached.png`
- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/job_detail_corrected_clean.png`

Invalid physical evidence:

- `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/job_detail_final.png` is
  untracked and invalid because it captured the launcher/app drawer.

## Next Required Actions

1. Post the 2026-07-04 evidence and G3 approval update to PR #10.
2. Recheck PR checks and Copilot/human review threads after the push.
3. Obtain exact `APPROVED: G2` on PR #10, or an explicit reviewer waiver of that gate.
4. If the reviewer requires exact user-provided iOS screenshot pairing, copy those screenshots into
   the repo or provide accessible paths and regenerate the side-by-side package.
