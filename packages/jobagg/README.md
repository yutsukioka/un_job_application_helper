# jobagg

`jobagg` is a Python CLI scaffold for aggregating job posts across recurring ATS
families: Workday, Oracle Taleo, SAP SuccessFactors / RMK-style career sites,
Oracle Fusion Cloud HCM Candidate Experience, PageUp, SmartRecruiters, Workable,
iCIMS, Cornerstone / CSOD, USAJobs, and custom or legacy HTML portals.

The package is intentionally adapter-first. Each organization source declares its
ATS family in `config/organizations.yaml`, the sync pipeline resolves that family
to an adapter, and normalized job records are persisted into SQLite with stable
content hashes for change detection.

## Quick Start

```bash
python -m pip install -e "packages/jobagg[dev]"
jobagg init-db
jobagg sync
jobagg export --format json --output private/jobagg/output/jobs.json
```

For regular WFP monitoring, use stable output paths rather than timestamped
snapshots:

```bash
jobagg --db private/jobagg/output/wfp_jobs.sqlite3 refresh-deadlines \
  --source-id wfp_workday \
  --json-output private/jobagg/output/wfp_jobs_current.json \
  --csv-output private/jobagg/output/wfp_jobs_current.csv \
  --history-json-output private/jobagg/output/wfp_jobs_history.json \
  --history-csv-output private/jobagg/output/wfp_jobs_history.csv
```

`refresh-deadlines` first refreshes the listing, then fetches detail pages only
for new postings, postings missing a stored deadline, and postings closing within
the configured deadline window. Use `--refresh-all-details` only for occasional
full audits. Source-local current exports include only `status = open`; source-local
history exports are the audit/history view for that source bundle. Consolidated
all-source exports apply the stricter current/history lifecycle described below.

For all enabled Workday sources, write one stable database and export bundle per
agency:

```bash
jobagg refresh-deadlines --separate-by-source --output-dir private/jobagg/output
```

This creates files such as `private/jobagg/output/wfp_jobs.sqlite3`,
`private/jobagg/output/wfp_jobs_current.json`, `private/jobagg/output/wfp_jobs_current.csv`,
`private/jobagg/output/wfp_jobs_history.json`, and `private/jobagg/output/wfp_jobs_history.csv`. The agency
prefix comes from `extra.output_slug` in `config/organizations.yaml`. Add
`--refresh-all-details` for an occasional full detail audit; omit it for regular
low-impact refreshes.

For all configured job sources, prefer the canonical bundle command. It writes
exactly five files per organization slug:

```bash
jobagg sync-bundles --output-dir private/jobagg/output
```

Each organization gets:

- `{slug}_jobs.sqlite3`
- `{slug}_jobs_current.json`
- `{slug}_jobs_current.csv`
- `{slug}_jobs_history.json`
- `{slug}_jobs_history.csv`

`sync-bundles` stages source results first, publishes one canonical bundle per
organization, archives old or noncanonical root files outside `private/jobagg/output/`, and
validates that each SQLite database has no duplicate `external_id` or
`apply_url` within the same organization. The staged database is seeded from the
existing canonical `{slug}_jobs.sqlite3` before each sync, so change history is
preserved across regular bundle runs.

### Routine Update Command

For normal operation, run:

```bash
cd packages/jobagg
uv run python -m jobagg.scheduler --verbose sync-bundles \
  --output-dir ../../private/jobagg/output \
  --keep-extra-output-files \
  --allow-source-degraded
```

This is the command to keep current and history bundles updated:

- Closed postings: when a source list run is complete and safety gates pass, jobs
  no longer present move out of `{slug}_jobs_current.*` / `all_jobs_current.*`
  and remain in `{slug}_jobs_history.*` / `all_jobs_history.*` with status
  `missing` or `closed`.
- Changed postings: changed list-level fields are updated in SQLite and exports.
  Detail refresh is queued when a posting is new, the listing hash changed,
  required detail fields are missing, a previous transient detail failed, the
  detail record is stale by source policy, or the deadline is close enough to
  require rechecking. Closing-date extensions or shortening are therefore
  captured from list fields immediately, and from detail fields when the detail
  refresh rule selects that posting.
- New postings: new IDs are inserted into the source bundle and consolidated
  into `all_jobs_current.*`.

The command intentionally does not refetch every unchanged complete detail page
on every run. That protects degraded sources from unnecessary load and prevents
detail failures from creating false closures. For an occasional detail audit,
add `--refresh-all-details`; source-specific caps, cooldowns, and circuit
breakers still apply, so remaining detail work stays queued for later safe runs.

For consolidated all-source outputs, `all_jobs_current.*` means current
searchable postings in the consolidated view. Consolidation excludes stale
source rows and moves postings whose deadline has been expired beyond the
24-hour grace window to `all_jobs_history.*` with status `expired`. Weak-detail
open rows remain in `all_jobs_current.*` so real open postings are not hidden,
but they are visibly flagged with `detail_quality_status` (`placeholder_only`,
`list_only`, `too_short`, or `empty`) and remain queued for detail refresh.

`trusted_current` and `application_ready` remain internal SQLite/health-report
fields for diagnostics and backward compatibility. They are no longer published
as canonical sidecar files; the canonical lifecycle exports are current,
history, and the consolidated SQLite database.

### Interactive Browser Cookie Assist

Some detail pages, especially UNICEF/PageUp, can return an AWS WAF human-check
page while the listing endpoint remains healthy. Do not use this mode from cron.
Use it only in an interactive terminal when detail backlogs are blocked by a
human verification page.

1. Run the command with reactive browser assist. It runs normally first and only
   opens the browser if the source hits a WAF/no-detail style block.

```bash
cd packages/jobagg
uv run python -m jobagg.scheduler --verbose sync-bundles \
  --source-id unicef_pageup \
  --output-dir ../../private/jobagg/output \
  --keep-extra-output-files \
  --allow-source-degraded \
  --refresh-all-details \
  --browser-cookie-assist-on-block
```

2. If the browser opens, complete the human check. Open the browser developer
   tools, inspect a successful request to `jobs.unicef.org`, and copy only the
   `Cookie:` request header value. Paste it into the hidden terminal prompt.

To avoid pasting a cookie into shell history, store it in a temporary private
file and pass the file instead. With `--browser-cookie-assist-on-block`, this
file is read only if a block is detected:

```bash
umask 077
pbpaste > /tmp/jobagg-unicef-cookie.txt

cd packages/jobagg
uv run python -m jobagg.scheduler --verbose sync-bundles \
  --source-id unicef_pageup \
  --output-dir ../../private/jobagg/output \
  --keep-extra-output-files \
  --allow-source-degraded \
  --refresh-all-details \
  --browser-cookie-assist-on-block \
  --browser-cookie-file /tmp/jobagg-unicef-cookie.txt
```

The Cookie header is injected only into the selected source for that run. The
aggregator does not read browser profile databases and does not persist the
cookie into `organizations.yaml`.

Use `--browser-cookie-assist` instead of `--browser-cookie-assist-on-block` only
when you intentionally want to open the browser and provide a Cookie header
before the first fetch attempt.

### Degraded Sources And Exit Codes

`sync-bundles` separates publish success from source health:

- Exit `0`: consolidation/export/publishing succeeded and selected sources were
  healthy.
- Exit `2`: consolidation/export/publishing succeeded, but at least one source
  was degraded, inconclusive, blocked, parser-failed, or detail-degraded.
- Exit `1`: consolidation/export/publishing failed, database validation failed,
  or missing/closed safety gates were violated.

Use `--allow-source-degraded` only for automation that should treat a safe
publish-with-warnings as shell success. The health report still records
`publish_result: success_with_source_warnings`, the warning sources, and
`source_health_exit_code: 2` even when the process exits `0`.

Every run writes `sync_bundles_health.json` in the output directory unless
`--health-report-output` is provided. Automation should read this file instead of
inferring health from the shell code alone. Key fields include `publish_result`,
`fatal_errors_count`, current/history/total counts, current detail-complete and
weak-detail counts, expired-moved-to-history counts,
degraded/inconclusive/adapter-broken/detail-quality source counts, and
per-source fetched count, pagination/verified-empty flags, missing-transition
gate state, detail attempt counts, detail backlog counts, consolidated open /
trusted / stale / expired / weak-detail counts, circuit-breaker state,
cooldowns, and last error summary.

Detail enrichment is intentionally non-fatal when listing discovery is healthy.
List discovery controls open/missing/closed transitions, and those transitions
are allowed only when scope and pagination gates pass and zero-fetched runs are
explicitly verified empty. Detail failures leave work in the persistent backlog
and may classify the source as `publishable_detail_degraded` or
`publishable_list_only`; they must not close jobs.

### Consolidated All-Jobs Quality Rules

`all_jobs.sqlite3` is stricter than the per-source bundles. Source bundles keep
the source-local fetch history as observed, while consolidation builds a
canonical current view:

- `detail_backlog`, `source_circuit_breakers`, latest source diagnostics, and
  consolidation source status are copied into `all_jobs.sqlite3` so automation
  can audit detail completeness from the consolidated database.
- Open rows from stale split-Inspira view sources such as
  `isa_inspira_split` and `itc_inspira_split` are quarantined with
  `status = stale_current` instead of being exported as current. These sources
  are agency-filter views of `un_inspira` until a reliable source-specific
  filter is confirmed.
- Duplicate current rows are demoted to `status = duplicate` when they share
  the same `(source_id, ats_family, external_id)` within one configured source,
  or when their normalized `apply_url` matches across sources. Source-native
  external IDs are not treated as globally unique across different
  organizations or Oracle Candidate Experience site numbers. The chosen
  canonical row stays `open`; duplicate relationships are recorded in
  `consolidated_job_aliases`.
- Current rows from stale, unknown, or inconclusive sources keep an explicit
  `stale_current` flag and source-health columns. This prevents old source data
  from silently looking equivalent to a freshly verified current posting.
- Consolidation writes `detail_quality_status` as content quality, independently
  from the queue's
  `detail_status`. Values include `complete`, `empty`, `placeholder_only`,
  `too_short`, `list_only`, and `detail_missing`. Rows whose queue status was
  `complete` but whose stored detail is only a placeholder such as
  `Duties and Responsibilities`, `.`, title-only text, or `Apply by: ...` are
  requeued as pending for later detail refresh.
- Consolidation writes `deadline_state` (`future`, `today`, `expired`,
  `unknown`), `trusted_current`, and `application_ready`. `trusted_current` is
  true only for open rows from non-stale sources whose deadline is not expired.
  `application_ready` additionally requires `detail_quality_status = complete`.
  These fields are internal quality flags, not separate canonical output files.
- `sync_bundles_health.json` includes sources that contribute consolidated
  current rows even if they were not part of the latest selected live run. This
  keeps consolidated-only stale sources visible to automation.

Canonical current exports (`all_jobs_current.*`) include consolidated
`status = open` rows. History exports (`all_jobs_history.*`) include rows moved
out of current (`closed`, `missing`, `expired`, `stale_current`, `duplicate`,
and other non-open statuses) with `consolidation_status` for auditability.
`sync_bundles_health.json` reports `current_detail_complete_count`,
`current_detail_weak_count`, and `expired_moved_to_history_count` so app search
quality can be monitored without introducing extra canonical datasets.

List circuit breakers open after repeated list failures or unsafe zero-fetched
incomplete runs, then cool down before a half-open probe. Detail breakers open
when detail failures are systemic, so the sync stops draining the full backlog
and performs small periodic probes instead. Transient detail host breakers cool
down after repeated timeout/429/503/504/remote-closed failures. Expired open
breaker cooldowns are normalized to half-open during diagnostics and
consolidation. After fixing a source adapter or waiting through cooldown, run a
dry-run diagnostic first:

```bash
jobagg source-health-report --dry-run \
  --sources undp_oracle_hcm,unicef_pageup,icc_successfactors_legacy,ctbto_successfactors_legacy,iom_oracle_hcm \
  --output-dir private/jobagg/output
```

Some sources have explicit source-specific fallbacks. `ipu_static_html` uses a
reader-proxy markdown view because direct static HTTP requests to the official
IPU vacancies page are Cloudflare-blocked from the sync environment; the parser
preserves the official IPU vacancy URLs as `apply_url` and `source_url`.

## Change Detection

`jobagg` tracks two stable keys:

- Identity key: `{org_key}:{source_vacancy_id}` when the source exposes an ID.
  In the current schema `org_key` is the configured `source_id`, which preserves
  compatibility with existing SQLite bundles. If a source does not expose an ID,
  it falls back to a hash of `org_key`, normalized title, normalized location,
  and closing date.
- Content hash: SHA-256 over title, location, grade, contract type, closing
  date, and description text. Apply URLs, source URLs, transient status, and
  posting IDs are excluded so URL churn does not create false updates.

Persistence rules:

- New identity key: upsert result `inserted`, change event `created`, and a
  `vacancy_snapshots` row.
- Same identity key with changed content hash: upsert result `updated`, change
  event `updated`, and a new snapshot.
- Same identity key reappearing after `missing` or `closed`: change event
  `reopened`.
- Not seen for `--missing-run-threshold` consecutive successful source runs:
  status becomes `missing`.
- Past closing date and not seen: status becomes `closed`.

HTTP behavior is intentionally conservative: the pipeline is sequential per
source, so per-domain concurrency is effectively one; requests enforce the
configured minimum crawl delay before every attempt without jitter; transient
HTTP 429, 500, 502, 503, and 504 responses use bounded exponential backoff.
HTTP 403 is not retried and stops the current source sync.

For one-off implementation testing across disabled real sources, use:

```bash
jobagg sync-bundles \
  --include-disabled \
  --ignore-robots-txt \
  --full-sync \
  --skip-source-id unaids_sharepoint \
  --output-dir private/jobagg/output
```

Sample sources remain excluded unless `--include-samples` is provided. If
multiple source variants map to the same `extra.output_slug`, the successful
variant with the largest fetched count is published and the others are archived.

## Layout

- `config/organizations.yaml`: source registry grouped by organization and ATS
  family.
- `config/robots_policy.yaml`: crawl policy defaults, rate limits, and per-domain
  overrides.
- `jobagg/adapters/`: one adapter per ATS family plus custom HTML and optional
  Playwright-assisted discovery.
- `jobagg/pipelines/`: source sync, change detection, and export workflows.
- `jobagg/db.py`: SQLite schema and upsert/change-event persistence.
- `jobagg/models.py`: normalized dataclasses shared by adapters and pipelines.

## Configuration Shape

```yaml
sources:
  - id: example_workday
    name: Example Workday Org
    ats_family: workday
    base_url: https://example.wd1.myworkdayjobs.com
    enabled: true
    extra:
      api_url: https://example.wd1.myworkdayjobs.com/wday/cxs/example/site/jobs
```

Adapters accept `extra` values for platform-specific endpoint hints such as
tenant names, company slugs, career-site paths, query parameters, or known API
URLs. If a source cannot be fetched safely with a static HTTP request, the
`playwright_discovery` adapter can be used as a discovery aid rather than the
default crawler.

For Workday Candidate Experience sites, prefer the public CXS endpoint discovered
from a logged-out HAR:

```yaml
extra:
  cxs_base_url: https://wd3.myworkdaysite.com/wday/cxs/wfp/job_openings
  page_size: 20
  fetch_details: false
```

The Workday adapter posts to `{cxs_base_url}/jobs` with `appliedFacets`, `limit`,
`offset`, and `searchText`. If `fetch_details` is true, it then fetches
`{cxs_base_url}/job/{externalPath}` for description and closing-date enrichment.
Do not capture or replay login, user profile, application, or apply-flow calls.

For low-impact operation, keep `fetch_details: false` in `organizations.yaml` and
use the `refresh-deadlines` command. Workday listing pages do not expose the WFP
deadline field, so deadline changes can only be confirmed from detail pages.

For Taleo faceted search sites, use the logged-out HAR request to
`/careersection/rest/jobboard/searchjobs`:

```yaml
extra:
  search_url: https://example.taleo.net/careersection/ex/jobsearch.ftl
  search_api_url: https://example.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal=...
  detail_url_template: https://example.taleo.net/careersection/ex/jobdetail.ftl?job={job_id_url}
  tz: GMT+02:00
  tzname: Europe/Zurich
  column_fields: [title, external_id, location, closingDate]
  search_payload:
    multilineEnabled: true
    sortingSelection:
      sortBySelectionParam: "3"
      ascendingSortingOrder: "false"
    fieldData:
      fields: {}
      valid: true
    filterSelectionParam:
      searchFilterSelections: []
    advancedSearchFiltersSelectionParam:
      searchFilterSelections: []
    pageNo: 1
```

The adapter opens the public search page first when needed, then posts the
payload with the same anonymous headers seen in the HAR. `column_fields` maps
Taleo's compact `column` array into normalized fields; use `fetch_details:
false` for routine listing/deadline refreshes and reserve detail fetching for
manual full-content audits.

For PageUp sites, look for the AJAX request to a language-specific filter path
such as `/en-us/filter/`. UNICEF uses a POST whose JSON response contains HTML
in `results`, plus `page`, `pageitems`, and `count` for pagination:

```yaml
extra:
  listing_url: https://jobs.example.org/en-us/listing/
  filter_url: https://jobs.example.org/en-us/filter/
  page_size: 20
  max_pages: 25
  fetch_details: false
  query:
    search-keyword: ""
```

The PageUp adapter parses listing HTML for title, job id, location, summary, and
deadline. Detail pages can be fetched with `fetch_details: true`, but regular
refreshes should keep it false when the listing already includes deadlines.

For UN Careers / Inspira, use the public Angular API visible in a logged-out HAR:

```yaml
extra:
  list_url: https://careers.un.org/api/public/opening/jo/list/filteredV2/en
  detail_api_url_template: https://careers.un.org/api/public/opening/joV2/{job_id}/en
  detail_url_template: https://careers.un.org/jobSearchDescription/{job_id}?language=en
  page_size: 50
  max_pages: 25
  fetch_details: false
  filter_config: {}
```

The listing API already includes title, duty station, department/category,
posting and closing dates, and job-description HTML. Keep `fetch_details: false`
for routine refreshes; use detail fetching only when the direct `inspiraURL`
apply link is needed.

For UNV, use the public UVP search endpoint:

```yaml
extra:
  api_url: https://app.unv.org/api/doa/doa/SearchDoaAsyncByAzureCognitive
  detail_api_url_template: https://app.unv.org/api/doa/doa/{job_id}
  page_size: 50
  fetch_details: false
```

For Oracle HCM Candidate Experience sites, configure both the `site_number` and
the expected site name. Multiple organizations can share the same Oracle host,
so the adapter validates `siteSettings/{site_number}` before updating jobs,
then paginates until the source-reported total is satisfied. A site can also
host multiple agency scopes, so Oracle diagnostics record agency and
organization facet counts separately from the stable `source_id`:

```yaml
extra:
  site_number: CX_1001
  expected_site_name: UN Women
  page_size: 100
  max_detail_pages_per_run: 25
  fetch_details: false
```

`max_detail_pages_per_run` bounds selective detail enrichment during routine
bundle runs. Listing fetches still complete and write jobs first; any remaining
detail pages stay pending for later runs instead of blocking the source.

For SuccessFactors/RMK, the adapter supports JSON POST (`api_url`), static HTML
listings (`search_url`), RSS feeds (`rss_url`), and XML feed mode for legacy RCM
sites that expose `career_ns=job_listing_summary&resultType=XML`. Treat zero-job
results as valid only when the feed or page provides explicit empty evidence.

For Avature portals such as UNOPS, configure the public `SearchJobs` page:

```yaml
extra:
  listing_url: https://careers.example.org/careersmarketplace/SearchJobs
  page_size: 6
  fetch_details: false
```

Some HARs expose endpoints that are not enabled by default:

- SharePoint-backed pages require authentication and should remain manual unless
  a public anonymous job-list endpoint is available.
- Some CSOD, Oracle HCM, Workable, SmartRecruiters, and SuccessFactors API hosts
  are parseable but currently fail authentication or are blocked by robots.txt.
  Their sources can stay in `organizations.yaml` as disabled templates until you
  explicitly decide whether to use an allowed public fallback or a vetted robots
  override.

## Robots and Crawl Policy

The CLI is built around conservative crawling:

- use a clear user agent;
- honor robots.txt unless explicitly overridden for a vetted internal use case;
- apply per-source and per-domain rate limits;
- avoid aggressive pagination until an adapter has platform-specific bounds.

The defaults live in `config/robots_policy.yaml`.

## Development

```bash
cd packages/jobagg
pip install -e ".[dev]"
pytest
```
