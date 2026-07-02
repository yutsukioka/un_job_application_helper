# Physical Pixel Verification Runbook

Date: 2026-07-03
Device: Pixel 8 Pro `38281FDJG001DJ`
App package: `com.yutsukioka.jobagg.atlas`
Server: `http://10.253.1.43:8765`

## Current Gate State

Physical-device verification is still blocked by the device lock screen. ADB confirms Atlas is the
resumed activity underneath keyguard, but the visible screenshot is the lock screen:

- `screenshots/physical-pixel-20260703/current_visibility_check.png`

Observed state:

- `mCurrentFocus=Window{... NotificationShade}`
- `mDreamingLockscreen=true`
- Atlas activity resumed underneath keyguard:
  `com.yutsukioka.jobagg.atlas/.MainActivity`

No physical in-app screenshot should be treated as complete until the Pixel is unlocked and the app
surface is visible.

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

Use this directory for the next physical pass:

`apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/`

Capture command pattern:

```sh
adb -s 38281FDJG001DJ shell screencap -p /sdcard/<name>.png
adb -s 38281FDJG001DJ pull /sdcard/<name>.png apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/<name>.png
```

Required physical screenshots:

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
- `offline_restart_cached.png`
- `settings_cache_status.png`
- `job_detail.png`
- `saved_tab.png`
- `updates_tab.png`
- `sources_tab.png`

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
12. Open a result row and capture `job_detail.png`.
13. Capture Saved, Updates, Sources, and Settings tabs.

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

- All required screenshots above exist and show the app, not lock screen.
- Offline restart screenshot shows cached rows, not an empty state.
- The screenshots are linked from `ANDROID_SEARCH_UI_AUDIT.md`.
- Human review comments `APPROVED: G3` or equivalent on PR #10.
