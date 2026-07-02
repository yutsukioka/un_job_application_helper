# iOS vs Android Visual Review Package

Date: 2026-07-03
Branch: `codex/atlas-flutter-android-parity`
Android evidence set: `screenshots/filter-cache-icons-emulator-20260703/`
Current refreshed Android evidence set:
`screenshots/filter-cache-icons-emulator-20260703-current/`

## Evidence Boundaries

The repo currently contains checked-in iOS Search-top screenshots only:

- `screenshots/ios-reference/iteration-7/ios_search_seeded_top.png`
- `screenshots/ios-reference/iteration-8/ios_search_top.png`

No local iOS reference screenshots exist for the filter sheet, cascade states, Job Detail, Saved,
Updates, Sources, or Settings. The user-provided iOS filter screenshots are therefore not available
as files in this worktree. Those screens are reviewed below against the written iOS requirements and
the Swift source structure, not against a pixel-paired iOS image.

Swift source references used for non-screenshot review:

- `apps/apple/Sources/AtlasUI/SearchScreen.swift`: iOS tab structure and Search navigation shell.
- `apps/apple/Sources/AtlasUI/AtlasSearchFilters.swift`: iOS filter model, active chip behavior,
  status/location/scope/grade/organization/capability filter fields, and chip removal semantics.
- `apps/apple/Sources/AtlasUI/JobDetailView.swift`: iOS detail screen hierarchy, classification
  rows, deadline panel, source link, and raw-record disclosure.

## Search Top Side-by-Side

Generated side-by-side image:

![iOS and Android Search top side-by-side](screenshots/filter-cache-icons-emulator-20260703/ios_android_search_top_side_by_side.png)

| Review item | Status | Evidence |
| --- | --- | --- |
| Centered `Search` title | Pass | Both screenshots show centered `Search`. |
| Top-right filter/bookmark group | Pass with style note | Android uses a rounded pill with Cupertino-style sliders/bookmark. Checked-in iOS reference has the same grouping concept but a different filled filter glyph. |
| Search box under header | Pass | Both place search field directly below the header controls. |
| Compact filter chips | Pass | Android chips are compact and horizontally arranged. |
| Result count and local-save text | Pass | Current Android evidence shows `2,269 searchable results` and compact local save timestamp. |
| Sort link alignment | Pass | Android sort control is compact and right aligned. |
| Compact list rows | Pass against current product requirement | Android rows are compact and hide diagnostic text. Checked-in iOS screenshot is stale and still shows diagnostic explanation text in rows. |
| Bottom navigation | Pass | Both show Search, Saved, Updates, Sources, Settings. |

## Android Screen Evidence

Single-image Android review contact sheet:

![Android evidence contact sheet](screenshots/filter-cache-icons-emulator-20260703/android_review_contact_sheet.png)

| Screen / state | iOS local reference | Android evidence | Review result |
| --- | --- | --- | --- |
| Search top | `screenshots/ios-reference/iteration-8/ios_search_top.png` | `screenshots/filter-cache-icons-emulator-20260703/search_top_refreshed.png` | Human-reviewable side-by-side generated. |
| Search top, current count/no banner | `screenshots/ios-reference/iteration-8/ios_search_top.png` | `screenshots/filter-cache-icons-emulator-20260703-current/search_refreshed_no_banner.png` | Current release shows `2,269 searchable results`, compact local-save text, and no large normal-state cache banner. |
| Search scrolled | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/search_scrolled.png` | Android evidence captured; no local iOS pair. |
| Filter sheet top | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_top.png` | Matches written iOS requirements: dark sheet, drag handle, title, Done, sticky Reset/Apply, count pills. |
| Filter Contract/Seniority | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_contract_seniority.png` | Android evidence captured. |
| Filter Grade/CCOG | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_grade_ccog.png` | Android evidence captured. |
| Filter Organizations/Work Mode | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_organizations_workmode.png` | Android evidence captured. |
| Filter Capability Tags | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_capability_tags.png` | Android evidence captured. |
| Country -> City cascade | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_japan_selected.png` | `JPN` selected; city options narrow to `TOKYO`. |
| City -> Country cascade | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_tokyo_selected.png` | `TOKYO` selected; `JPN` remains visible as matching country option. |
| Multi City/Country values | Swift source has string fields; product requirement asks Android multi-select | `screenshots/filter-cache-icons-emulator-20260703/filter_japan_selected.png`, `screenshots/filter-cache-icons-emulator-20260703/filter_tokyo_selected.png`; tests in `test/atlas_filters_test.dart` and `test/atlas_search_controller_test.dart` | Android now accepts comma/semicolon-separated location text and multi-select pills; values serialize as Search API lists and filter cached rows as OR within Location. |
| Seniority -> Grade cascade | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_entry_junior_selected.png` | `Entry Junior` selected; Grade options narrow. |
| Grade selected | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/filter_grade_selected.png` | Grade selected state captured. |
| Offline restart | Not applicable | `screenshots/filter-cache-icons-emulator-20260703-current/offline_restart_no_banner.png` | Cached rows visible immediately after offline relaunch with no large normal-state cache banner. |
| Settings cache status | Missing locally | `screenshots/filter-cache-icons-emulator-20260703-current/settings_after_refresh.png` | Shows cache timestamp, freshness, cached/search/health counts, refresh and clear controls with current `2,269` count. |
| Job Detail | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/job_detail.png` | Shows populated detail, core fields, weak-detail state, description, and save affordance. |
| Saved tab | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/saved_tab.png` | Implemented screen captured. |
| Updates tab | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/updates_tab.png` | Implemented screen captured. |
| Sources tab | Missing locally | `screenshots/filter-cache-icons-emulator-20260703/sources_tab.png` | Implemented screen captured. |

## Visual Checklist

- [x] Android title says `Search`.
- [x] Top-right filter/bookmark controls are visually grouped.
- [x] Search box is directly below the title/control area.
- [x] Applied filters are compact chips.
- [x] Results count and local-save status are compact.
- [x] Normal cache-load state does not show a large blue Search banner.
- [x] Sort control is compact and right aligned.
- [x] Job rows are compact and hide diagnostic paragraphs.
- [x] Bottom tabs are Search, Saved, Updates, Sources, Settings.
- [x] Filter sheet uses dark modal styling with `Filters`, `Done`, and sticky `Reset` / `Apply filters`.
- [x] Filter sheet exposes all required groups from the written iOS reference.
- [x] City/Country and Seniority/Grade cascade states have Android screenshot evidence.
- [x] Multi City/Country values are implemented in Android request serialization and offline cached filtering.
- [x] One-page Android evidence contact sheet is generated for human review.
- [ ] Physical Pixel screenshots are captured after unlock.
- [ ] User-provided iOS filter screenshots are checked into or copied into the repo for true pixel-paired comparison.

## Remaining Visual Differences / Risks

- The checked-in iOS Search screenshot appears stale relative to the user's latest requirements:
  it includes diagnostic explanation text in list rows, while the requirement now says diagnostics
  must be hidden from the main list.
- Android currently uses source monograms with a light cyan background. The checked-in iOS screenshot
  uses stronger per-source color blocks. This should receive human review.
- Android filter icon style is Cupertino-style sliders, but not a pixel-identical clone of the
  filled iOS glyph in the checked-in reference.
- Android Location filters now support multiple City/Country values, but the visible text-field
  representation is comma-separated. Human review should decide whether this is visually close
  enough to iOS or should become a dedicated selected-chip editor.
- Physical Pixel rendering may differ from the emulator capture; the physical device must be
  unlocked and reviewed before this can be treated as final.

## Score Impact

This package satisfies the "create a reviewable evidence package" requirement for available local
artifacts, but it does not satisfy the final human gate because most iOS reference images are missing
locally and physical Pixel screenshots are still blocked. The strict completion score remains below
80 until those two gaps are closed.
