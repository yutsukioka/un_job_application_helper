# Job Opening List and Detail Fetching Mechanism

Generated: 2026-05-20

This document explains how `jobagg` collects current vacancy listings, enriches job details, preserves history, and decides whether a source run is operationally safe. It is intended as a reference before expanding normalization and search features.

## Current Completeness Position

The aggregator has a complete current listing snapshot for the successfully fetched enabled sources in the latest full live validation run. That snapshot is stored under `output/_rigorous_20260520` and contains:

- 39 inspected per-organization databases
- 2,101 open jobs
- 36 PASS, 2 WARN, and 1 FAIL in the first rigorous ops check
- A later UNDP-only follow-up with `158` listed jobs, complete Oracle pagination, scope validation passed, and `25/25` detail fetch attempts succeeded

This does not mean every organization has complete detailed enrichment for every open job. Listing completeness and detail completeness are separate:

- Listings are the authoritative current inventory for a source when scope validation and pagination pass.
- Details are enriched in batches for sources whose detail pages are slow, expensive, or only needed for missing fields.
- Historical records are complete only from the first time the local database observed each job. The system does not reconstruct jobs that closed before the first local ingestion unless the upstream source still exposes them.

The regular root database `output/all_jobs.sqlite3` may be older than the latest rigorous validation artifact. Use the newest validated output directory when checking current coverage.

Known current caveats:

- `ctbto_successfactors_legacy` and `globalfund_workday` produced zero jobs with warning-level diagnostics in the latest full run because zero vacancy evidence was not fully verified.
- UNDP Oracle detail pages can take 60-100 seconds. The implementation now uses a longer `detail_timeout_seconds` only for detail calls, while keeping normal listing requests at the regular per-source timeout.
- Disabled sources in `config/organizations.yaml` are not part of the current fetched inventory.

## Core Data Model

Each vacancy has a stable key:

```text
job_key = "{source_id}:{external_id}"
```

The stable `source_id` is part of identity. Changing it creates a new job namespace, so source fixes should preserve IDs unless a source is deliberately split.

The main persistence tables are:

- `jobs`: current and historical vacancy rows. Rows are not deleted during normal sync; status changes to `open`, `missing`, or `closed`.
- `change_events`: created, updated, reopened, missing, and closed events.
- `vacancy_snapshots`: content snapshots when a job is inserted or materially changed.
- `source_runs`: compact per-source run counts and errors.
- `source_run_diagnostics`: structured health, scope, pagination, detail, and zero-fetch evidence.
- `grade_mappings`: the revised organization grade mapping table loaded from `grade_mapping_table_revised.csv`.
- `vacancy_source_features`, `vacancy_classifications`, and `vacancy_locations`: normalization/search layers built after raw fetch.

The output bundle for each organization contains five files:

```text
{slug}_jobs.sqlite3
{slug}_jobs_current.csv
{slug}_jobs_current.json
{slug}_jobs_history.csv
{slug}_jobs_history.json
```

`current` exports contain open jobs only. `history` exports include open, missing, and closed jobs retained in that source database.

## Sync Flow

The normal command is:

```bash
.venv/bin/jobagg sync-bundles --output-dir output
```

For each enabled source:

1. Load source config from `config/organizations.yaml`.
2. Create or open the per-source SQLite database.
3. Build an HTTP client using robots policy, per-source timeout, retries, and delay settings.
4. Instantiate the adapter from `ats_family` or explicit `adapter`.
5. Fetch listing jobs.
6. Optionally enrich selected jobs with detail pages.
7. Upsert jobs into SQLite.
8. Record change events and snapshots for inserts and material updates.
9. Apply classification and location extraction.
10. Export current/history CSV and JSON files.
11. Refresh the consolidated `output/all_jobs.sqlite3` database by default after bundle publication. Use `--skip-consolidate` only when you intentionally want per-source bundle output without refreshing the single all-jobs database.

If an existing output database is used as a seed, the pipeline copies it using SQLite backup semantics. This preserves historical rows, first-seen timestamps, missing counts, and snapshots.

## List Fetching

The list fetch is the primary inventory step. A source run is trusted for missing/closed transitions only when the listing scope is valid and pagination is complete where applicable.

General listing guarantees:

- Adapters deduplicate by `job.identity_key()`.
- Paginated APIs track pages fetched and source-reported totals when available.
- Oracle HCM CE validates `site_number` and expected site name before updating jobs.
- Zero-job results are accepted only when the source has verified empty evidence or no active prior jobs.
- If a listing fetch fails, no missing/closed transition is applied.

### Platform Listing Mechanisms

| Platform | Sources | Listing mechanism | Listing completeness rule |
| --- | --- | --- | --- |
| Workday CXS | WFP, IMF, TBI, UNHCR, WEF, WTO, Global Fund, PAHO | POST to `{cxs_base_url}/jobs` with `limit`, `offset`, facets, and search text | Iterate pages until source total/short page/max page boundary; dedupe by external ID/path |
| Taleo | WIPO, WHO, IAEA, FAO, ADB | REST `jobboard/searchjobs` with source-specific payload and timezone context | Iterate configured pages; parse REST rows and detail URLs |
| PageUp | UNICEF | Listing/filter endpoint configured in source | Parse listing cards; details only when needed |
| UN Inspira | UN Careers | Public opening list API | Paginate by configured page size and sort |
| UNV UVP | UNV volunteer assignments | UNV search API | Paginate API results; keep UNV volunteer market separate from Oracle staff postings |
| Avature | UNOPS | Careers marketplace listing pages | Iterate marketplace pages and parse job cards |
| Oracle HCM CE | ICAO, UNFPA, UNDP, UN Women, WMO | `recruitingCEJobRequisitions` with `findReqs` and source `site_number` | Validate `siteSettings/{site_number}` first, then fetch until unique job count reaches `TotalJobsCount` |
| SuccessFactors RMK | ITU, UNIDO, UNESCO, ICRC | RMK listing pages, API-like payloads, or HTML fallback depending on site | Page through configured listing/search pages |
| SuccessFactors legacy | ICC, CTBTO | ICC uses XML feed first; CTBTO currently legacy listing handling | XML jobs are trusted; zero requires explicit evidence |
| Static/custom HTML | CERN, OSCE, IPU, ITCILO, ITLOS, OPCW, UNSSC, UNU | Configurable public-link, JSON-LD, or custom table parsing | Job links or JSON-LD rows must be found, unless verified structural/text empty evidence is present |
| icddr,b | icddr,b | Source-specific current opportunities endpoint | Fetch configured employee type groups |
| PeopleSoft | IFAD | Public PeopleSoft listing page | Parse search result rows |
| IMO API | IMO | JSON API for current vacancies | Parse API rows |

Disabled sources such as NATO, World Bank CSOD, IOM Oracle, ILO, AU, iDE Global, CERN SmartRecruiters, UNAIDS, ISA split, ITC split, and UN Tourism are not included in the enabled production fetch until their access path or scope is verified.

## Detail Fetching

Details are not fetched blindly for every source on every run. They are fetched when the adapter supports detail enrichment and either:

- the job is new and lacks important detail-only fields;
- the existing closing date is absent or within the refresh window;
- `--refresh-all-details` is explicitly used.

The selective detail path is implemented by `sync_source_with_selective_details()`. It first fetches the full listing with `fetch_details: false`, then calls adapter-level `fetch_detail_for_listing_item()` for selected rows.

Detail calls are isolated per job:

- A failed detail page records an error and keeps the listing-only `JobRecord`.
- Other jobs in the same source continue to sync.
- If at least 5 detail attempts have been made and 50% or more fail, the run aborts further detail calls and records a high-signal detail failure.
- Detail failure ratios are reflected in diagnostics as `ok`, `degraded`, or `issue`.

Detail-only fields are preserved on listing-only updates. If a new listing update omits description, department, employment type, posting date, or closing date, `JobDatabase._merge_existing_detail_fields()` keeps the existing enriched value.

### Detail Budgets

Some sources configure `max_detail_pages_per_run` to avoid long wall-clock runs:

- Oracle CE sources commonly use `25` detail pages per run.
- UNDP currently lists about 158 jobs; a normal detail pass enriches at most 25 per run.
- Skipped detail count is stored in `source_run_diagnostics.detail_skipped`.

Batched retrieval is therefore intentional. To complete a detail backfill, run repeated refresh passes or temporarily raise the per-run detail budget.

Example source-only refresh:

```bash
.venv/bin/jobagg sync-bundles \
  --source-id undp_oracle_hcm \
  --output-dir output/undp_refresh \
  --refresh-all-details
```

### Detail Timeouts

The HTTP client has a normal request timeout and an optional per-call override.

For UNDP Oracle:

```yaml
request_timeout_seconds: 60
detail_timeout_seconds: 120
max_retries: 4
backoff_base_seconds: 2
```

Only detail endpoint calls use `detail_timeout_seconds`. Listing and site-settings calls still use the normal request timeout, so slow detail pages do not slow all Oracle requests.

## Oracle HCM CE Scope

Oracle HCM CE must be scoped by host plus `site_number`, not host alone. The shared host `estm.fa.em2.oraclecloud.com` currently serves multiple organizations:

| Source | Site number | Expected site name |
| --- | --- | --- |
| `undp_oracle_hcm` | `CX_1` | `UNDP` |
| `unwomen_oracle_hcm` | `CX_1001` | `UN Women` |
| `unfpa_oracle_hcm` | `CX_2003` | `UNFPA` |
| `icao_oracle_hcm` | `CX_3001` | `ICAO` |
| `wmo_oracle_hcm` | `CX_5001` | `WMO` |

Every Oracle CE run first calls:

```text
/hcmRestApi/CandidateExperience/en/siteSettings/{site_number}
```

The run aborts safely on site-number or site-name mismatch. This prevents a HAR, browser page, or API URL for UNDP `CX_1` from updating UN Women `CX_1001` records.

Oracle list pages use:

```text
/hcmRestApi/resources/latest/recruitingCEJobRequisitions
  ?onlyData=true
  &expand=...
  &finder=findReqs;siteNumber={site_number},facetsList=...,limit={limit},offset={offset},sortBy=POSTING_DATES_DESC
```

Oracle details use:

```text
/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
  ?expand=all
  &onlyData=true
  &finder=ById;Id="{job_id}",siteNumber={site_number}
```

Hosted agency/flex-field counts are stored as diagnostics. They are classification evidence, not automatic source identity changes. For example, UNDP `CX_1` may include UNDP, UNCDF, and UN Volunteers staff or internship postings, while `unv_uvp` remains the canonical source for volunteer assignments.

## Workday Detail URL Normalization

Workday listing rows can provide `externalPath` with or without `/job/`. The adapter normalizes both forms:

```text
Senior_Economist_JR123              -> /job/Senior_Economist_JR123
/job/WFP_External/Senior_Economist  -> /job/WFP_External/Senior_Economist
```

This avoids generating `/job/job/...` and keeps detail enrichment consistent across Workday tenants.

## Zero-Fetch and Missing/Closed Safety

The pipeline protects existing inventories from parser regressions, blocked pages, and site migrations.

Missing/closed transitions are allowed only when:

- `close_missing` is true;
- `health_status` is `ok` or `ok_empty`;
- pagination is not incomplete;
- scope validation is `passed` or `not_applicable`;
- zero fetched jobs have verified empty evidence.

If a source fetches zero jobs while the DB has active jobs and the run has no verified empty evidence, the pipeline records:

```text
{source.id}: zero jobs fetched for source with active jobs; skipping missing/closed marking
```

and returns without marking existing jobs missing or closed.

Verified empty examples:

- `verified_total_zero`: API/XML response explicitly reports zero jobs.
- `verified_structural_empty`: a static page has expected vacancy-section markers and no job nodes.
- `verified_text_empty`: a static page contains a recognized no-vacancy text marker.

Unverified zeros become warning or issue diagnostics depending on source policy.

## History and Change Semantics

The system preserves history by upserting rows rather than replacing the database.

On every successful observation:

- new jobs create `created` change events and snapshots;
- changed normalized content creates `updated` events and snapshots;
- reopened jobs create `reopened` events;
- missing/closed transitions create status events;
- unchanged jobs update `last_seen_at` but do not create new snapshots.

A historical export is therefore the local observed history, not a complete global archive. If a vacancy was posted and closed before the first successful fetch, it will not appear unless the source still exposes it.

## Operational Commands

Full enabled-source sync:

```bash
.venv/bin/jobagg sync-bundles --output-dir output
```

One source:

```bash
.venv/bin/jobagg sync-bundles --source-id undp_oracle_hcm --output-dir output --refresh-all-details
```

Consolidate source bundles:

```bash
.venv/bin/jobagg consolidate-bundles \
  --output-dir output \
  --summary-output output/organization_summary.csv
```

`sync-bundles` now runs this consolidation step automatically unless `--skip-consolidate` is supplied.

## Grade Standardization

The revised grade mapping CSV is bundled at:

```text
jobagg/classification/rules/grade_mapping_table_revised.csv
```

Every database initialization seeds the `grade_mappings` table from that CSV. Classification then uses the same table to add per-vacancy standardized grade fields into `vacancy_classifications`:

- `grade_mapping_organization`
- `grade_mapping_raw_grade_code`
- `standard_grade_family`
- `standard_seniority_tier`
- `standard_scope`
- `standard_employment_category`
- `standard_un_equivalent`
- `standard_experience_range`
- `standard_role_scope`
- `standard_supervisory_expectations`
- `grade_mapping_confidence`
- `grade_mapping_evidence_type`
- `grade_mapping_notes`

This means a full or detail-refresh run updates source bundles and the consolidated `all_jobs.sqlite3` with both raw listing/detail data and standardized grade metadata. The raw classifier fields such as `grade_code = P4` remain available; the new `standard_*` fields are the cross-organization comparison layer for the next search implementation.

Operational health check:

```bash
.venv/bin/jobagg ops-check --all --output output/ops_check.md
```

Classification audit:

```bash
.venv/bin/jobagg --db output/all_jobs.sqlite3 audit-classification \
  --all \
  --format markdown \
  --output output/classification_audit.md
```

Search with explanations:

```bash
.venv/bin/jobagg --db output/all_jobs.sqlite3 search \
  --city Nairobi \
  --country KE \
  --scope international \
  --grade P2 \
  --grade P3 \
  --grade P4 \
  --status open \
  --explain
```

## Completeness Checks

Use `ops-check` first. A source is operationally trustworthy when it is PASS and its diagnostics show:

- health is `ok` or `ok_empty`;
- scope is `passed` or `not_applicable`;
- pagination is complete;
- zero jobs have verified empty evidence;
- detail failure ratio is acceptable.

Useful SQLite checks:

```sql
SELECT status, COUNT(*) FROM jobs GROUP BY status;

SELECT source_id, COUNT(*)
FROM jobs
WHERE status = 'open'
GROUP BY source_id
ORDER BY source_id;

SELECT sr.source_id,
       sr.fetched,
       d.health_status,
       d.scope_validation_status,
       d.pagination_complete,
       d.total_reported_by_source,
       d.detail_attempted,
       d.detail_succeeded,
       d.detail_failed,
       d.detail_skipped,
       sr.errors_json,
       sr.observed_at
FROM source_runs sr
LEFT JOIN source_run_diagnostics d
  ON d.source_run_id = sr.id
ORDER BY sr.id DESC;
```

For Oracle CE, listing completeness is specifically:

```text
scope_validation_status = passed
pagination_complete = 1
fetched = total_reported_by_source
```

For detail completeness:

```text
detail_failed = 0
detail_skipped = 0
```

If `detail_skipped > 0`, the list is complete but the detail backfill is still batched/pending.

## Practical Interpretation

For day-to-day operation:

- Run full listing sync periodically.
- Keep detail refresh batched for slow platforms.
- Use `ops-check` to decide whether a source run can be trusted.
- Treat `current_json/current_csv` as the current open inventory for PASS sources.
- Treat `history_json/history_csv` as local observed history.
- Use diagnostics before deciding that a source is truly empty.

For the next normalization/search phase, the key input distinction is:

- strong source fields from list APIs and detail APIs should be treated as high-confidence evidence;
- listing-only sparse fields should be normalized with lower confidence;
- skipped details should be visible in search explainability and audit reports when important filters depend on missing detail-only fields.
