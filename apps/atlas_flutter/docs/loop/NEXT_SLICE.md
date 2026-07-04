# Next Slice

Gate state: implementation for persistent cache, iOS-style filter groups, multi-value
City/Country cascade, Seniority/Grade cascade, Cupertino-style icon mapping, Apple Atlas Android
launcher icon, compact Search rows, Updates/Sources, and populated Job Detail is in place. The
Search ANR reported on the physical Pixel was fixed by changing Search results to lazy row
construction, and cached/offline `Open only` filtering now excludes past-deadline open rows like the
Search API.

PR #10 is still below final completion because corrected physical Job Detail capture, physical
offline-restart verification, exact user-provided iOS screenshot pairing, and human G3 approval are
pending. The Pixel 8 Pro was detached after the latest physical screenshot pass, so no further ADB
verification is possible until it is reconnected.

## Intent

When the Pixel is reconnected, finish the remaining physical review gaps without broad backend or
JobAgg lifecycle work: recapture Job Detail correctly, run the offline restart/cache check on the
phone, capture any missing filter-section states if human review needs them, and post the final
evidence package for G3.

## Acceptance Tests

- Reconnect and unlock Pixel 8 Pro `38281FDJG001DJ`.
- Confirm the latest release APK still launches `com.yutsukioka.jobagg.atlas`.
- With server `http://10.253.1.43:8765` available, refresh Search and confirm the displayed count
  matches POST `/api/search` for default open/searchable filters.
- Kill app, make the server unreachable, relaunch app, and confirm cached Search rows appear
  immediately with stale/offline state.
- Verify Search, filter sheet, filter chips, sort, saved state, Job Detail, Updates, Sources, and
  Settings still work from cached or live data as appropriate.
- Capture corrected physical screenshots for Job Detail and offline restart. Recapture filter
  section/cascade states only if the human reviewer needs physical-device versions beyond the
  existing emulator/iOS side-by-side evidence.
- Update `ANDROID_SEARCH_UI_AUDIT.md` and `PR_REPORT.md` with final screenshot paths and any
  human-visible differences.
- After any app-code change, rerun `dart format --set-exit-if-changed .`, `dart analyze`,
  `flutter test --coverage`, and the Android release build.

## Known Remaining Technical Gaps

| Gap | Current state | Required next action |
| --- | --- | --- |
| Physical screenshot evidence | Physical Search top/scrolled, Filter sheet, Sort menu, Saved, Updates, Sources, and Settings are captured. Corrected physical Job Detail was not captured before detach. | Reconnect Pixel and capture a real Job Detail screen. |
| Physical offline restart | Covered by controller/cache tests and emulator evidence; not yet physically verified after the final cache/deadline fix. | Perform USB Pixel offline restart flow and capture screenshot. |
| iOS side-by-side package | Source-rendered iOS Simulator references and generated side-by-side packages exist. Exact user-provided iOS screenshots are not available as local files. | Add/copy the user-provided iOS screenshots if available, then update the final comparison. |
| Android goldens | Search-top, filter-sheet top, location cascade, populated Job Detail, Saved, Updates, Sources, and Settings goldens exist. | Add more only if human review finds a concrete visual regression. |
| Multiple city/country selections | Android supports comma-separated text input plus multi-select pills; values serialize to Search API list fields and filter cached rows as OR within Location. | Human-review whether this display is close enough to iOS or should become a dedicated selected-chip editor. |
| Backend location/grade facet metadata | Android computes city/country and grade/seniority facets locally from cached rows. | Add API facet metadata only if server-side full-dataset counts become required. |
| Human gate | PR checks were green and no current non-outdated unresolved actionable Copilot threads were found; G3 is still pending. | Post/refresh the evidence package and wait for explicit `APPROVED: G3`. |

## Last Verification Snapshot

- Format/analyze/full tests: pass from the latest app-code verification.
- Latest full Flutter suite after ANR/cache fixes: `flutter test --coverage`, 67 tests, passed.
- Release APK: pass, `apps/atlas_flutter/build/app/outputs/flutter-apk/app-release.apk`.
- Release AAB: pass, `apps/atlas_flutter/build/app/outputs/bundle/release/app-release.aab`.
- Release APK install before detach: pass on Pixel 8 Pro `38281FDJG001DJ`.
- Physical ANR fix check: pass; `Sort: Closing soon` opened without another ANR after the lazy-list
  fix.
- Current live reconciliation before detach: `/api/health open_jobs=2452`; POST `/api/search`
  returned `total=2355`; physical Settings after `Refresh Local Save Now` displayed
  `2,355 searchable results`, `2,452 health open jobs`, and `97 deadline-past open rows hidden by
  Search`.
- Physical screenshots captured:
  `apps/atlas_flutter/docs/loop/screenshots/physical-pixel-20260703/search_top_final.png`,
  `search_scrolled_final.png`, `filter_sheet_final.png`, `sort_menu_final.png`,
  `saved_tab_final.png`, `updates_tab_final.png`, `sources_tab_final.png`, and
  `settings_tab_final.png`.
- Corrected physical Job Detail screenshot: pending; the untracked `job_detail_final.png` is an
  invalid launcher/app-drawer capture and is intentionally excluded from evidence.
