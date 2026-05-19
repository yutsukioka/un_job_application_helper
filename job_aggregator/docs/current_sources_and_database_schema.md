# Current Sources and Database Schema

This document summarizes the currently enabled fetch sources and the SQLite data model used by `jobagg`.
It is intended as a reference for extending adapters, search filters, classification logic, exports, and operational QA.

Data snapshot:

- Config source: `config/organizations.yaml`
- Latest run metadata: `output/all_jobs.sqlite3.source_runs`
- Structured health metadata: `output/all_jobs.sqlite3.source_run_diagnostics`
- Consolidated database: `output/all_jobs.sqlite3`
- Consolidated open jobs: 2067
- Enabled fetch sources: 39
- Open-job source count: 34

Notes:

- `source_id` is part of the stable job identity. Changing it changes every `job_key`.
- `open_jobs` is the current count of rows with `jobs.status = 'open'`; it can be higher than the latest `fetched` count while missing-run thresholds are still being accumulated.
- `fetch_details` below is the source adapter configuration. `sync-bundles` can still perform selective detail refresh when the adapter supports it.
- `Issue` should be read from `source_run_diagnostics.health_status` when available. `source_runs.errors_json` remains a compact compatibility summary.
- Oracle HCM sources are scoped by `site_number` first. Hosted agency facets such as `UNDP`, `UNCDF`, or `UN Volunteers` are diagnostic/classification evidence and do not by themselves change the configured `source_id`.

## Enabled Fetch Sources

| Source ID | Organization | ATS family | Adapter | Output slug | Host | Fetch details | Allow empty | Latest fetched | Open jobs | Latest status |
|---|---|---|---|---|---|---:|---:|---:|---:|---|
| wfp_workday | World Food Programme | workday | workday | wfp | wd3.myworkdaysite.com | false | false | 124 | 124 | OK |
| imf_workday | International Monetary Fund | workday | workday | imf | imf.wd5.myworkdayjobs.com | false | false | 12 | 12 | OK |
| tbi_workday | Tony Blair Institute | workday | workday | tbi | tbinstitute.wd3.myworkdayjobs.com | false | false | 16 | 16 | OK |
| unhcr_workday | UNHCR | workday | workday | unhcr | unhcr.wd3.myworkdayjobs.com | false | false | 37 | 37 | OK |
| wef_workday | World Economic Forum | workday | workday | wef | weforum.wd3.myworkdayjobs.com | false | false | 30 | 30 | OK |
| wto_workday | World Trade Organization | workday | workday | wto | wto.wd103.myworkdayjobs.com | false | false | 5 | 5 | OK |
| globalfund_workday | The Global Fund | workday | workday | globalfund | theglobalfund.wd1.myworkdayjobs.com | false | true | 0 | 0 | OK |
| wipo_taleo | World Intellectual Property Organization | taleo | taleo | wipo | wipo.taleo.net | false | false | 10 | 10 | OK |
| who_taleo | World Health Organization | taleo | taleo | who | careers.who.int | false | false | 47 | 47 | OK |
| iaea_taleo | International Atomic Energy Agency | taleo | taleo | iaea | iaea.taleo.net | false | false | 30 | 30 | OK |
| fao_taleo | Food and Agriculture Organization | taleo | taleo | fao | jobs.fao.org | false | false | 115 | 115 | OK |
| adb_taleo | Asian Development Bank | taleo | taleo | adb | adb.taleo.net | false | false | 30 | 30 | OK |
| unicef_pageup | UNICEF | pageup | pageup | unicef | jobs.unicef.org | false | false | 202 | 202 | OK |
| un_inspira | United Nations Careers | inspira | inspira | un | careers.un.org | false | false | 385 | 405 | OK |
| unv_uvp | United Nations Volunteers | unv | unv | unv | app.unv.org | false | false | 201 | 202 | OK |
| unops_avature | UNOPS | avature | avature | unops | careers.unops.org | false | false | 82 | 82 | OK |
| icao_oracle_hcm | International Civil Aviation Organization | oracle_hcm | oracle_hcm | icao | estm.fa.em2.oraclecloud.com | false | false | 105 | 105 | OK |
| paho_workday | Pan American Health Organization | workday | workday | paho | paho.wd5.myworkdayjobs.com | false | false | 17 | 17 | OK |
| unfpa_oracle_hcm | UNFPA | oracle_hcm | oracle_hcm | unfpa | estm.fa.em2.oraclecloud.com | false | false | 89 | 89 | OK |
| undp_oracle_hcm | UNDP | oracle_hcm | oracle_hcm | undp | estm.fa.em2.oraclecloud.com | false | false | 172 | 172 | Issue |
| unwomen_oracle_hcm | UN Women | oracle_hcm | oracle_hcm | unwomen | estm.fa.em2.oraclecloud.com | false | false | 71 | 72 | OK |
| wmo_oracle_hcm | World Meteorological Organization | oracle_hcm | oracle_hcm | wmo | estm.fa.em2.oraclecloud.com | false | false | 4 | 4 | OK |
| itu_successfactors | International Telecommunication Union | successfactors_rmk | successfactors_rmk | itu | jobs.itu.int | false | false | 37 | 37 | OK |
| unido_successfactors | UNIDO | successfactors_rmk | successfactors_rmk | unido | careers.unido.org | default | false | 20 | 20 | OK |
| unesco_successfactors | UNESCO | successfactors_rmk | successfactors_rmk | unesco | careers.unesco.org | default | false | 58 | 58 | OK |
| icrc_successfactors | International Committee of the Red Cross | successfactors_rmk | successfactors_rmk | icrc | careers.icrc.org | default | false | 25 | 25 | OK |
| cern_custom_html | CERN | custom_html | custom_html | cern | careers.cern | default | false | 61 | 61 | OK |
| osce_custom_html | OSCE | osce_custom_html | static_html | osce | vacancies.osce.org | true | false | 10 | 12 | OK |
| ctbto_successfactors_legacy | Comprehensive Nuclear-Test-Ban Treaty Organization | successfactors_legacy | successfactors_legacy | ctbto | career2.successfactors.eu | false | true | 0 | 0 | OK |
| icc_successfactors_legacy | International Criminal Court | successfactors_legacy | successfactors_legacy | icc | career5.successfactors.eu | false | true | 0 | 0 | OK |
| icddrb_custom_html | icddr,b | icddrb_custom_html | icddrb_custom_html | icddrb | career.icddrb.org | true | false | 5 | 5 | OK |
| ifad_peoplesoft | International Fund for Agricultural Development | peoplesoft | peoplesoft | ifad | job.ifad.org | false | false | 14 | 14 | OK |
| imo_api | International Maritime Organization | imo_api | imo_api | imo | recruit.imo.org | false | false | 10 | 10 | OK |
| ipu_static_html | Inter-Parliamentary Union | ipu_static_html | static_html | ipu | www.ipu.org | true | false | 0 | 0 | Issue |
| itcilo_custom_html | International Training Centre of the ILO | itcilo_custom_html | static_html | itcilo | jobs.itcilo.org | true | true | 0 | 0 | OK |
| itlos_static_html | International Tribunal for the Law of the Sea | itlos_static_html | static_html | itlos | www.itlos.org | false | false | 2 | 2 | OK |
| opcw_talentsoft_candidatespace | Organisation for the Prohibition of Chemical Weapons | opcw_talentsoft_candidatespace | static_html | opcw | jobs.opcw.org | true | false | 4 | 4 | OK |
| unssc_drupal_custom | United Nations System Staff College | unssc_drupal_custom | static_html | unssc | www.unssc.org | false | false | 1 | 1 | OK |
| unu_recruitee | United Nations University | unu_recruitee | static_html | unu | careers.unu.edu | true | false | 12 | 12 | OK |

## Source Expansion Fields

When adding an organization to `organizations.yaml`, keep these fields stable and explicit.

| Config field | Type | Required | Purpose |
|---|---|---:|---|
| `id` | string | yes | Stable source identifier. Used in `job_key` and output bundle identity. |
| `name` | string | yes | Human-readable organization name. |
| `ats_family` | string | yes | Adapter family, for example `workday`, `taleo`, `oracle_hcm`, `static_html`. |
| `adapter` | string or null | no | Explicit adapter override when `ats_family` is source-specific but implementation is shared. |
| `base_url` | URL string | yes | Primary source URL and host for robots/delay policy. |
| `enabled` | boolean | no | Whether `sync-bundles` includes the source by default. |
| `extra.output_slug` | string | recommended | Canonical output bundle slug, for example `wfp_jobs.sqlite3`. |
| `extra.fetch_details` | boolean | optional | Adapter-level detail fetching preference. Selective refresh can still enrich details. |
| `extra.allow_empty_source` | boolean | optional | Allows zero fetched jobs to mark existing jobs missing. Use only for verified empty boards. |
| `extra.date_locale` | `US`, `EU`, `ISO` | optional | Controls ambiguous numeric date parsing. |
| `extra.expected_site_name` | string | recommended for Oracle | Site-name guard used with `site_number` on shared Oracle hosts. |
| `extra.empty_policy` | string or mapping | optional | Per-run zero-fetch evidence policy, for example `verified_empty_required` or `verified_structural_empty`. |
| `extra.max_detail_pages_per_run` | integer | optional | Caps selective detail enrichment in routine runs so slow detail pages do not block listing fetches. |
| `extra.request_timeout_seconds` | integer | optional | Per-source HTTP timeout override. |
| `extra.max_retries` | integer | optional | Per-source HTTP retry override. |
| `extra.backoff_base_seconds` | number | optional | Per-source retry backoff base. |

## Database Conventions

SQLite type notes:

- Datetimes are stored as ISO-8601 `TEXT`.
- JSON payloads are stored as serialized `TEXT`.
- Booleans are stored as `INTEGER` using `0` or `1`.
- Confidence values are stored as `REAL` from `0.0` to `1.0`.
- `job_key` is the canonical vacancy key: usually `{source_id}:{external_id}`.

## Table: `jobs`

Canonical vacancy record emitted by adapters and persisted by sync.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `job_key` | TEXT | primary key | Stable vacancy identity. |
| `source_id` | TEXT | indexed with `status` | Source from `organizations.yaml`. |
| `org_id` | TEXT |  | Organization identity, currently usually same as `source_id`. |
| `ats_family` | TEXT |  | Adapter family that produced the row. |
| `external_id` | TEXT |  | Vendor vacancy ID when available. |
| `title` | TEXT |  | Normalized title. |
| `location` | TEXT |  | Raw or normalized listing location text. |
| `department` | TEXT |  | Department, office, job family, or unit when provided. |
| `employment_type` | TEXT |  | Raw contract/type/grade text when provided. |
| `posted_at` | TEXT | indexed | UTC-normalized posting date/time. |
| `closes_at` | TEXT | indexed | UTC-normalized closing date/time. |
| `closes_at_local` | TEXT |  | Original local deadline text when known. |
| `closes_tz` | TEXT |  | IANA timezone for local deadline when known. |
| `apply_url` | TEXT |  | Candidate-facing apply URL. |
| `source_url` | TEXT |  | Source/detail page URL. |
| `description` | TEXT | FTS indexed | Normalized description text. |
| `status` | TEXT | indexed with `source_id` | `open`, `missing`, or `closed`. |
| `normalized_hash` | TEXT |  | Hash of material job content for change detection. |
| `raw_json` | TEXT |  | Serialized adapter raw payload. |
| `first_seen_at` | TEXT |  | First time this vacancy was inserted. |
| `last_seen_at` | TEXT |  | Most recent successful observation. |
| `missing_run_count` | INTEGER |  | Consecutive successful source runs where the job was not seen. |
| `posting_fingerprint` | TEXT | indexed | Cross-source duplicate-detection fingerprint. |

## Table: `change_events`

Material changes detected during upsert and missing/closed transitions.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `id` | INTEGER | primary key autoincrement | Event row ID. |
| `source_id` | TEXT |  | Source that produced the event. |
| `job_key` | TEXT |  | Vacancy affected by the event. |
| `change_type` | TEXT |  | Usually `created`, `updated`, `missing`, or `closed`. |
| `old_hash` | TEXT |  | Previous normalized hash when applicable. |
| `new_hash` | TEXT |  | New normalized hash when applicable. |
| `observed_at` | TEXT |  | Event timestamp. |

## Table: `vacancy_snapshots`

Historical content snapshots used to inspect change history.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `id` | INTEGER | primary key autoincrement | Snapshot row ID. |
| `source_id` | TEXT |  | Source that produced the snapshot. |
| `job_key` | TEXT |  | Vacancy identity. |
| `content_hash` | TEXT |  | Hash of the stored snapshot payload. |
| `snapshot_json` | TEXT |  | Serialized content hash payload. |
| `observed_at` | TEXT |  | Snapshot timestamp. |

## Table: `source_runs`

One operational record per source sync attempt.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `id` | INTEGER | primary key autoincrement | Source-run row ID. |
| `source_id` | TEXT | indexed with `observed_at` | Source that ran. |
| `fetched` | INTEGER |  | Count returned by adapter/listing pipeline after caps. |
| `inserted` | INTEGER |  | Newly inserted jobs. |
| `updated` | INTEGER |  | Existing jobs with material changes. |
| `unchanged` | INTEGER |  | Existing jobs with unchanged material content. |
| `missing` | INTEGER |  | Jobs moved to `missing` during this run. |
| `closed` | INTEGER |  | Jobs moved to `closed` during this run. |
| `errors_json` | TEXT |  | Serialized list of source-level or per-detail errors. |
| `observed_at` | TEXT | indexed with `source_id` | Run timestamp. |

## Table: `source_run_diagnostics`

Companion health table for one source sync attempt. It is tied one-to-one to `source_runs.id` and explains whether missing/closed transitions were safe to apply.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `source_run_id` | INTEGER | primary key, FK to `source_runs.id` | Parent source run. |
| `source_id` | TEXT | indexed with `observed_at` | Source that ran. |
| `adapter_version` | TEXT |  | Adapter/version label used by the run. |
| `fetch_method` | TEXT |  | Fetch path such as `oracle_ce`, `successfactors_xml`, `static_html`. |
| `platform_host` | TEXT |  | Host used for platform scope and diagnostics. |
| `site_number` | TEXT |  | Oracle CE site number or equivalent platform scope. |
| `expected_site_name` | TEXT |  | Configured site name guard. |
| `observed_site_name` | TEXT |  | Site name observed from the platform, if available. |
| `endpoint_family` | TEXT |  | Platform endpoint family. |
| `http_status` | INTEGER |  | List endpoint status when captured. |
| `total_reported_by_source` | INTEGER |  | Source-reported total, for paginated APIs. |
| `pages_fetched` | INTEGER |  | Pages successfully fetched. |
| `pagination_complete` | INTEGER |  | `1` when all source-reported pages/items were collected. |
| `list_error_count` | INTEGER |  | List/scope/pagination error count. |
| `detail_attempted` | INTEGER |  | Detail enrichment attempts. |
| `detail_succeeded` | INTEGER |  | Detail enrichment successes. |
| `detail_failed` | INTEGER |  | Detail enrichment failures. |
| `detail_skipped` | INTEGER |  | Detail records intentionally left pending because a per-run detail budget was reached. |
| `empty_reason` | TEXT |  | `verified_total_zero`, `verified_structural_empty`, `unverified_zero`, `parser_no_match`, etc. |
| `zero_fetched_evidence` | TEXT |  | JSON evidence for accepted zero-fetch runs. |
| `observed_agency_counts` | TEXT |  | JSON map of Oracle hosted-agency facet counts, for example `UNDP`, `UNCDF`, `UN Volunteers`. |
| `observed_organization_counts` | TEXT |  | JSON map of Oracle organization facet counts observed for the scoped site. |
| `count_delta_pct` | REAL |  | Count drift percentage when computed. |
| `health_status` | TEXT |  | `ok`, `ok_empty`, `warning`, `degraded`, or `issue`. |
| `scope_validation_status` | TEXT |  | `passed`, `not_applicable`, `site_number_mismatch`, `site_name_mismatch`, etc. |
| `missing_transition_allowed` | INTEGER |  | `1` only when it is safe to increment missing/closed state. |
| `observed_at` | TEXT | indexed with `source_id` | Diagnostic timestamp. |

Missing/closed transitions should only run when:

- `health_status` is `ok` or `ok_empty`
- `pagination_complete` is not false
- `scope_validation_status` is `passed` or `not_applicable`
- zero fetched jobs have verified empty evidence

## Table: `vacancy_source_features`

Source-specific structured features extracted before classification.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `vacancy_id` | TEXT | primary key, FK to `jobs.job_key` | Vacancy identity. |
| `source_id` | TEXT |  | Source ID. |
| `ats_family` | TEXT |  | Adapter family. |
| `raw_title` | TEXT |  | Raw title signal. |
| `raw_description` | TEXT |  | Raw description signal. |
| `raw_location` | TEXT |  | Raw location signal. |
| `raw_department` | TEXT |  | Raw department signal. |
| `raw_employment_type` | TEXT |  | Raw employment type signal. |
| `source_grade` | TEXT |  | Source-provided grade value. |
| `source_grade_field` | TEXT |  | Field name that supplied grade. |
| `source_contract_type` | TEXT |  | Source-provided contract type. |
| `source_contract_field` | TEXT |  | Field name that supplied contract type. |
| `source_job_family_code` | TEXT |  | Source job-family code, if structured. |
| `source_job_family_label` | TEXT |  | Source job-family label. |
| `source_job_network_code` | TEXT |  | Source job-network code. |
| `source_job_network_label` | TEXT |  | Source job-network label. |
| `source_recruitment_type` | TEXT |  | Recruiting type, scope, or category. |
| `source_staff_category` | TEXT |  | Staff category signal. |
| `source_seniority` | TEXT |  | Seniority signal. |
| `source_country_code` | TEXT |  | Country code signal. |
| `source_city` | TEXT |  | City signal. |
| `source_region` | TEXT |  | Region signal. |
| `source_work_modality` | TEXT |  | Remote/home-based/on-site signal. |
| `source_unv_category_code` | TEXT |  | UNV category code. |
| `source_unv_category_label` | TEXT |  | UNV category label. |
| `source_unv_volunteer_type` | TEXT |  | UNV volunteer type. |
| `source_unv_work_location` | TEXT |  | UNV work location. |
| `source_unv_work_arrangement` | TEXT |  | UNV work arrangement. |
| `source_unv_assignment_duration` | TEXT |  | UNV assignment duration. |
| `source_unv_hours_week` | TEXT |  | UNV weekly hours. |
| `source_unv_host_entity` | TEXT |  | UNV host entity. |
| `source_unv_sdg` | TEXT |  | UNV SDG signal. |
| `source_unv_expertise_areas` | TEXT |  | UNV expertise areas. |
| `evidence` | TEXT |  | Serialized extraction evidence. |
| `extracted_at` | TEXT |  | Extraction timestamp. |
| `extractor_version` | TEXT |  | Feature extractor version. |

## Table: `vacancy_classifications`

Normalized classification output for filtering, search, audit, and review.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `vacancy_id` | TEXT | primary key, FK to `jobs.job_key` | Vacancy identity. |
| `ccog_primary_code` | TEXT | indexed | Primary CCOG code. |
| `ccog_primary_label` | TEXT |  | Primary CCOG label. |
| `ccog_family_code` | TEXT | indexed | CCOG family code. |
| `ccog_family_label` | TEXT |  | CCOG family label. |
| `ccog_part` | TEXT |  | CCOG part. |
| `ccog_confidence` | REAL |  | CCOG confidence. |
| `ccog_method` | TEXT |  | Rule or method used for CCOG classification. |
| `contract_category` | TEXT | indexed | Normalized contract category. |
| `contract_subtype` | TEXT |  | Contract subtype when available. |
| `contract_confidence` | REAL |  | Contract classification confidence. |
| `national_international` | TEXT | indexed | Scope classification. |
| `national_international_confidence` | REAL |  | Scope confidence. |
| `grade_system` | TEXT |  | Grade system, for example UN staff grades. |
| `grade_family` | TEXT | indexed with `grade_code` | Grade family such as `P`, `G`, `NO`. |
| `grade_code` | TEXT | indexed with `grade_family` | Normalized grade code such as `P4`, `G5`, `NOB`. |
| `grade_level` | TEXT |  | Numeric or source grade level. |
| `staff_category` | TEXT |  | Staff category classification. |
| `min_years_experience` | INTEGER |  | Derived minimum years when inferable. |
| `grade_confidence` | REAL |  | Grade classification confidence. |
| `country` | TEXT |  | Normalized country name. |
| `country_iso2` | TEXT |  | ISO-3166 alpha-2 country code. |
| `country_iso3` | TEXT | indexed | ISO-3166 alpha-3 country code. |
| `city` | TEXT | indexed | Normalized city. |
| `region` | TEXT | indexed | Region. |
| `subregion` | TEXT |  | Subregion. |
| `location_confidence` | REAL |  | Location confidence. |
| `work_modality` | TEXT | indexed | On-site, hybrid, remote, home-based, etc. |
| `work_modality_confidence` | REAL |  | Work modality confidence. |
| `unv_category` | TEXT | indexed | Normalized UNV category. |
| `unv_raw_category` | TEXT |  | Raw UNV category. |
| `unv_volunteer_type` | TEXT |  | Raw/normalized UNV volunteer type. |
| `unv_assignment_duration` | TEXT |  | UNV duration. |
| `unv_work_arrangement` | TEXT |  | UNV work arrangement. |
| `unv_hours_per_week` | TEXT |  | UNV hours per week. |
| `unv_host_entity` | TEXT |  | UNV host entity. |
| `unv_sdg` | TEXT |  | UNV SDG. |
| `unv_expertise_areas` | TEXT |  | UNV expertise areas. |
| `needs_review` | INTEGER | indexed | `1` if classification needs manual review. |
| `classification_version` | TEXT |  | Classification rule/version label. |
| `evidence` | TEXT |  | Serialized classification evidence. |
| `classified_at` | TEXT |  | Classification timestamp. |
| `source_hash` | TEXT |  | Source content hash used to skip unchanged reclassification. |

## Table: `vacancy_locations`

Multi-location expansion table used by search and explainability.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `id` | INTEGER | primary key autoincrement | Location row ID. |
| `vacancy_id` | TEXT | indexed, FK to `jobs.job_key` | Vacancy identity. |
| `city` | TEXT |  | Display city. |
| `city_key` | TEXT | indexed | Normalized city key for matching. |
| `country` | TEXT |  | Display country. |
| `country_iso2` | TEXT |  | ISO-3166 alpha-2 country code. |
| `country_iso3` | TEXT | indexed | ISO-3166 alpha-3 country code. |
| `region` | TEXT |  | Region. |
| `subregion` | TEXT |  | Subregion. |
| `location_type` | TEXT | indexed | Example: `duty_station`, `multiple_unknown`, `country_only`. |
| `is_primary` | INTEGER |  | `1` if this is the primary location. |
| `is_remote` | INTEGER |  | `1` if remote/home-based. |
| `confidence` | REAL |  | Location row confidence. |
| `source_field` | TEXT |  | Field or rule that produced the location. |
| `evidence` | TEXT |  | Human-readable or serialized evidence. |

## Table: `classification_overrides`

Manual correction layer for classification or search fields.

| Column | SQLite type | Key/index | Meaning |
|---|---|---|---|
| `vacancy_id` | TEXT | composite primary key, FK to `jobs.job_key` | Vacancy identity. |
| `field_name` | TEXT | composite primary key | Field being overridden. |
| `override_value` | TEXT |  | Replacement value. |
| `reason` | TEXT |  | Human review reason. |
| `created_at` | TEXT |  | Override timestamp. |
| `created_by` | TEXT |  | Reviewer or process that created the override. |

## Virtual Table: `jobs_fts`

SQLite FTS5 table for text search over `jobs`.

| Column | Type | Meaning |
|---|---|---|
| `title` | FTS text | Searchable job title. |
| `description` | FTS text | Searchable description. |
| `department` | FTS text | Searchable department/unit. |
| `location` | FTS text | Searchable location text. |

Triggers keep `jobs_fts` synchronized on insert, update, and delete from `jobs`.

## Index Summary

| Index | Table | Columns | Primary use |
|---|---|---|---|
| `idx_jobs_source_status` | `jobs` | `source_id`, `status` | Source-specific current/history queries. |
| `idx_jobs_closes_at` | `jobs` | `closes_at` | Closing-soon and deadline queries. |
| `idx_jobs_posted_at` | `jobs` | `posted_at` | New/recent job queries. |
| `idx_jobs_posting_fingerprint` | `jobs` | `posting_fingerprint` | Cross-source duplicate detection. |
| `idx_source_runs_source_observed` | `source_runs` | `source_id`, `observed_at` | Latest run status and trend reports. |
| `idx_source_run_diag_source_observed` | `source_run_diagnostics` | `source_id`, `observed_at` | Latest structured health diagnostics. |
| `idx_class_ccog_code` | `vacancy_classifications` | `ccog_primary_code` | CCOG search. |
| `idx_class_ccog_family` | `vacancy_classifications` | `ccog_family_code` | CCOG family search. |
| `idx_class_contract` | `vacancy_classifications` | `contract_category` | Contract filters. |
| `idx_class_grade` | `vacancy_classifications` | `grade_family`, `grade_code` | Grade filters. |
| `idx_class_country` | `vacancy_classifications` | `country_iso3` | Country filters. |
| `idx_class_city` | `vacancy_classifications` | `city` | City filters. |
| `idx_class_region` | `vacancy_classifications` | `region` | Region filters. |
| `idx_class_modality` | `vacancy_classifications` | `work_modality` | Remote/home-based filters. |
| `idx_class_scope` | `vacancy_classifications` | `national_international` | International/national/local filters. |
| `idx_class_unv_category` | `vacancy_classifications` | `unv_category` | UNV-specific filters. |
| `idx_class_review` | `vacancy_classifications` | `needs_review` | Manual review workflow. |
| `idx_vacloc_city_key` | `vacancy_locations` | `city_key` | City matching. |
| `idx_vacloc_country_iso3` | `vacancy_locations` | `country_iso3` | Country matching. |
| `idx_vacloc_city_country` | `vacancy_locations` | `city_key`, `country_iso3` | Precise city-country search. |
| `idx_vacloc_type` | `vacancy_locations` | `location_type` | Duty-station vs multiple/unknown filtering. |
| `idx_vacloc_vacancy` | `vacancy_locations` | `vacancy_id` | Join locations back to vacancies. |

## Relationship Map

```text
organizations.yaml source
  -> adapter emits JobRecord
  -> jobs.job_key
      -> change_events.job_key
      -> vacancy_snapshots.job_key
      -> vacancy_source_features.vacancy_id
      -> vacancy_classifications.vacancy_id
      -> vacancy_locations.vacancy_id
      -> classification_overrides.vacancy_id
  -> source_runs.source_id
      -> source_run_diagnostics.source_run_id
```

## Extension Guidance

Use these boundaries when expanding the library:

- New ATS adapter: emit `JobRecord` with stable `external_id`, `apply_url`, `source_url`, and rich `raw` payload.
- New source: add an enabled source in `organizations.yaml`, add a matching host policy in `robots_policy.yaml`, and keep `output_slug` stable.
- New search filter: prefer `vacancy_classifications` or `vacancy_locations`; avoid parsing `raw_json` at query time.
- New classification signal: add extraction into `vacancy_source_features`, then derive normalized output into `vacancy_classifications`.
- Manual correction workflow: write overrides into `classification_overrides` rather than mutating raw jobs.
- Operational reporting: read latest rows from `source_runs` plus `source_run_diagnostics`, not logs.
