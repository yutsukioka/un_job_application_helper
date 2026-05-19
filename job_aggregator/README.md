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
cd .agents/job_aggregator
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
jobagg init-db
jobagg sync --config config/organizations.yaml
jobagg export --format json --output output/jobs.json
```

For regular WFP monitoring, use stable output paths rather than timestamped
snapshots:

```bash
jobagg --db output/wfp_jobs.sqlite3 refresh-deadlines \
  --source-id wfp_workday \
  --json-output output/wfp_jobs_current.json \
  --csv-output output/wfp_jobs_current.csv \
  --history-json-output output/wfp_jobs_history.json \
  --history-csv-output output/wfp_jobs_history.csv
```

`refresh-deadlines` first refreshes the listing, then fetches detail pages only
for new postings, postings missing a stored deadline, and postings closing within
the configured deadline window. Use `--refresh-all-details` only for occasional
full audits. Current exports include only `status = open`; history exports include
all jobs ever seen in the database, including jobs later marked `closed`.

For all enabled Workday sources, write one stable database and export bundle per
agency:

```bash
jobagg refresh-deadlines --separate-by-source --output-dir output
```

This creates files such as `output/wfp_jobs.sqlite3`,
`output/wfp_jobs_current.json`, `output/wfp_jobs_current.csv`,
`output/wfp_jobs_history.json`, and `output/wfp_jobs_history.csv`. The agency
prefix comes from `extra.output_slug` in `config/organizations.yaml`. Add
`--refresh-all-details` for an occasional full detail audit; omit it for regular
low-impact refreshes.

For all configured job sources, prefer the canonical bundle command. It writes
exactly five files per organization slug:

```bash
jobagg sync-bundles --output-dir output
```

Each organization gets:

- `{slug}_jobs.sqlite3`
- `{slug}_jobs_current.json`
- `{slug}_jobs_current.csv`
- `{slug}_jobs_history.json`
- `{slug}_jobs_history.csv`

`sync-bundles` stages source results first, publishes one canonical bundle per
organization, archives old or noncanonical root files outside `output/`, and
validates that each SQLite database has no duplicate `external_id` or
`apply_url` within the same organization. The staged database is seeded from the
existing canonical `{slug}_jobs.sqlite3` before each sync, so change history is
preserved across regular bundle runs.

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
  --output-dir output
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
cd .agents/job_aggregator
pip install -e ".[dev]"
pytest
```
