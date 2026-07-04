# Physical Pixel Verification Runbook

Date: 2026-07-04
Device: Pixel 8 Pro `38281FDJG001DJ`
App package: `com.yutsukioka.jobagg.atlas`
Server: `http://10.253.1.43:8765`

## Current Gate State

Physical-device screenshot verification is complete for the current release APK, and the G3 human
approval gate is approved.

Completed physical evidence after the ANR/cache fixes and 2026-07-04 recapture:

- `screenshots/physical-pixel-20260703/search_top_final.png`
- `screenshots/physical-pixel-20260703/search_scrolled_final.png`
- `screenshots/physical-pixel-20260703/filter_sheet_final.png`
- `screenshots/physical-pixel-20260703/sort_menu_final.png`
- `screenshots/physical-pixel-20260703/saved_tab_final.png`
- `screenshots/physical-pixel-20260703/updates_tab_final.png`
- `screenshots/physical-pixel-20260703/sources_tab_final.png`
- `screenshots/physical-pixel-20260703/settings_tab_final.png`
- `screenshots/physical-pixel-20260704/settings_after_refresh.png`
- `screenshots/physical-pixel-20260704/search_top_after_refresh.png`
- `screenshots/physical-pixel-20260704/offline_restart_cached.png`
- `screenshots/physical-pixel-20260704/job_detail_corrected_clean.png`

Gate evidence:

- `APPROVED: G3` posted by `yutsukioka` on PR #10 at `2026-07-04T01:07:37Z`:
  `https://github.com/yutsukioka/un_job_application_helper/pull/10#issuecomment-4880106432`

The untracked `screenshots/physical-pixel-20260703/job_detail_final.png` remains invalid evidence
because it captured the launcher/app drawer instead of Atlas Job Detail. The valid replacement is
`screenshots/physical-pixel-20260704/job_detail_corrected_clean.png`.

## 2026-07-04 Physical Pass

Device state:

- `adb devices -l` showed Pixel 8 Pro `38281FDJG001DJ` connected and authorized.
- Atlas was already installed and launchable as `com.yutsukioka.jobagg.atlas/.MainActivity`.
- Settings refresh against `http://10.253.1.43:8765` succeeded.

Current data reconciliation after tapping `Refresh Local Save Now`:

- `/api/health open_jobs=2392`
- `/api/health last_sync_at=2026-07-04T00:52:18.058256+00:00`
- POST `/api/search` with Android default open/searchable filters returned `total=2304`.
- Physical Settings displayed `2,304 searchable results`, `2,392` health open jobs, and
  `88 deadline-past open rows hidden by Search`.

Offline restart test:

- Wi-Fi was temporarily disabled on the Pixel with `adb shell svc wifi disable`.
- Atlas was force-stopped and relaunched while the server was unreachable from the device.
- `screenshots/physical-pixel-20260704/offline_restart_cached.png` shows cached Search rows and
  `2,304 searchable results` immediately on startup with an offline indicator.
- Wi-Fi was restored with `adb shell svc wifi enable`.

Corrected Job Detail test:

- Tapping the first visible Search result opened Atlas Job Detail.
- `screenshots/physical-pixel-20260704/job_detail_corrected_clean.png` shows the real detail screen
  with title, bookmark control, metadata chips, weak-detail state, full description, and core
  fields.

## Pre-Flight

1. Unlock the Pixel 8 Pro and keep it awake.
2. Confirm the local server is reachable from the Pixel network:
   `http://10.253.1.43:8765`
3. Confirm USB debugging remains authorized:
   `adb devices -l`
4. Launch Atlas:
   `adb -s 38281FDJG001DJ shell am start -n com.yutsukioka.jobagg.atlas/.MainActivity`
5. Confirm Atlas is visible, not obscured by keyguard:
   `adb -s 38281FDJG001DJ shell dumpsys window | rg "mCurrentFocus|mDreamingLockscreen"`

Pass condition:

- `mDreamingLockscreen=false`
- current focus is Atlas or a visible Atlas dialog/sheet.

## Screenshot Capture Commands

Use this directory for the current physical pass:

`apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260704/`

Capture command pattern:

```sh
adb -s 38281FDJG001DJ shell screencap -p /sdcard/<name>.png
adb -s 38281FDJG001DJ pull /sdcard/<name>.png apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/<name>.png
```

Already captured in the 2026-07-03 bundle:

- `search_top_final.png`
- `search_scrolled_final.png`
- `filter_sheet_final.png`
- `sort_menu_final.png`
- `saved_tab_final.png`
- `updates_tab_final.png`
- `sources_tab_final.png`
- `settings_tab_final.png`

Captured in the 2026-07-04 bundle:

- `settings_after_refresh.png`
- `search_top_after_refresh.png`
- `offline_restart_cached.png`
- `job_detail_corrected_clean.png`

Still required before merge:

- Exact `APPROVED: G2` design-review PR comment, unless the reviewer explicitly waives that gate.

Optional recaptures if human review requests full physical coverage:

- `search_top.png`
- `search_scrolled.png`
- `filter_top.png`
- `filter_location.png`
- `filter_contract_seniority.png`
- `filter_grade_ccog.png`
- `filter_organizations_workmode.png`
- `filter_capability_tags.png`
- `filter_japan_selected.png`
- `filter_tokyo_selected.png`
- `filter_entry_junior_selected.png`
- `filter_grade_selected.png`
- `settings_cache_status.png`

## Manual Flow

1. Open Settings.
2. Verify API base URL is `http://10.253.1.43:8765`.
3. Tap `Save and Reload`.
4. Confirm cache status shows `Fresh`, cached/search count, health open count, and hidden
   deadline-past count.
5. Open Search and capture `search_top.png`.
6. Scroll slightly and capture `search_scrolled.png`.
7. Open the filter sheet and capture each section.
8. Test Country -> City cascade:
   - Type/select `JPN`.
   - Capture `filter_japan_selected.png`.
9. Test City -> Country cascade:
   - Reset.
   - Type/select `TOKYO`.
   - Capture `filter_tokyo_selected.png`.
10. Test Seniority -> Grade cascade:
    - Reset.
    - Select `Entry Junior`.
    - Capture `filter_entry_junior_selected.png`.
11. Test Grade -> Seniority cascade:
    - Reset.
    - Select a visible grade such as `P-1` if present.
    - Capture `filter_grade_selected.png`.
12. Open a result row and capture `job_detail.png` or `job_detail_final.png`.
13. Capture Saved, Updates, Sources, and Settings tabs only if recapture is requested; final
    physical versions already exist.

## Offline Restart Flow

Do not stop the local server globally if other work depends on it. Prefer temporarily disabling Pixel
network from the device UI, or put the app server URL in Settings to an unreachable local address
only for this test.

Required behavior:

1. Start with server available and refresh local save.
2. Kill the app:
   `adb -s 38281FDJG001DJ shell am force-stop com.yutsukioka.jobagg.atlas`
3. Make the server unreachable from the Pixel.
4. Relaunch:
   `adb -s 38281FDJG001DJ shell am start -n com.yutsukioka.jobagg.atlas/.MainActivity`
5. Capture `offline_restart_cached.png`.

Pass condition:

- Search does not start empty.
- Cached rows are visible immediately.
- Result count is shown from cache.
- Local save/offline/stale state is visible.
- Search/filter interactions still operate from cached rows.

## Final Physical Gate

Physical Pixel verification is complete only when:

- All required screenshots above exist and show the app, not lock screen. Complete as of the
  2026-07-04 bundle.
- Offline restart screenshot shows cached rows, not an empty state. Complete as of
  `offline_restart_cached.png`.
- The screenshots are linked from `ANDROID_SEARCH_UI_AUDIT.md`.
- Human review comments `APPROVED: G3` or equivalent on PR #10. Complete.
