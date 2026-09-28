# World Bank and OSCE recovery

The collectors enumerate the current board and independently reconcile captured
IDs against the provider's advertised total. Counts and OSCE session numbers are
never configured. The ordinary worker queues a separate public-detail task for
every listing ID; listing success alone does not establish detail completeness.

## Implemented repair

World Bank requires the anonymous context published by its public career page.
Both `requires_bearer_token` and `requires_runtime_context` are now true. The
adapter discovers that context before any search POST and sends its bearer token
on every page. Missing context stops locally before an unauthenticated API call.
HTTP 401/403 stops endpoint fallback and token-refresh attempts, including when
the adapter runs without the durable transport wrapper. Candidate credentials
are not required or requested.

OSCE's listing browser omits stylesheets before dispatch, using the existing
guarded renderer option. All data and required script requests still pass through
the normal host, robots, pacing and denial checks. It starts at the official
search URL, discovers the generated session, and clicks controls only inside the
observed pagination container. It waits for both changed IDs and the next page
index, then reconciles saved page indices/counts, filter scope, stable totals,
unique IDs and the complete ID union. Detail extraction continues to use the
existing public-detail parser; CSS-free listing text is not a job description.

## Explicit recovery of old holds

`python -m jobagg.recover_public_sources` accepts `--registry`, `--robots`,
`--workspace`, `--shared-lock`, `--output-dir` and `--source` (worldbank_csod or
osce_custom_html). Use the existing worker workspace and its shared lock/policy
directory so accounting is shared. It is a dry run unless `--execute` is supplied.

Execution accepts only the evidenced missing-Authorization 401 for World Bank's
configured public API, or the listing stylesheet 403 at OSCE's `/styles/core.css`,
and requires the matching repaired configuration. It reserves the normal attempt
and gives that capture owner a lease of at most 540 seconds. Other workers still
see the hold. Counters and pacing are retained. Any new failure changes the
evidence and invalidates the lease. A crash leaves the old hold stopped; expiry
prevents the abandoned owner from continuing.

Only a complete independently verified listing releases the specific old hold.
Partial listings, new denials and failed parsing keep it stopped. The command
saves the old hold, tested binding, listing and independent verification. It does
not alter workspace bindings, fetch attachments, fetch job details, or publish.
After reviewed activation of the tested code/config, the existing worker and
publication pipeline must complete all detail tasks and their acceptance checks.

## Live verification, 18 September 2026

- World Bank: context GET 200 followed by three authenticated API POSTs, all 200;
  **73 unique listing IDs** reconciled against the live total. The old API hold
  was released after independent verification. A subsequent ordinary worker run
  fetched all **73 public detail pages** into an isolated evidence database,
  preserving shared quotas and pacing. Exact listing/detail ID equality, all
  capture hashes and all stored description hashes passed final readback.
- OSCE: the bounded corrected probe received **403 on `/jobs/search/` itself**.
  No stylesheet was requested. The probe stopped and the new access-denial hold
  remains active. The CSS-specific recovery command cannot authorize that new
  main-document denial. Further access recovery needs a successful authorized
  route or provider-side resolution, not repeated automatic requests.
- The final tested code/config binding was activated for the existing worker;
  schedule, quotas, operational job data and the new OSCE hold were preserved.
  World Bank's fetched records are available in the local `worldbank-jobs.json`
  export. This run did not perform consolidated/source publication or API-service
  readback. Those publication checks, and OSCE's listing/details, remain pending.

Validation: **2,139 package tests passed**, including real Chromium fixture tests;
the final focused run passed **72 tests**. Ruff passed on the changed Python
modules and regression tests.

Evidence and test logs are in
`reports/jobagg-worldbank-osce-recovery-2026-09-18/` at the repository root.

## OSCE native network recovery (18 September 2026)

The subsequent native transport fixes an important limitation of the first
repair: `GuardedBrowser` renders with Chromium but fetches through Python,
including an initial search GET before Chromium starts. OSCE now selects
`browser_render.transport: chromium_cdp_native_v1`, implemented by
`osce_native_browser.py`, for both full-search listings and public job details.
Other organizations keep their existing transport.

The native transport uses a fresh anonymous Chromium context for each task.
CDP Fetch pauses every request and response, including redirects. Requests are
serialized through the existing DurableCapture host lock, attempt accounting,
robots policy, pacing, request ceiling and deadline. Each response body is
bounded, hashed and persisted before scripts consume it; HTTP failures create
the existing holds. Redirect destinations are validated before their own
separately charged dispatch. Cookies stay in that task's browser context and
are not exported. TLS verification remains enabled. Stylesheets, images, fonts
and media are omitted. CSP blocks child frames, workers, popups and forms;
WebSocket URLs are blocked. No user profile, authentication, CAPTCHA solving,
stealth flags or automatic HTTP fallback is used.

The existing recovery CLI now accepts a **separate** mode:

```text
python -m jobagg.recover_public_sources \
  --registry PATH --robots PATH --workspace EXISTING_WORKSPACE \
  --shared-lock EXISTING_SHARED_LOCK --output-dir REPORT_DIRECTORY \
  --source osce_custom_html --recovery-mode native-browser \
  --access-evidence BROWSER_ACCESS_REVIEW.json
```

The review JSON must contain the official search `url`,
`observation: user_confirmed_browser_access`, and a timezone-qualified ISO
`observed_at` or Unix timestamp within 24 hours. This records the user's
observation, not a fabricated agent verification. Default execution is a dry
run; `--execute` reserves one attempt. The new mode accepts only the old Python
main-document 403 and the matching native configuration. It does not broaden
the CSS exception. It persists an attempt marker, grants one owner a lease of
at most 540 seconds in the CLI, leaves every other worker held, and releases
the hold only after independent complete listing reconciliation. New evidence,
expiry, interruption or incomplete pagination retains the hold. A native denial
cannot authorize another native probe. A repeat against the same old evidence
is also rejected after a crash or incomplete attempt.

The live native probe returned **307** for `/jobs/search/` and **200** for the
generated search session, followed by **403** for
`/js-dict?v=202308280716_0`. It stopped after those three requests and retained
the new hold. Search-document access is recovered, but a complete census,
detail acceptance and publication remain unverified. The script denial must
be resolved through an authorized access route/provider assistance; successful
first-page access must not be reported as a complete inventory. Existing jobs
must remain retained until a complete replacement passes normal publication
checks. Evidence: `reports/jobagg-osce-native-recovery-2026-09-18/`.

## OSCE HAR-backed result fragments (19 September 2026)

`browser_render.data_route: osce_job_results_v1` now reads the initial search
HTML and the public `/ajax/content/job_results` responses in one fresh native
Chromium session. Site JavaScript and UI subresources are omitted. The initial
redirect establishes anonymous cookies in memory; no HAR cookie values are
imported. The generated search session and `site.short_name` are discovered
from the current document.

Pages 2 through the advertised final page use the HAR-observed empty-body POST.
Admission binds the exact HTTPS origin, endpoint, parameter names, search
session, site name and next page index. It rejects extra/duplicate parameters,
nonempty bodies, other AJAX operations and more than 50 pages. The response is
parsed as JSON regardless of its text/plain MIME type: `Status: OK` and a string
`Result` containing unfiltered result HTML are required. The parser reconciles
page counters, stable totals, unique IDs and `/pageN` history URLs. Every page
is independently bound to a successful saved native response and body hash.

Cookie diagnostics retain names and inclusion/blocking metadata only. CDP can
omit an extra-info event on an intercepted redirect; that hop is marked
unavailable instead of reusing the preceding hop's observation. HTTPS fixture
tests verify both cookies at the receiving server, including the redirect hop.

The recovery CLI adds `--recovery-mode osce-data --access-evidence REVIEW.json`.
The review requires `observation: har_verified_full_listing`,
`complete_captured_listing: true`, a 64-character `har_sha256` and an
`observed_at` within 24 hours. It accepts only the prior native GET `/js-dict`
403 with this configured data route. A durable attempt marker prevents repeating
the same recovery. A new document or pagination denial is outside this recovery
exception. Existing host ownership, pacing, quotas and independent census
verification still apply. Default execution remains a no-network dry run.

The sanitized five-page HAR fixture contains 45 distinct public jobs. It is
regression evidence, not proof of current live access. Live acceptance evidence
and the operational outcome are recorded in
`reports/jobagg-osce-fragment-recovery-2026-09-19/REPORT.md`.

## OSCE current-page CSRF repair (19 September 2026)

The successful HAR pagination requests also carry `tss-token`. The provider's
captured `desktop.js` reads it from `input#tsstoken` for same-origin POSTs. The
initial data-route repair omitted this header after disabling provider scripts.
Both cookies alone were insufficient to reproduce the browser request.

`browser_render.csrf: osce_tss_token_v1` identifies the corrected contract. The
collector extracts exactly one nonempty, header-safe hidden token from its
fresh successful search document and sends it on every job-results POST in
that context. Admission rejects missing or mismatched headers before dispatch.
Diagnostics record only presence and current-document match booleans from
Chromium's network events; tokens are not exported as diagnostic/header values.
Independent census verification requires successful CSRF observations on every
pagination response. Original response bodies remain under the existing local
capture policy and can contain the provider's hidden form token.

`--recovery-mode osce-csrf --access-evidence REVIEW.json` permits one narrowly
reviewed probe for the old page-2 POST 403. The review binds the exact prior
capture metadata hash, provider CSRF-rule evidence and current-document token
presence. Corrected requests have `csrf_observation` diagnostics and cannot
qualify for another CSRF recovery, even if a new denial occurs. The durable
attempt marker prevents repeats after an incomplete run; the hold is released
only after independently verified complete listing capture.

See `reports/jobagg-osce-csrf-repair-2026-09-19/REPORT.md` for test and live
acceptance results. The earlier GET denials remain separate observations.
