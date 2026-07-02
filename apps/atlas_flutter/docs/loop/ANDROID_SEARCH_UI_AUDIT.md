# Android Search UI Audit

Date: 2026-07-02
Branch: `codex/atlas-flutter-android-parity`

## Current Android Implementation

- Framework: Flutter, Material 3, single `MaterialApp` in `apps/atlas_flutter/lib/features/app_shell/atlas_app.dart`.
- Search screen component: `AtlasSearchSkeleton`.
- Job result row/card component: `AtlasJobResultTile`.
- Filter chip component: `AtlasFilterChip`.
- Search input component: `TextField` inside `AtlasSearchSkeleton`.
- Sort control: `AtlasSearchStatusBar` with `MenuAnchor`.
- Saved/bookmark control: disabled `IconButton` in `AtlasHomeShell` app bar; no saved state.
- Bottom navigation: `NavigationBar` in `AtlasHomeShell` with Search, Saved, Updates, Sources, Settings.
- Data source: `AtlasAppController` calls `AtlasAPIClient.search` against `POST /api/search`; no direct Android file read of `all_jobs_current.*` or sqlite.
- Current mock/stub behavior: Saved, Updates, Sources are placeholders; filter button disabled; save button disabled; filter chips are decorative; job rows do not open detail; saved/bookmark persistence absent; local save is session-only.

## Gap List

| iOS reference behavior | Current Android behavior | Required change | File/component |
| --- | --- | --- | --- |
| Navigation title is centered `Search`; search field is in the top search area. | App bar title is `Atlas`; Search screen begins below a generic Material app bar. | Make Search tab header read `Search`, centered, and reserve top controls for Search-specific actions. | `AtlasHomeShell`, `AtlasSearchSkeleton` |
| Filter and Save Search toolbar buttons are functional, compact, and grouped by platform toolbar hierarchy. | Filter/save buttons are disabled icon buttons; no visual grouping. | Enable buttons, group them in a compact rounded control area, open real filter UI, and save current search locally. | `AtlasHomeShell`, new filter/save widgets |
| Filter ribbon has active/removable chips plus quick filter chips. | Chips render but are decorative and do not reflect controller filter state. | Add filter state, toggles/removal, and compact active chip styling. | `AtlasAppController`, `AtlasFilterChip`, filter UI |
| Count/sort bar is compact: results count, one-line status, right-aligned sort menu. | Similar fields exist, but layout is less dense and normal success messages show as a banner below. | Keep result summary compact; move normal local-save success into status text and reserve banners for errors only. | `AtlasSearchStatusBar`, `AtlasStatusBanner` |
| iOS result rows are `List` rows with compact vertical padding, source monogram, title, org/location, metadata line, optional short match summary. | Flutter rows are bordered card containers with larger spacing and always show match-summary paragraph text. | Replace card look with compact list rows; hide diagnostic/match text from main list by default; add trailing chevron. | `AtlasJobResultTile` |
| iOS row metadata uses a deadline pill plus inline grade/contract/modality text to avoid hanging chips. | Flutter metadata is separate chips in a wrapping `Wrap`, which can hang or detach. | Use deadline pill plus compact inline metadata; only wrap as a controlled fallback. | `AtlasJobResultTile`, `AtlasMetadataPill` |
| Sort menu updates sort order and triggers search. | Sort menu works after Iteration 4. | Keep behavior; tighten visual to iOS-style compact link. | `AtlasSearchStatusBar` |
| Search typing schedules/searches and submit searches. | Submit searches; typing only changes controller query until submit. | Add debounced/scheduled search or explicit clear/search behavior. | `AtlasAppController`, search input |
| Job row opens `JobDetailView`. | Flutter row has no tap behavior or detail route. | Add detail screen route and show diagnostic/match text there, not in list. | `AtlasHomeShell`, `AtlasJobResultTile`, new detail widget |
| Save Search persists and Saved tab lists saved searches/jobs. | Save button disabled; Saved tab placeholder only. | Implement local saved search/job state at least for current session or local persistence, and show saved jobs tab. | `AtlasAppController`, Saved tab |
| Open-only search should use current/open rows and not display expired rows without data-quality explanation. | Flutter trusts `/api/search` response and has no client-side stale/deadline audit. | Add UI/test guard for closed rows and deadline-passed data-quality state, then audit backend response source. | `AtlasAppController`, `AtlasJobResultTile`, tests |

## Immediate Slice Recommendation

The highest visible parity blocker is the Search tab structure and row density. The next implementation slice should:

1. Change the Search tab app bar title from `Atlas` to `Search`.
2. Replace disabled top-right controls with a compact functional filter/save group.
3. Make quick filter chips tappable and backed by controller state.
4. Remove normal success banners from the list flow and show local-save status in the compact summary row.
5. Replace large result cards with compact list rows and hide match diagnostics from the main list.
6. Add a first detail route so row taps are functional and diagnostic text has a destination.

## Iteration 5 Outcome

- Search tab title now reads `Search`, centered, with a compact pill group for filters and saved search.
- Search input, active filter chips, quick filter toggles, filter sheet, sort menu, saved-search action, row tap detail, and bottom navigation are functional in widget/integration coverage.
- Main results now render as compact list rows with source badge, title, organization/location, deadline pill, inline grade/contract/modality, and trailing chevron.
- Match diagnostics are hidden from Search rows and shown on the first detail screen.
- Normal local-save success is represented as compact status text; banners are reserved for errors.
- Saved search persistence now uses `POST /api/saved-searches`; the Android transport sends explicit UTF-8 bytes with `Content-Length` after a real-device-emulator save failed with HTTP 400 body parsing.
- Remaining gaps: Updates/Sources are still clear placeholder tabs, job detail is basic, no iOS golden side-by-side artifact is generated, and data count mismatch remains (`/api/health` reported 2,420 open jobs while Search returned 2,274 results for Open only).
