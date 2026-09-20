# Python job fetching and live publication

The production cron entry runs every 15 minutes. Its dispatcher runs `jobagg.remediation_worker`, then `jobagg.publish_worker` against the live databases and exports. Routine fetching, extraction, queueing and publication make **zero LLM calls**.

The configured universe has 49 enabled sources and 10 disabled sources. Every observed listing receives a separate detail task. Since 17 September 2026, supplementary attachments are excluded by user request: all sources set `extra.fetch_attachments: false`. The worker skips attachment discovery, enqueueing and existing document tasks; publication and coverage omit attachment reconciliation. Previously stored files and associations remain intact. A PDF that is the vacancy posting itself remains a source for its main text. A successful publication is not a claim that every source is complete.

## Production command

The existing crontab uses this command. No second cron entry is needed:

```sh
/Users/yutsukioka2/git/un_job_application_helper/packages/jobagg/.venv/bin/python /Users/yutsukioka2/git/un_job_application_helper/reports/jobagg-remediation-plan-2026-09-10/proposed_runner/runner.py --config /Users/yutsukioka2/git/un_job_application_helper/reports/jobagg-deterministic-fetch-2026-09-14/dispatcher.proposed.json --execute >> /Users/yutsukioka2/git/un_job_application_helper/private/jobagg-runtime/dispatcher.log 2>&1
```

Its existing cron prefix is `*/15 * * * *`. The Mac must be awake and the configured external volume mounted. The wrapper verifies the volume sentinel and available space, honors the maintenance switch, and holds one shared owner lock. An overlapping invocation does no work.

The worker database is `/Volumes/MacbookAirM2/un_job_application_helper/jobagg/deterministic-worker-generation001/jobs.sqlite3`. Live consolidated data is `/Volumes/MacbookAirM2/un_job_application_helper/jobagg/output/all_jobs.sqlite3`. Logs and invocation health remain on the internal drive so an unavailable external drive can still be reported.

Every 15 minutes means a bounded batch, not a complete refresh of every detail in 15 minutes. Existing source policies normally refresh listings every three hours and unchanged details every 24 hours; a new or materially changed listing is due immediately. The backlog continues across ticks. UNICEF retains its reviewed **10 detail starts per rolling hour**, conservative spacing and batch pauses. The earlier manual 30/hour exception is not active. Actual network requests share host pacing and stop on access challenges; deterministic retries preserve Retry-After and quota history.

## Parallel organization fetching

As of 17 September 2026, the deployed controller was explicitly raised to **16 organization tasks concurrently**, with adaptive growth up to **32**. Existing batch history is retained; this is not a controller reset. One coordinator owns the existing lock, chooses and reserves work, and joins all running tasks before publication. Each source and scheduling host has at most one active task. The HTTP transport additionally locks every actual host, including redirects and shared document hosts. Database transactions and shared quota-index updates are serialized; network waits can overlap across independent organizations.

The worker admits at most **100 total tasks / 80 detail attempts** per cycle; publication accepts at most **40 changed jobs**. Existing 360-second worker and publisher budgets still bound each stage, and per-site limits can reduce actual throughput.

The `concurrency` policy in the existing dispatcher configuration increases the limit by one after **two consecutive qualifying batches**, up to **32**. A qualifying batch must use the available organization slots, make accepted progress, have eligible work across additional distinct hosts, and finish with verified publication. Actual HTTP requests need not arrive simultaneously: per-host waiting is expected. Global content-completeness warnings are distinct from execution health.

Fresh access blocking, repeated transport failures and runtime/integrity/database errors can reduce the limit by one, to a minimum of one. Existing access holds are preserved and counted separately. Missing metrics, failed publication, an interrupted run or idle/insufficient backlog cannot earn an increase. The controller journals its state at `<state_dir>/concurrency.json`; do not delete that file to reset concurrency. Missing previously established state is an explicit recovery error.

For an intentional fixed setting, edit only the `concurrency` object in the dispatcher JSON: set `mode` to `fixed` and `fixed_limit` to an integer within its configured bounds. A valid policy change preserves history and clears growth credit. Return to `adaptive` and remove `fixed_limit` to resume feedback. The crontab does not need another entry or a different interval.

Every acceptance includes task and actual HTTP interval receipts, source/host counts, accepted progress and pressure counters. The dispatcher outcome records the limit used, next limit, decision and reasons. These measurements establish actual parallel execution without model calls.

## Existing live inventory and publication size

`jobagg.baseline_inventory` reconciles existing live rows and attachment associations into the worker in bounded, preview-bound transactions. It retains original rows, document text and verified original bytes in baseline tables; missing rows, empty stubs and matching retained bodies gain explicit provenance. Accepted worker observations and existing queues/quota history remain unchanged. Historical jobs do not become current merely because they were imported, and imported text does not become a fresh source observation.

The production publisher uses `publication_snapshot_mode: projection`. A sealed snapshot contains the accepted-job records, observations, document/task/source evidence and referenced blobs that publication reads. It excludes the baseline archive and unobserved historical job bodies. Its manifest retains original counts, and full/projection comparisons must preserve rejected evidence as well as accepted changes. The older full-backup snapshot format remains supported.

The legacy diagnostic `listing_only` counts rows without an accepted worker observation; after baseline adoption it can include retained full descriptions. Use content/freshness-specific coverage measures rather than interpreting that diagnostic as a count of missing descriptions. Smaller snapshots prevent multiplying the baseline archive; they do not by themselves provide indefinite archive retention.

## Repairs made on 15 September 2026

- Preserve exact original listing records before database merging. A normalized or retained full-detail object cannot overwrite the URL needed by the next detail task.
- Bind each queued detail to its saved source/job listing frame. A reviewed, preview-first repair tool can restore proven local queue mutations while retaining original attempts and quota history.
- Compare SQL readback to an independently projected database merge. Missing optional metadata can retain a prior value without rejecting newly fetched text; explicit fresh fields and the entire fresh description remain strict.
- Requeue proven transient failures with their existing cooldowns, and rotate retries behind untouched sibling tasks. Identity, content, TLS, access and unknown failures remain explicit review cases.
- Retain HTML links alongside parsed text. Classify site navigation, social links and website policies separately from JD/TOR and job forms; retain a reason for each exclusion. Unknown document purpose stays unresolved.
- Extract native DOCX paragraphs, tables and other supported parts with bounded ZIP/XML handling, alongside PDF extraction. Preserve downloaded bytes even when extraction is partial. Images, complex layout, revisions and embedded content remain explicit fidelity gaps.
- Require a fresh guarded IMO API response for each detail. Preserve date-only posting/deadline labels as calendar values with unknown UTC time and timezone; validate that old raw values cannot return during database/publication merges.
- Use verified macOS certificate trust through `truststore`, preserving TLS verification and redirect guards.
- Add captured-response enumeration checks for Oracle CE and SmartRecruiters, alongside Workday. These prove the configured endpoint/filter scope only.

## Small Python browser

`jobagg.browser_fetch` runs a dedicated Chromium instance through Playwright. It uses no normal browser profile and no LLM. Its network requests go through the same captured, rate-limited HTTP transport as the worker. It saves screenshots, rendered public text, composed HTML including open shadow roots, link targets and hashes. API requests continue to use ordinary HTTP unless a reviewed browser URL pattern matches.

To inspect an enabled source’s listing in a visible browser window:

```sh
/Users/yutsukioka2/git/un_job_application_helper/packages/jobagg/.venv/bin/python -m jobagg.browser_fetch --dispatcher-config /Users/yutsukioka2/git/un_job_application_helper/reports/jobagg-deterministic-fetch-2026-09-14/dispatcher.proposed.json --source unu_recruitee --ready-selector 'a[href*="/o/"]' --headed --execute
```

Omit `--execute` for a preview. Inspection captures evidence; it does not itself publish job rows. When a reviewed `extra.browser_render` contract is enabled for an adapter, the normal cron worker uses the rendered page, queues parsed listings and details (supplementary documents are disabled in production), and publishes through the existing live pipeline. A working JavaScript-to-parser-to-SQLite-to-PDF test covers that integration.

A source contract specifies anchored HTTPS `url_patterns`, a `ready_selector`, one complete `content_selector`, optional bounded expand controls, a timeout, and exact read-only POST URLs where needed. A ready selector is not a population certificate: the adapter must still prove pagination, identity and full job-content scope. Unreviewed asset hosts and write requests are omitted and recorded. Unreviewed data hosts, private-network destinations, changed redirects, access challenges, closed shadow roots and iframe content require separate handling. This browser does not automate human login or solve CAPTCHAs.

Install in another environment with `uv sync --project packages/jobagg --extra browser`; then use that environment’s `python -m playwright install chromium`. This Mac’s dedicated runtime is in `private/jobagg-runtime/browsers`; it is separate from user browser profiles.

## Coverage and live database readback

Run a read-only census of all currently enumerated IDs, fresh detail hashes and live posting text:

```sh
/Users/yutsukioka2/git/un_job_application_helper/packages/jobagg/.venv/bin/python -m jobagg.fetch_coverage --registry /Users/yutsukioka2/git/un_job_application_helper/packages/jobagg/config/organizations.yaml --worker-database /Volumes/MacbookAirM2/un_job_application_helper/jobagg/deterministic-worker-generation001/jobs.sqlite3 --live-database /Volumes/MacbookAirM2/un_job_application_helper/jobagg/output/all_jobs.sqlite3 --report /Users/yutsukioka2/git/un_job_application_helper/private/jobagg-runtime/coverage.json
```

The report lists disabled sources separately. A text hash match proves that fetched text survived persistence. It cannot establish that the website had no unfetched paragraphs, another board, an unseen pagination cursor, or an unlinked required document. Source-specific public text checks remain required. Attachment counters are zero because attachments are out of scope, not because every attachment was fetched; historical task counts remain reported separately. Statistics can estimate defects in reviewed samples; only a source census can reconcile the known population, and neither proves an unknown population complete.

The deployment, real-site browser checks, queue repairs, tests and before/after census are recorded in `reports/jobagg-python-browser-2026-09-15/`. Earlier pilot receipts remain below as history.

The [earlier isolated-pilot documentation](../../reports/jobagg-python-browser-2026-09-15/DETERMINISTIC_FETCH_before.md) is preserved for historical review; its old scheduling and workspace instructions do not apply to this installation.

Earlier 15 September deployment: 1,784 package tests and eight dedicated browser tests passed. Four completed publications were read back in the live databases/API, covering 44 updates across 41 jobs. See the [earlier implementation report](../../reports/jobagg-python-browser-2026-09-15/IMPLEMENTATION_REPORT.md) for those results and statistical verification limits.

The later parallel deployment reconciled 18,434 existing live rows and validated two real batches with four organizations overlapping. Their 57 changed jobs passed live database/API readback. The current controller remains at four with zero growth credit after one real ICRC timeout; increases to five through eight are automatic only after qualifying batches. Maintenance ended at 20:51:55 EAT, and the existing cron remains active. The full pre-correction suite passed 1,897 package tests, eight browser tests and 69 dispatcher tests; the final narrow telemetry correction passed focused and independent regression checks. See the [parallel implementation report](../../reports/jobagg-parallel-fetch-2026-09-15/IMPLEMENTATION_REPORT.md), [current source status](../../reports/jobagg-parallel-fetch-2026-09-15/SOURCE_STATUS.md) and [operational receipt](../../reports/jobagg-parallel-fetch-2026-09-15/final_status.json). Global content completeness remains unproven.


## 18 September2026 source recovery

FAO publication recognizes its bound date headers and explicit English URL qualification, preserving all non-date text checks. UNV and UNOPS have capture-based pagination contracts that reconcile POST bodies/GET offsets, advertised totals and every parsed ID. Historical transport holds were reviewed once; future requests retain normal pacing, quota and access checks. Both sources use their existing deterministic API/HTML adapters; no LLM service is involved. See `reports/jobagg-fao-unv-unops-repair-2026-09-18/REPORT.md` for the repair and live verification.
