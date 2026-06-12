# ATS Detail Display Research

Generated: 2026-06-12

## Overview

This report reviews the job aggregation adapters, tests, docs, current SQLite
output, and the local API/app detail contract to guide the Apple app right-pane
job detail display. It does not change app code.

Primary sources reviewed:

- `packages/jobagg/jobagg/adapters/*.py`
- `packages/jobagg/jobagg/models.py`
- `packages/jobagg/jobagg/db.py`
- `packages/jobagg/jobagg/classification/extractors/`
- `packages/jobagg/config/organizations.yaml`
- `packages/jobagg/tests/test_additional_adapters.py`, `test_inspira.py`,
  `test_taleo.py`, `test_workday.py`, `test_pageup.py`, `test_sync_source.py`,
  `test_db.py`, and `test_config_consistency.py`
- `packages/jobagg/docs/current_sources_and_database_schema.md`
- `packages/jobagg/docs/fetching_list_and_detail_mechanism.md`
- `packages/jobagg/docs/ios_macos_search_app_spec.md`
- `packages/jobagg/docs/atlas_design_system.md`
- `services/job-api/job_api/app.py`
- `apps/apple/Sources/AtlasUI/JobDetailView.swift`
- Current DB: `private/jobagg/output/all_jobs.sqlite3`

Current consolidated DB sample:

- Total retained rows: 5,009
- Open rows: 2,452
- Closed rows: 2,285
- Missing rows: 272
- Latest observed row timestamp sampled from `jobs.last_seen_at`:
  `2026-06-12T06:17:03.846448+00:00`

The normalized `jobs` table already stores the cross-ATS header fields the app
needs: `title`, `source_id`, `org_id`, `ats_family`, `external_id`,
`location`, `department`, `employment_type`, `posted_at`, `closes_at`,
`closes_at_local`, `closes_tz`, `apply_url`, `source_url`, `description`,
`status`, `first_seen_at`, `last_seen_at`, `missing_run_count`, and `raw_json`.
The current API builds `display_sections` from raw text fields, then from
heuristic description heading splits or a fallback `Full Description`, and then
adds generic `Job Record`, `Classification`, `Locations`, `Source Features`,
and `Raw Source Data`. `_display_rows()` already skips empty row values, but
the section model is not ATS-aware and still exposes raw JSON by default in the
main detail payload.

## Reviewer Corrections

This review pass corrected misleading or incomplete claims after checking the
current code and DB snapshot:

- The API detail builder already has generic raw-field sections and heuristic
  description heading splitting; it is not limited to a fixed `Full
  Description` section.
- The Swift detail view filters only `Job Record`, `Locations`, `Source
  Features`, and `Raw Source Data` as metadata. It does not special-case `Full
  Description` into a separate `Description` heading; it renders `Full
  Description` like any other non-metadata section.
- The Swift `Apply` link is active whenever the search result has `applyURL`;
  it does not currently disable itself for `closed` or `missing` rows.
- `playwright_discovery` is registered by the sync adapter bootstrap but is a
  discovery helper, not a configured production sync family.

Recommended app principle:

1. Use normalized fields for the pinned header and key facts.
2. Use ATS/source-specific raw fields only when they add user-facing meaning.
3. Put parser evidence, raw HTML, raw JSON, diagnostics, and low-confidence
   extraction evidence in an inspector/debug panel, not the main right pane.
4. Render a section only when it has at least one non-empty row or non-empty
   body after trimming.
5. Never show empty headings such as "Responsibilities" or "Benefits" unless
   that section has content.

## Common Right-Pane Display Contract

Recommended default order for every ATS:

1. Header: organization/source, title, status, deadline, source monogram.
2. Actions: Apply if `apply_url` exists and row is open; Source if
   `source_url` exists; Save.
3. Why matched panel: current strategy/classification evidence.
4. Key facts: location, grade/level, contract/employment type, department/unit,
   posted date, closing date, source ID, external ID. Hide missing fields.
5. ATS-specific summary section: only the high-value fields listed below for
   the row family.
6. Description sections: use extracted or heuristic headings when available;
   otherwise show one `Full Description` body.
7. Classification and locations: only non-empty rows.
8. Source and history: first seen, last seen, status, missing run count,
   source/apply links.
9. Inspector-only: raw source data, raw HTML, detail URL, parser name,
   source-run diagnostics, snapshots.

Do not show:

- Blank section shells.
- `Raw Source Data` by default.
- Empty capability/checklist sections.
- Raw HTML in the main detail body.
- Apply button as active for `closed` or `missing` rows.

## Fallback Behavior For Closed Jobs And History Rows

The DB retains `open`, `closed`, and `missing` rows. Current exports contain
open jobs only; history exports and API detail may include inactive rows.

Recommended display:

- Always show a status pill when `status != open`.
- For `closed`: disable Apply or show it as a secondary "Source may be closed"
  link if the app keeps the link visible. Prefer "Open source" over "Apply".
- For `missing`: show "Not seen in latest successful source run" and include
  `missing_run_count` when non-zero.
- Show `first_seen_at` and `last_seen_at` in Source and history.
- Show stored `description` if present; do not fetch live details from the app.
- If `description` is empty, omit the Description section and show a compact
  Source and history section instead.
- If `raw.oracle_detail_status = not_available` or
  `raw.oracle_detail_empty_by_id = true`, display "Detail no longer available
  from Oracle CE" in Source and history, not as an empty description.
- Historical apply/source URLs may be stale; label them as source links, not
  guaranteed application entry points.
- Preserve existing enriched fields when listing-only refreshes omit details.
  This matches `JobDatabase._merge_existing_detail_fields()`.

## Current DB Examples By Family

These examples came from `private/jobagg/output/all_jobs.sqlite3`, selecting an
open row per family where available and preferring rows with a non-empty
description.

| ATS family | Example source/job key | Example title | Notes |
|---|---|---|---|
| `avature` | `unops_avature:3419` | Senior Mediation Adviser | UNOPS detail row; large detail HTML/description retained. |
| `csod` | `worldbank_csod:37017` | Senior Agriculture Specialist | World Bank CSOD public API row. |
| `custom_html` | `cern_custom_html:sy-epc-2026-111-ld` | Electronics/Electrical Technician Power Converter Systems | CERN static detail with grade. |
| `eu_careers_static` | `eu_careers_static:europol-2026-ca-fgiv-181` | Senior Agent | EU Careers static/detail parser. |
| `icddrb_custom_html` | `icddrb_custom_html:32232` | Senior Medical Technologist | icddr,b custom detail parser. |
| `imo_api` | `imo_api:1017` | Consultant | IMO JSON API with many section fields. |
| `inspira` | `un_inspira:277079` | Director, Air Transport Bureau (ATB), D2 | UN Careers/Inspira API row. |
| `itlos_static_html` | `itlos_static_html:VA_2026_001_Associate_Press_Officer_P-2_En.pdf` | Untitled role | Static PDF/detail fallback. |
| `opcw_talentsoft_candidatespace` | `opcw_talentsoft_candidatespace:564` | Organisation for the Prohibition of Chemical Weapons | Static detail from OPCW Talentsoft-like page. |
| `oracle_hcm` | `unwomen_oracle_hcm:34714` | ONU MUJERES: Consultoria... | Oracle CE row with flex fields and site metadata. |
| `osce_custom_html` | `osce_custom_html:...ssa-4765` | Local consultant for LWC Toolkit | OSCE static detail with grade/contract. |
| `pageup` | `unicef_pageup:593547` | Administrative & Finance Assistant (G-5), TA | UNICEF PageUp detail HTML. |
| `peoplesoft` | `ifad_peoplesoft:35547` | Search Jobs | IFAD PeopleSoft row; title quality risk. |
| `smartrecruiters` | `oecd_smartrecruiters:REF3042Q` | Directeur/trice general(e) - Agence... | OECD SmartRecruiters API row. |
| `successfactors_legacy` | `ctbto_successfactors_legacy:2403` | Head, Radionuclide Unit (P-4) | Legacy XML/SuccessFactors row. |
| `successfactors_rmk` | `unido_successfactors:1353769655` | Environmental and Social Impact Assessment Expert | RMK detail/listing row. |
| `taleo` | `fao_taleo:2601223` | FAO Representative in Turkmenistan | Taleo detail payload with `_taleo_flat`. |
| `unssc_drupal_custom` | `unssc_drupal_custom:IC_006_2026` | Untitled role | UNSSC Drupal PDF/static row. |
| `unu_recruitee` | `unu_recruitee:science-policy-visiting-fellow-cstd` | Science-Policy Visiting Fellow - Data Governance CSTD | JSON-LD row from static parser. |
| `unv` | `unv_uvp:1784888021269222` | Senior Planning, Monitoring & Evaluation and Reporting Officer | UNV assignment API row. |
| `workday` | `paho_workday:Req-05569` | PAHO Consultant - Roster - Band B | Workday CXS detail row. |

Families present in code/config but without current consolidated examples:

- `workable`: disabled `ideglobal_workable`; covered by parser tests.
- `icims`: registered adapter only; no configured source found.
- `usajobs`: registered adapter only; no configured source found.
- `playwright_discovery`: registered discovery helper only; no configured
  production source found.
- `greenhouse` and `lever`: no adapter or configured source found.
- `irena_oracle_hcm`: disabled Oracle HCM config.
- `councilofeurope_talents`: disabled static config needing a portal parser.

## Per-ATS Display Research

### Inspira / UN Careers

Sources:

- Adapter: `jobagg/adapters/inspira.py`
- Extractor: `InspiraExtractor`
- Current source: `un_inspira`
- Disabled split configs: `isa_inspira_split`, `itc_inspira_split`

Available structured fields:

- Normalized: title, duty station/location, department, employment type,
  posted date, closing date, apply/source URL, description.
- Raw keys include `jobId`, `postingTitle`, `jobTitle`, `jobCodeTitle`,
  `jobDescription`, `dutyStation`, `dept`, `jc`, `jl`, `jf`, `jn`,
  `categoryCode`, `jobLevel`, `jobFamilyCode`, `recruitmentType`,
  `startDate`, `endDate`, `inspiraURL` when detail is fetched, and
  `_inspira_detail_url`.
- Extractor maps `jc` to staff category, `jl`/`jobLevel` to grade,
  `jf` to job family, `jn` to job network, and `recruitmentType` to
  contract/recruitment type.

Recommended section order:

1. Key facts: duty station, level/grade, job family, department, close date.
2. UN job classification: staff category (`jc`), grade (`jl`), job family
   (`jf`), job network (`jn`), recruitment type. Hide any absent values.
3. Full Description or current heuristic heading-split sections. If
   ATS-specific backend splitting is added, prefer:
   Org setting, Responsibilities, Competencies, Education, Work experience,
   Languages, Assessment, Special notice, Apply information.
4. Classification, Locations, Source and history.

Risks/gaps:

- Current config has `fetch_details: false`; `inspiraURL` may be absent from
  listing-only rows.
- Description is usually one long HTML-cleaned body; backend section extraction
  would improve scanning.
- Split agency configs are disabled and covered by `un_inspira` until reliable
  filters are confirmed.

### Oracle HCM Candidate Experience

Sources:

- Adapter: `jobagg/adapters/oracle_hcm.py`
- Extractors: `OracleHCMExtractor`, `IOMOracleHCMExtractor`,
  `ICAOOracleHCMExtractor`
- Current sources: `icao_oracle_hcm`, `undp_oracle_hcm`,
  `unfpa_oracle_hcm`, `unwomen_oracle_hcm`, `wmo_oracle_hcm`
- Configured but empty/disabled examples: enabled `iom_oracle_hcm` has an
  empty current DB in this snapshot; disabled `irena_oracle_hcm`.

Available structured fields:

- Normalized: title, primary/secondary locations, department/organization,
  employment type, posted/closing dates, apply URL, description, status.
- Raw keys include `Id`, `RequisitionId`, `Title`, `PrimaryLocation`,
  `PrimaryLocationCountry`, `secondaryLocations`, `otherWorkLocations`,
  `workLocation`, `Department`, `Organization`, `LegalEmployer`,
  `BusinessUnit`, `ContractType`, `WorkerType`, `JobType`, `JobGrade`,
  `JobLevel`, `WorkplaceType`, `ShortDescriptionStr`,
  `ExternalDescriptionStr`, `ExternalResponsibilitiesStr`,
  `ExternalQualificationsStr`, `requisitionFlexFields`, `skills`,
  `oracle_site_number`, `oracle_site_name`, `oracle_expected_site_name`,
  `source_priority`, `listing_status`, and optional `fraud_warning`.
- Important flex-field prompts include `Grade`, `Agency`, `Vacancy Type`,
  `Recruiting Type`, `Practice Area`, `Bureau`, `Contract Duration`, and
  `Vacancy Timeline`.
- Oracle detail fetch can return no row for a just-closed requisition; the
  adapter marks such records with `status = closed`,
  `raw.oracle_detail_status = not_available`, and
  `raw.oracle_detail_empty_by_id = true`.

Recommended section order:

1. Key facts: primary location, other locations, grade, vacancy type,
   recruiting type, agency, bureau/practice area, close date.
2. Oracle requisition metadata: requisition ID, site name/site number,
   workplace type, contract duration, vacancy timeline.
3. Description sections in this order when available:
   Short description, Description, Responsibilities, Qualifications.
4. Fraud warning if `raw.fraud_warning` exists.
5. Classification, Locations, Source and history.

Risks/gaps:

- Oracle CE uses shared hosts; site number/name validation is essential and
  should be visible only in inspector unless there is a mismatch.
- Detail budgets mean many rows may be listing-only; avoid empty detail
  sections.
- IOM current output is empty in this DB snapshot despite enabled config;
  right pane should handle history/empty source states.
- Fallback official listings have less structure and should display
  `listing_status`/`fallback_reason` only in Source and history or inspector.

### Taleo

Sources:

- Adapter: `jobagg/adapters/taleo.py`
- Extractor: `TaleoExtractor`
- Current sources: `wipo_taleo`, `who_taleo`, `iaea_taleo`, `fao_taleo`,
  `adb_taleo`
- Disabled config: `nato_taleo`

Available structured fields:

- Normalized: title, external ID, location, department/job field,
  employment type, posted/closing dates, source URL, description.
- Raw keys include `_taleo_flat`, `_taleo_detail_url`, and sometimes
  `detail_url`.
- `_taleo_flat` commonly contains `JOB_LEVEL`, `Grade Level`,
  `Position Level`, `POSITION_LEVEL_LABEL`, `TYPE_OF_REQUISITION`,
  `Type of Requisition`, `JOB_TYPE`, `Primary Location`, `LOCATION`,
  `JOB_FIELD`, `Department`, `ORGANIZATION`, `Division`, `UNIT`,
  `Job Posting`, `Closing Date`, `Closing Date (Period for Applying) -
  Internal`, `Contract Duration`, `Post Number`, and `CLASSIFICATION_CODE`.
- FAO and ADB detail parsers extract richer metadata from
  `requisitionDescriptionInterface.fillList`.

Recommended section order:

1. Key facts: location, job field/department, grade/position level, type of
   requisition, closing date.
2. Taleo metadata: organization/division/unit, contract duration, post number,
   classification code, posting date.
3. Full Description or current heuristic heading-split sections. ATS-specific
   splitting should use the headings already embedded in the cleaned
   description.
4. Classification, Locations, Source and history.

Risks/gaps:

- Some listing configs set `fetch_details: false`; detail-level grade,
  division, and contract duration may appear only after selective refresh.
- Taleo column layouts vary by source; display must read non-empty normalized
  fields first, then `_taleo_flat` with source-specific labels.

### PageUp / UNICEF

Sources:

- Adapter: `jobagg/adapters/pageup.py`
- Extractors: `PageUpExtractor`, `UNICEFPageUpExtractor`
- Current source: `unicef_pageup`

Available structured fields:

- Listing/raw keys include `listing_html` and `_pageup_detail_url`.
- Detail/raw keys include `detail_html` and `_pageup_detail_url`.
- Parsed fields include job number (`job-externalJobNo`), contract type,
  location, categories, deadline, apply URL, and `#job-details` description.

Recommended section order:

1. Key facts: job number, location, categories, contract type, deadline.
2. UNICEF details: advertised date if extracted, duty station/level when
   present in the detail body.
3. Full Description from `description`.
4. Classification, Locations, Source and history.

Risks/gaps:

- Raw detail HTML can be large; keep it inspector-only.
- Some PageUp detail responses are empty AJAX fragments; the adapter retries
  public HTML. The UI should not expose failed retry internals by default.
- UNICEF consultant level/duty station may appear in detail text but not as
  normalized structured rows unless parsed upstream.

### Workday CXS

Sources:

- Adapter: `jobagg/adapters/workday.py`
- Extractors: `WorkdayExtractor`, `IMFWorkdayExtractor`,
  `GlobalFundWorkdayExtractor`
- Current sources: `wfp_workday`, `imf_workday`, `tbi_workday`,
  `unhcr_workday`, `wef_workday`, `wto_workday`, `globalfund_workday`,
  `paho_workday`

Available structured fields:

- Listing raw keys include `title`, `externalPath`, `bulletFields`,
  `locationsText`, `jobFamily`, `department`, `timeType`, `workerSubType`,
  `postedOn`, `startDate`, `externalUrl`, `applyUrl`, and `jobPostingUrl`.
- Detail raw commonly wraps `jobPostingInfo`, plus `hiringOrganization`,
  `similarJobs`, and `userAuthenticated`.
- Detail fields include `jobReqId`, `jobPostingId`, `title`,
  `jobDescription`, `location`, `country.descriptor`, `startDate`,
  `endDate`, `timeType`, `jobFamily`, `department`, `externalUrl`,
  `posted`, and `canApply`.

Recommended section order:

1. Key facts: requisition ID, location/country, time type, job family,
   department, close date.
2. Workday posting: posted/start date, `canApply` or `posted` status only if
   meaningful; otherwise inspector.
3. Full Description.
4. Classification, Locations, Source and history.

Risks/gaps:

- Many Workday sources run listing-only. If `description` is missing, omit
  Description and rely on Source link.
- `postedOn` can be relative text such as "Posted Today"; normalized date may
  be absent.
- Workday detail URL normalization is handled in the adapter; the UI should
  use stored URLs rather than reconstructing them.

### Avature / UNOPS

Sources:

- Adapter: `jobagg/adapters/avature.py`
- Extractor: `UNOPSAvatureExtractor`
- Current source: `unops_avature`

Available structured fields:

- Listing raw keys: `listing_html`, `_detail_url`.
- Detail raw keys: `detail_html`, `_detail_url`.
- Parsed detail fields include `Duty Station`, `Location`, `Seniority Level`,
  `Level`, `Contract type`, `Contract Type`, `Posted`,
  `Posting Start Date`, and `Posting End Date`.

Recommended section order:

1. Key facts: duty station/location, level/seniority, contract type, posting
   end date.
2. UNOPS details: level/seniority as source-native grade proxy.
3. Full Description from cleaned main content.
4. Classification, Locations, Source and history.

Risks/gaps:

- UNOPS descriptions can be very long. Section splitting by visible headings
  would materially improve right-pane scanning.
- Detail HTML should remain inspector-only.

### SAP SuccessFactors RMK / Jobs2Web

Sources:

- Adapter: `jobagg/adapters/successfactors_rmk.py`
- Extractors include `ICRCSuccessFactorsExtractor` and
  `UNESCOSuccessFactorsExtractor`
- Current sources: `itu_successfactors`, `ilo_successfactors`,
  `unido_successfactors`, `unesco_successfactors`, `icrc_successfactors`,
  `idb_successfactors`, `ebrd_successfactors`, `interpol_jobs2web`
- Disabled config: `au_successfactors`

Available structured fields:

- API raw fields can include `id`, `urlTitle`, `unifiedStandardTitle`,
  `title`, `jobLocationShort`, `mfield1`, `legalEntity_obj`, `filter6`,
  `jobGrade`, `filter3`, `unifiedStandardStart`, `cus_postingdate`,
  `unifiedStandardEnd`, and `cus_enddate`.
- HTML/listing raw fields include `listing_html`, `detail_url`, `title`,
  and `parser`.
- RSS raw fields include `title`, `link`, `description`, and `pubDate`.
- Detail parser can extract itemprop/jobdescription or `jobDisplay` content,
  plus `datePosted`, `validThrough`, and location meta.

Recommended section order:

1. Key facts: location, department/legal entity, grade/job grade, closing
   date.
2. Source-native metadata: parser/source path only if user enables inspector.
3. Full Description. For UNESCO, prefer extracted labeled values such as
   Grade, Level, and Type of contract in Key facts when present.
4. Classification, Locations, Source and history.

Risks/gaps:

- The family covers several layouts: API, RMK tables, tiles, RSS, and static
  detail pages. Do not assume every row has the same raw keys.
- RSS empty placeholders are treated as verified empty sources, not job rows.

### SuccessFactors Legacy

Sources:

- Adapter class: `SuccessFactorsLegacyAdapter`
- Current sources: `ctbto_successfactors_legacy`,
  `icc_successfactors_legacy`, `afdb_successfactors_legacy`,
  `aiib_successfactors_legacy`

Available structured fields:

- XML feed rows flatten to keys such as `jobreqid`, `reqid`, `title`,
  `jobtitle`, `location`, `department`, `grade`, `jobgrade`, `closingdate`,
  `enddate`, `applicationdeadline`, `description`, and `parser`.
- Legacy HTML rows store `listing_html`, `detail_url`, `title`, and `parser`.
- AIIB rows store official feed keys such as `number`, `title`, `description`,
  `department`, `type`, `location`, `positioning-date`, `closing-date`,
  `path`, `detail_url`, and parser `aiib_current_jobs_js`; detail enrichment
  adds `detail_fields`, `successfactors_job_id`, and parser
  `aiib_official_detail`.

Recommended section order:

1. Key facts: location, department/division, grade or job type, closing date.
2. Legacy source metadata: company/feed parser in inspector.
3. Full Description. For AIIB detail rows, preserve headings such as
   Responsibilities and Requirements when already embedded in description.
4. Classification, Locations, Source and history.

Risks/gaps:

- Some legacy sources can legitimately have zero openings. Verified empty
  evidence belongs in source health, not job detail.
- XML field names are flattened and lowercased; display code should rely on
  normalized fields and known aliases, not exact casing only.

### CSOD / Cornerstone

Sources:

- Adapter: `jobagg/adapters/csod.py`
- Extractor: `CSODExtractor`
- Current source: `worldbank_csod`

Available structured fields:

- Current raw keys include `requisitionId`, `displayJobTitle`,
  `externalDescription`, `locations`, `postingEffectiveDate`, and
  `postingExpirationDate`.
- Adapter can also parse `title`, `displayTitle`, `location`,
  `primaryLocation`, `department`, `ouName`, `employmentType`,
  `postedDate`, `openDate`, `closeDate`, `companyApplyUrl`, `applyUrl`,
  `externalDescription`, and `jobDescription`.
- Config contains World Bank detail/custom-field URLs that could eventually
  expose richer fields such as organization, sector, grade, term duration,
  recruitment type, languages, and closing date, but current config has
  `fetch_details: false`.

Recommended section order:

1. Key facts: requisition ID, location, posting effective/expiration date.
2. World Bank metadata: organization/sector/grade/recruitment type only when
   detail/custom fields are actually stored.
3. Full Description from `externalDescription`/`description`.
4. Classification, Locations, Source and history.

Risks/gaps:

- Rich WBG fields are documented in config notes but are not consistently
  present in current raw rows.
- CSOD context/token diagnostics should stay inspector-only.

### UNV Unified Volunteering Platform

Sources:

- Adapter: `jobagg/adapters/unv.py`
- Extractor: `UNVExtractor`
- Current source: `unv_uvp`

Available structured fields:

- Raw keys include `id`, `doaRequestNo`, `name`, `country`, `hostEntity`,
  `volunteerType`, `workArrangement`, `categoryName`, `assignmentDuration`,
  `duration`, `hoursWeek`, `workLocation`, `dutyStations`, `unvRegion`,
  `expertiseAreas`, `sdgType`, `taskType`, `isOnsite`, `publishDate`,
  `sourcingEndDate`, `status`, `spiId`, and `spiName`.
- Description is composed from `organizationMission`, `context`,
  `taskDescription`, `requiredSkillExperience`, and `livingConditions`.

Recommended section order:

1. Assignment summary: country, host entity, volunteer type, category, work
   arrangement, work location, duration, hours per week.
2. Assignment content: mission/context, task description, required skills and
   experience, living conditions.
3. UNV tags: SDG, expertise areas, region.
4. Classification, Locations, Source and history.

Risks/gaps:

- Some nested label/code objects may be present instead of plain strings; use
  label extraction and hide missing nested values.
- Apply/source URLs may be API detail URLs in current config, not a polished
  candidate-facing page.

### Static HTML And Custom Static Families

Sources:

- Adapters: `static_html.py`, `custom_html.py`, `icddrb.py`
- Current families/sources include:
  `custom_html`/`cern_custom_html`, `osce_custom_html`, `ipu_static_html`,
  `itcilo_custom_html`, `itlos_static_html`,
  `opcw_talentsoft_candidatespace`, `unssc_drupal_custom`,
  `unu_recruitee`, `eu_careers_static`, and `icddrb_custom_html`.

Available structured fields:

- Generic `static_detail` raw keys: `grade`, `contract_type`, `parser`, and
  `href`.
- Detail-enriched rows often also contain `detail_url` and `listing_raw`.
- JSON-LD rows can include `@type`, `title`, `identifier`, `datePosted`,
  `validThrough`, `description`, `hiringOrganization`, `jobLocation`,
  `employmentType`, and `baseSalary`.
- UNSSC Drupal rows store `code`, `document_url`, `apply_url`, and parser
  `unssc_drupal`.
- EU Careers rows store `grade`, `domain`, `institution`, `location`,
  parser `eu_careers_open_vacancies`, and `href`.
- icddr,b detail rows store `posted_at`, `closes_at`, `location`,
  `contract_type`, and parser `icddrb_detail`.

Recommended section order:

1. Key facts: grade, contract type, location, institution/domain, posted date,
   deadline, document URL when the source is a PDF.
2. Source-specific details:
   - EU Careers: institution, domain, grade, location.
   - UNSSC/ITLOS/IPU PDF rows: document link as primary source detail.
   - CERN/OSCE/OPCW/UNU: grade/contract/source parser fields only when
     present.
   - icddr,b: posted date, deadline, location, contract type/duration.
3. Full Description if non-empty; otherwise omit Description.
4. Classification, Locations, Source and history.

Risks/gaps:

- Generic static parsers can produce `Untitled role` when a PDF or page lacks
  parseable title metadata. This should be treated as a data quality flag, not
  a UI title style.
- Some static sources are verified empty; empty-source evidence belongs in
  source health, not in job detail.
- Static detail parsing is heuristic. Avoid rendering parser/debug fields in
  the main pane.

### SmartRecruiters

Sources:

- Adapter: `jobagg/adapters/smartrecruiters.py`
- Current source: `oecd_smartrecruiters`
- Disabled config: `cern_smartrecruiters`

Available structured fields:

- Raw keys include `id`, `uuid`, `refNumber`, `jobId`, `name`, `company`,
  `location`, `department`, `function`, `industry`, `experienceLevel`,
  `typeOfEmployment`, `releasedDate`, `applyUrl`, `postingUrl`,
  `referralUrl`, `jobAd`, `defaultJobAd`, `customField`, `active`,
  and `visibility`.
- `jobAd.sections` is the best source for display sections when detail is
  fetched or returned by the API.

Recommended section order:

1. Key facts: reference number, location, department/function, employment
   type, released date.
2. Description sections from `jobAd.sections` in source order when non-empty.
3. Classification, Locations, Source and history.

Risks/gaps:

- Current OECD rows have strong raw structure but no guaranteed closing date.
- Section keys inside `jobAd.sections` are vendor-defined; labels should be
  normalized for display but not hard-coded to one company.

### Workable

Sources:

- Adapter: `jobagg/adapters/workable.py`
- Disabled config: `ideglobal_workable`
- No current consolidated DB rows in this snapshot.
- Test example: `Google Gemini Strategy & Governance Intern`,
  external ID `7DE71DFD5E`, Denver/Colorado/United States, workplace remote.

Available structured fields:

- Raw keys include `id`, `shortcode`, `title`, `remote`, `location`,
  `locations`, `department`, `employment_type`, `type`, `workplace`,
  `published`, `url`, `application_url`, `shortlink`, `description`,
  `requirements`, and `benefits`.

Recommended section order:

1. Key facts: location(s), department, employment type, workplace/remote,
   published date.
2. Description, Requirements, Benefits as separate sections when non-empty.
3. Classification, Locations, Source and history.

Risks/gaps:

- Source is disabled, so production UI may not see Workable until enabled.
- `requirements` and `benefits` may contain HTML or markdown-like text; clean
  before display.

### PeopleSoft

Sources:

- Adapter: `jobagg/adapters/peoplesoft.py`
- Current source: `ifad_peoplesoft`

Available structured fields:

- Listing parser extracts title (`SCH_JOB_TITLE`), job ID
  (`HRS_JOB_OPENING_ID`), location (`LOCATION`), department
  (`HRS_DEPT_DESCR`), opened date (`SCH_OPENED`), closing date
  (`HRS_JO_PST_CLS_DT`), and `close_date_text`.
- Detail parser can extract body from `HRS_JO_DSCR_DESCRLONG`.
- Current raw keys include `job_id`, `title`, `detail_url`,
  `close_date_text`, and parser name.

Recommended section order:

1. Key facts: job ID, location, department, opened date, closing date.
2. Full Description if detail is present.
3. Classification, Locations, Source and history.

Risks/gaps:

- Current DB sample has title `Search Jobs`, indicating a parser/detail title
  quality issue for at least one retained row.
- Detail fetch is disabled in config, so many rows may be listing-only.

### IMO API

Sources:

- Adapter: `jobagg/adapters/imo.py`
- Extractor: `IMOAPIExtractor`
- Current source: `imo_api`

Available structured fields:

- Raw keys include `jobVacancyId`, `title`, `jobTitle`, `vacancyReference`,
  `circularNumber`, `classification`, `contractType`, `contractHours`,
  `role`, `location`, `department`, `country`, `region`, `dateofissue`,
  `deadlineforapplications`, `jobCloseDateExternal`,
  `jobCloseDateInternal`, `jobDescription`, `purposeforthepost`,
  `maindutiesandresponsibilities`, `requiredcompetencies`,
  `professionalexperience`, `education`, `languageskills`, `otherskills`,
  `contractInformation`, `salaryinformation`, `backgroundQuestions`, and
  `competencyQuestions`.

Recommended section order:

1. Key facts: vacancy reference/circular number, classification, contract
   type/hours, role, location, department, issue date, deadline.
2. Purpose for the post.
3. Main duties and responsibilities.
4. Required competencies.
5. Professional experience.
6. Education.
7. Language skills.
8. Other skills.
9. Contract and salary information.
10. Background/competency questions only if the app later supports application
    prep; otherwise inspector.

Risks/gaps:

- IMO has richer native sections than most adapters; collapsing everything
  into `Full Description` wastes structure.
- Some SharePoint-style raw keys are verbose; display labels should be curated.

### iCIMS

Sources:

- Adapter: `jobagg/adapters/icims.py`
- No configured source or current DB rows found.

Available structured fields:

- Parser accepts `jobs`, `searchResults`, or `items`.
- Raw fields include `id`, `jobId`, `reqId`, `title`, `jobtitle`,
  `location`, `primaryLocation`, `department`, `employmentType`,
  `postedDate`, `url`, `jobUrl`, and `description`.

Recommended section order:

1. Key facts: req/job ID, location, department, employment type, posted date.
2. Full Description.
3. Classification, Locations, Source and history.

Risks/gaps:

- Untested in current fixtures and no live source configured.

### USAJobs

Sources:

- Adapter: `jobagg/adapters/usajobs.py`
- No configured source or current DB rows found.

Available structured fields:

- Raw is `MatchedObjectDescriptor`.
- Fields include `PositionID`, `PositionTitle`, `PositionLocation`,
  `OrganizationName`, `DepartmentName`, `PositionSchedule`,
  `PublicationStartDate`, `ApplicationCloseDate`, `PositionURI`, and
  `UserArea.Details.JobSummary`.

Recommended section order:

1. Key facts: position ID, organization/department, locations, schedule,
   publication date, close date.
2. Job summary.
3. Classification, Locations, Source and history.

Risks/gaps:

- Registered but not configured; not part of current Apple app data.

### Greenhouse And Lever

No Greenhouse or Lever adapter, configured source, test fixture, or current DB
row was found in the inspected repository. If either family is added later,
the recommended default should follow the generic pattern:

1. Header/key facts from normalized columns.
2. Source-native fields such as department/team, workplace, office/location,
   employment type, requisition ID, and closing date if available.
3. Source-provided content sections in source order.
4. Raw JSON only in inspector.

## Org-Specific Adapter/Source Notes

Newer or org-specific configured sources map to existing adapter families:

- `worldbank_csod`: CSOD, enabled, current rows present.
- `iom_oracle_hcm`: Oracle HCM, enabled in config, current output empty in
  this snapshot.
- `ilo_successfactors`: SuccessFactors RMK/RSS, enabled, current rows present.
- `idb_successfactors`: SuccessFactors RMK/RSS, enabled but current output
  empty in this snapshot.
- `ebrd_successfactors`: SuccessFactors RMK/static, enabled, current rows
  present.
- `aiib_successfactors_legacy`: SuccessFactors legacy with AIIB official feed,
  enabled, current rows present.
- `oecd_smartrecruiters`: SmartRecruiters, enabled, current rows present.
- `eu_careers_static`: static HTML, enabled, current rows present.
- `interpol_jobs2web`: SuccessFactors RMK/jobs2web, enabled, current rows
  present.
- `irena_oracle_hcm`: Oracle HCM, disabled pending verified endpoint.
- `councilofeurope_talents`: static/portal placeholder, disabled pending
  custom parser.

For display, these should inherit their adapter-family rules, with only
source-specific labels added when the raw field is present.

## Implementation Guidance For The Apple Detail Pane

The current backend already sends `display_sections`. The Apple view treats
`Job Record`, `Locations`, `Source Features`, and `Raw Source Data` as metadata
sections and renders all other section titles, including `Full Description`, in
the main detail flow. It falls back to a `Description` section only when no
non-metadata `display_sections` are available. To support ATS-aware display
without showing empty sections, the backend should eventually emit curated
sections such as:

```json
[
  {"title": "Key Facts", "rows": [{"label": "Grade", "value": "P-4"}]},
  {"title": "Responsibilities", "body": "..."},
  {"title": "Qualifications", "body": "..."},
  {"title": "Source and History", "rows": [...]}
]
```

Recommended backend section-builder behavior:

- Build sections from a family-specific registry keyed by `ats_family`.
- Each section builder returns rows and body candidates.
- Trim strings, clean HTML, collapse whitespace, and drop empty rows.
- Drop the whole section if both body and rows are empty.
- Keep normalized `description` as fallback `Full Description`.
- Move `Raw Source Data` behind a request flag such as `include_raw=true` or an
  inspector-only endpoint.
- Preserve current generic `Job Record` fields, but curate them into Key facts
  and Source and history instead of dumping every non-empty DB column.

Potential section extraction helpers:

- Harden or replace the existing `_structured_description_sections()` heuristic
  for cleaned descriptions with visible headings.
- `rows_from_raw(raw, aliases)` for ATS metadata tables.
- `sections_from_html(raw.detail_html, selectors)` only on the backend, never
  in Swift.
- `source_history_rows(job)` for status, first/last seen, missing count, and
  source/apply links.

## Risks And Gaps

- The API currently exposes generic `Raw Source Data` by default. That can make
  the right pane noisy and can leak parser/debug internals into the primary
  user workflow.
- The Swift `Apply` action is currently enabled for any result with `applyURL`,
  even when the row is `closed` or `missing`; the status banner warns the user,
  but it does not neutralize the link.
- Several sources store large `detail_html` blobs in `raw_json`; rendering
  those in the main detail pane would be slow and unreadable.
- Detail completeness is intentionally partial for Oracle, PageUp, and some
  Workday/Taleo sources because of detail budgets or `fetch_details: false`.
- Static/PDF-heavy sources can produce weak titles such as `Untitled role`.
- PeopleSoft has at least one current row with the title `Search Jobs`, which
  should be flagged for parser quality review.
- Some configured sources are enabled but currently have zero rows in the
  consolidated DB (`iom_oracle_hcm`, `idb_successfactors` in this snapshot).
- Greenhouse and Lever are absent; any future support needs new adapters and
  display research.
- CSOD World Bank has configured detail/custom-field URLs, but current raw rows
  do not yet expose the richer WBG metadata table in normalized display-ready
  form.
- UNV source URLs currently route to the API detail template in config; if a
  better public candidate-facing URL exists, it should be stored separately.
- Multi-layout families such as SuccessFactors and static HTML need parser
  markers in the inspector, but user-facing sections should be based on
  available fields, not parser names.
