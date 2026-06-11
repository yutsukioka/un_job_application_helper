# iPhone and Mac Search App Spec

Status: draft product and architecture spec  
Scope: native iPhone and Mac app for searching the local `jobagg` vacancy database  
Primary data authority: local server-side job aggregator, not client-side job storage

## 1. Purpose

Build a native iPhone and Mac search interface over the latest `job_aggregator` functionality. The app should provide the fast, readable job-discovery experience of UN Talent while exposing the richer classification, confidence, scoring, and saved-search capabilities already present in `jobagg`.

The client apps must not own the canonical job lists or job detail records. Current vacancy lists, job descriptions, classification evidence, source-run health, and historical update state stay on the local server side for now, backed by SQLite and the existing bundle outputs.

## 2. Source Research Summary

Observed UN Talent patterns worth adapting:

- Top-level navigation is simple: Openings, Search, Account, Pro.
- The openings page prioritizes a readable stream: organization logo/name, title, location, short summary, freshness/deadline, tags, More info, Apply.
- Search is a preference builder rather than a raw database form. Users select work areas, contract category, locations, remote setting, exact contracts, and organizations.
- Pro search unlocks region-level locations, up to 10 location selections, exact contract-grade filtering, and a large organization/office selector.
- Alerts are saved searches rendered as natural-language sentences, with Edit and Jobs actions.
- The dashboard doubles as an application tracker with per-job status chips and a Manage action.
- Job detail pages expose full source text, source links, apply link, related jobs, and a Pro assistant/advice action.
- The visual language is friendly and low-friction: bright header, compact nav, rounded filter rows, grouped fieldsets, short result cards.

Adaptation principle:

Use UN Talent's clarity and progressive disclosure, but avoid a web-style wall of hundreds of checkboxes. Native iPhone/Mac should use search bars, chips, searchable pickers, grouped filter sheets, result density controls, and split views.

## 3. Local Job Aggregator Capabilities To Expose

Current `jobagg search` supports:

- Full-text search across title, description, and location.
- Status filters: open, missing, closed.
- Organization and source filters.
- ATS family filters.
- Location filters: city, country, region, location type.
- Scope filters: international, national, local, global remote.
- Contract filters: contract category, normalized contract group, subtype where available.
- Grade filters: grade system, grade family, exact grade code.
- CCOG filters: exact primary code and family/prefix.
- Derived occupational taxonomy filters: occupational family and medium codes.
- UN mandate taxonomy filters: mandate network and family codes.
- Capability tag filters.
- Seniority group filters.
- Work modality filters: onsite, home_based, online_remote, hybrid, multiple_locations, unknown.
- UNV filters: category and volunteer type.
- Posted and closing date ranges.
- Confidence thresholds for location and grade, with an exploratory low-confidence mode.
- Pagination, sorting, CSV/JSON/Markdown output.
- Per-result match evidence for location, grade, and scope.
- Facet counts for active searches.
- Saved searches stored as JSON.
- Strategy-fit scoring via `--score-against` and `--min-score`.
- Search debugging and filter explanations.

Current data model includes:

- `jobs`: canonical postings, source identity, title, location, dates, URLs, description, status, hashes, first/last seen, missing count.
- `vacancy_classifications`: CCOG, occupational, mandate, capability, contract, grade, seniority, location, work modality, UNV, confidence, review flags.
- `vacancy_locations`: normalized multi-location evidence used by search.
- `source_runs` and `source_run_diagnostics`: sync health, pagination/scope validation, detail-refresh status, missing-transition safety.
- `change_events` and `vacancy_snapshots`: update history and material-change tracking.
- Per-organization bundles plus consolidated `output/all_jobs.sqlite3`, `all_jobs_current.json`, and `all_jobs_history.json`.

Important implementation note:

Some newer taxonomy fields can be sparse depending on the database's classification/backfill state. The UI must show coverage/confidence plainly and must not imply every facet is complete when the database has no values for it.

## 4. Product Goals

1. Make high-signal UN and international-organization vacancy search fast on iPhone and Mac.
2. Preserve server-side control over job lists, full details, updates, sync, and classification.
3. Expose advanced `jobagg` filters without overwhelming the user.
4. Make saved searches readable, editable, and runnable.
5. Support "search for my target application profile" via strategy scoring.
6. Show why a job matched: location evidence, grade evidence, scope reason, taxonomy evidence, and confidence.
7. Keep future paths open for alerts, application tracker, and application-assistant integration.

## 5. Non-Goals For MVP

- No public multi-user cloud service.
- No client-owned job database.
- No direct scraping from the iPhone or Mac client.
- No account/password system unless remote access is added later.
- No automatic external job applications.
- No cloning of UN Talent's exact visual design, content, or proprietary flow.

## 6. Target Platforms

Use one SwiftUI codebase:

- iPhone: tab-based navigation, filter sheet, result list, detail push.
- Mac: `NavigationSplitView` with sidebar, results column, detail pane, inspector panel.
- Shared model layer using async API client and local lightweight preferences.

Minimum platform recommendation:

- iOS 18+
- macOS 15+
- Swift 6+

## 7. Information Architecture

Primary app sections:

1. Search
2. Saved Searches
3. Updates
4. Sources
5. Settings

Optional later sections:

- Application Tracker
- Strategy Fit
- Assistant

### iPhone Layout

Search tab:

- Top search bar.
- Horizontal "active filters" chip row.
- Results count and sort control.
- Result list cards.
- Filter button opens a sheet with grouped sections.
- Detail page opens as navigation push.

Saved Searches tab:

- List of saved search summaries.
- Run, Edit, Duplicate, Delete.
- Natural-language summary on each row.

Updates tab:

- Newly seen, closing soon, changed, missing/closed.
- Source health warnings.

Sources tab:

- Organizations and source health.
- Last run time, open count, latest status.

Settings tab:

- Server connection.
- Sync behavior.
- Cache policy.
- Appearance and result density.

### Mac Layout

Use three-pane layout:

- Sidebar: saved searches, quick facets, sources.
- Results column: search bar, chips, sort, result list.
- Detail pane: selected job detail.
- Optional inspector: match evidence, source diagnostics, history.

## 8. Search Screen Design

Top area:

- Search field: placeholder "Title, keyword, skill, or organization".
- Primary chips:
  - Open only
  - Closing soon
  - Remote
  - International
  - P-2 to P-4
  - Consultant
  - Nairobi/Kenya
  - Scored for target
- Sort menu:
  - Closing soon
  - Newest posted
  - Deadline latest
  - Best strategy fit
- Density toggle on Mac:
  - Comfortable
  - Compact

Filter sheet sections:

1. Location
   - City picker with search.
   - Country picker with ISO/name normalization.
   - Region picker.
   - Location type picker: primary, duty station, outposted.
   - Confidence slider or switch: "include uncertain matches".

2. Contract and Grade
   - Contract category.
   - Contract group.
   - Seniority group.
   - Grade family.
   - Exact grade chips.
   - Grade confidence threshold.

3. Work Mode
   - Onsite
   - Home-based
   - Online remote
   - Hybrid
   - Multiple locations
   - Unknown

4. Organization and Source
   - Organization picker.
   - Source ID picker.
   - ATS family picker.
   - Source health filter: OK only, include warning, include issue.

5. Function and Taxonomy
   - CCOG family.
   - CCOG exact code.
   - Occupational family.
   - Occupational medium.
   - Mandate network.
   - Mandate family.
   - Capability tags.

6. UNV
   - UNV category.
   - Volunteer type.

7. Dates and Status
   - Status: open, missing, closed.
   - Posted date range.
   - Closing date range.
   - "Closing within N days" shortcut.

8. Strategy Fit
   - Select strategy signal file or saved profile.
   - Minimum score slider.
   - Show score reasons.

Filter behavior:

- Every selected filter becomes a removable chip.
- Facet counts update after each committed filter change.
- Empty facets should be hidden by default but available under "Show all fields".
- Unknown values should be shown only when useful, not mixed into high-confidence default chips.

## 9. Result Card

Required card fields:

- Title.
- Organization display name.
- Duty station.
- Closing date with urgency label.
- Grade code and normalized grade family.
- Contract category or contract group.
- Work modality.
- Source icon or source initials.
- Match score if strategy scoring is active.
- Needs review badge when `needs_review` is true.
- Confidence badges for location and grade when below threshold or when exploratory mode is active.

Actions:

- Open detail.
- Open source posting.
- Open apply URL.
- Save/unsave job.
- Add to tracker, later phase.

Card content priority:

1. Title and organization.
2. Deadline and duty station.
3. Grade/contract/seniority.
4. Match reason summary.
5. Secondary taxonomy tags.

## 10. Job Detail Screen

Sections:

1. Header
   - Title.
   - Organization.
   - Status.
   - Deadline.
   - Apply and source buttons.

2. Search Match
   - Why this job matched the current query.
   - Matched location, source field, confidence.
   - Matched grade, source field, confidence.
   - Scope reason.
   - Strategy score and reasons if active.

3. Classification
   - Grade mapping.
   - Contract category/group.
   - CCOG and occupational taxonomy.
   - Mandate taxonomy.
   - Capability tags.
   - Work modality.
   - UNV fields when present.

4. Description
   - Server-rendered normalized description.
   - Section-aware display if the backend later extracts responsibilities, qualifications, and competencies.

5. Source and History
   - Source ID and ATS family.
   - First seen, last seen.
   - Posted and closing dates.
   - Change events.
   - Source-run health for latest observation.

6. Actions
   - Open source.
   - Open apply.
   - Copy link.
   - Add to saved jobs/tracker.

## 11. Saved Searches and Alerts

Saved searches should be first-class objects, not just command strings.

Stored fields:

- ID.
- Name.
- Natural-language summary.
- Raw `VacancySearchRequest`.
- Sort.
- Strategy scoring config.
- Created and updated timestamps.
- Optional alert schedule.
- Last run summary: total, new since last run, closing soon, changed, source warnings.

UI behavior:

- List saved searches as readable sentences, similar to UN Talent's alert summaries.
- Run saved search immediately.
- Edit with the same filter sheet.
- Duplicate a saved search.
- Delete only after confirmation.
- Optionally pin saved searches to Search sidebar or iPhone quick chips.

Alert behavior for local-only phase:

- No email from the client.
- Local server can run saved searches on schedule.
- Client displays new counts and local notifications if enabled.
- Notification payload should contain only summary metadata; details are fetched from the server when opened.

## 12. Server-Side Architecture

Add a local HTTP API process around existing Python `jobagg` modules.

Recommended stack:

- Python FastAPI or Starlette.
- Reuse `JobDatabase`, `VacancySearchRequest`, `search_collected_jobs`, `facet_counts`, saved-search helpers, scoring helpers.
- SQLite remains the source of truth.
- Server runs on localhost for MVP.
- Optional launchd agent on Mac for automatic background sync.

Server responsibilities:

- Own `output/all_jobs.sqlite3` and per-source bundles.
- Run `sync-bundles`, `refresh-deadlines`, classification, consolidation, and exports.
- Serve search results and job details.
- Serve facets and taxonomy metadata.
- Store saved searches.
- Compute strategy scores.
- Expose source health and update history.
- Enforce schema migrations before opening the API.

Client responsibilities:

- Present UI.
- Store only user preferences, recent queries, selected server URL, and optional short-lived cached summaries.
- Fetch job details on demand.
- Never scrape external ATS sites.
- Never mutate server-side job records directly.

## 13. API Contract Draft

### `GET /api/health`

Returns:

```json
{
  "status": "ok",
  "db_path": "output/all_jobs.sqlite3",
  "schema_version": "string",
  "open_jobs": 2067,
  "enabled_sources": 39,
  "last_sync_at": "2026-06-09T16:36:00+00:00"
}
```

### `POST /api/search`

Request:

```json
{
  "text": "programme management",
  "status": ["open"],
  "cities": ["Nairobi"],
  "countries_iso3": ["KEN"],
  "grade_codes": ["P2", "P3", "P4"],
  "national_international": ["international"],
  "work_modalities": ["onsite", "home_based"],
  "limit": 50,
  "offset": 0,
  "sort": "closing_date_asc",
  "include_low_confidence": false,
  "score_against": null,
  "min_score": null,
  "include_facets": true,
  "include_explain": false
}
```

Response mirrors `VacancySearchResponse`:

```json
{
  "total": 9,
  "limit": 50,
  "offset": 0,
  "results": [],
  "facets": {},
  "unclassified_count": 0
}
```

### `GET /api/jobs/{job_key}`

Returns full job detail:

- `jobs` fields.
- Classification fields.
- Locations.
- Change events.
- Snapshots summary.
- Latest source-run diagnostics.

### `GET /api/facets`

Returns global or filtered facet counts.

### `GET /api/taxonomies`

Returns display metadata:

- Regions.
- Countries.
- Grade families.
- Contract categories/groups.
- Seniority groups.
- Work modalities.
- CCOG families.
- Mandate networks/families.
- Capability tags.
- Source IDs and display names.

### `GET /api/saved-searches`

Returns saved search list.

### `POST /api/saved-searches`

Creates or updates a saved search.

### `POST /api/saved-searches/{id}/run`

Runs one saved search and returns a search response plus run summary.

### `DELETE /api/saved-searches/{id}`

Deletes a saved search after client confirmation.

### `GET /api/updates`

Returns:

- New jobs since timestamp.
- Updated jobs.
- Closing soon.
- Missing/closed transitions.
- Source health warnings.

### `POST /api/sync/run`

Starts a server-side sync job. For MVP, expose this only on Mac or admin mode.

Request:

```json
{
  "mode": "refresh_deadlines",
  "source_id": null,
  "deadline_refresh_days": 14
}
```

### `GET /api/sync/runs`

Returns recent source runs and diagnostics.

## 14. Data Update Model

Routine local update flow:

1. Server runs `jobagg sync-bundles --output-dir output`.
2. Server publishes per-organization bundles and consolidated `all_jobs` outputs.
3. Server runs classification/backfill when needed.
4. Server records source-run diagnostics.
5. Server computes change events and missing/closed transitions only when diagnostics permit.
6. Clients receive update summaries through polling or local notifications.

Selective deadline refresh:

- Use `refresh-deadlines` for listings plus detail pages requiring deadline validation.
- Detail pages are fetched for new postings, missing stored deadlines, and jobs closing within the configured window.
- Full detail refresh is an occasional maintenance action, not routine app behavior.

Server-side retention:

- Keep canonical SQLite and history on server.
- Keep JSON/CSV exports for interoperability.
- Keep local change history for "new since last run" and "changed since saved search last run".

## 15. Native Design System

Visual direction:

- Bright but restrained header accent inspired by UN Talent cyan.
- Neutral content background.
- Orange reserved for primary action or Pro/strategy-fit emphasis.
- Rounded cards no more than 8px radius unless platform defaults apply.
- Use native SF Symbols for search, filter, clock, bookmark, external link, warning, and refresh.
- Avoid emoji as primary controls in the native app; use icons plus labels.

Interaction rules:

- Use chips for selected filters.
- Use searchable pickers for large vocabularies.
- Use disclosure groups for advanced filters.
- Use segmented controls for status and sort where compact.
- Use sliders only for confidence and score thresholds.
- Use confirmation dialogs for destructive actions.
- Use native share sheets for copy/open actions.

Accessibility:

- Dynamic Type.
- VoiceOver labels for all chips and cards.
- High contrast support.
- Keyboard shortcuts on Mac:
  - Command-F: focus search.
  - Command-Option-F: open filters.
  - Command-R: refresh search.
  - Command-S: save search.
  - Command-Return: open selected job.

## 16. MVP Delivery Phases

### Phase 1: Local API

- Add local API wrapper around current search, facets, details, saved searches, and health.
- Add schema migration check on startup.
- Add endpoint tests using a copied database fixture.

### Phase 2: Mac Search App

- Implement Search, Details, Saved Searches, Sources.
- Use `NavigationSplitView`.
- Support all core filters and facets.
- Support open/apply/source links.

### Phase 3: iPhone Search App

- Reuse API client and models.
- Implement tabs, search list, detail, filter sheet, saved searches.
- Add local notifications for saved-search runs if server is reachable.

### Phase 4: Updates and Sync

- Add Updates tab.
- Add sync run status and source diagnostics.
- Add server-triggered refresh controls for Mac.

### Phase 5: Strategy Fit

- Add strategy profile import/selection.
- Surface score and score reasons.
- Add "best fit" sort.

### Phase 6: Tracker and Assistant Integration

- Add saved jobs/application tracker.
- Add status transitions.
- Add optional assistant/advice integration only after search is stable.

## 17. Open Decisions

- Whether the first backend should be FastAPI or a smaller stdlib HTTP server.
- Whether iPhone connects only to a Mac-local server on the same network or later to a hosted private server.
- Whether saved searches stay in JSON initially or move into SQLite.
- How to display sparse taxonomy fields without clutter.
- Whether to normalize organization display names from `organizations.yaml` at API time.
- How to handle remote access authentication if the server is exposed beyond localhost.

## 18. Acceptance Criteria

MVP is successful when:

- A Mac client can search `output/all_jobs.sqlite3` through the local API without loading all jobs into client storage.
- A user can reproduce common CLI searches through native controls.
- Results show deadline, location, grade, contract, source, and match evidence.
- Facets update for the active query.
- Saved searches can be created, edited, run, and deleted.
- Job detail pages show full server-side data on demand.
- Server can report source health and last update status.
- No external ATS scraping occurs from the iPhone or Mac client.
