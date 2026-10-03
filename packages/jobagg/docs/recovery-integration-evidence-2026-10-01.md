# Recovery integration evidence — 1 October 2026

This is a sanitized review record for a locally prepared change, not an activation receipt. The production checkout remains on dirty local master `f717b5e845257eb745a8aec71c5ca4db812ce531`. Its 136 Python file hashes and saved workspace binding matched the final deployment manifest `06b27008efa0ddf6595407b7269d9e385fdb1ee1d0dada0ba684f985793b3d33` during this investigation. The integration candidate retains newer committed master behavior; its fingerprint will differ and must never be substituted into the live binding without separately reviewed activation.

Raw captures, databases, journals and one-time activation/recovery scripts remain machine-local under the original audit folders. They are not included in this change. The proposal hashes below bind that historical evidence; portable tests reproduce the relevant contracts using isolated data. A reviewer without those audit folders can verify the code and fixtures but cannot independently inspect the original live recovery transactions.

## Production speed

PR #123's original publication indexes, bounded lineage/projection/snapshot preparation, exact EU redirect host and IDB changing-total deferral were selectively deployed on 30 September. The isolated integration was refreshed on 2 October against master `c7cb8364ba81ed7564ef7128d8d37c4ca0f57ab5`, after PRs #122 and #123 merged. This recovery change retains their committed code; it does not deploy them again. The merged IDB revision allows two 900-second semantic deferrals and blocks the task on the third consecutive mismatch. Success resets the sequence; cooldown/budget deferrals preserve it; unchanged reseeding cannot reopen the blocked task. See [source-refresh tests](../tests/test_source_refresh_repairs.py) and [publication performance](publication_performance.md).

## Stale source recovery

The original audited stale-source change was deployed at 08:07 EAT on 1 October; exact selected-row recovery followed at 08:08 EAT. It repaired stable UNOPS exact-multiple enumeration, typed TLS EOF handling, source-specific unavailable-vacancy outcomes, effective listing health, and selection/claim gates requiring a fresh complete listing after reviewed recovery. Initial OSCE UI collection was superseded by the server-document and detail changes below. One vacancy-specific Workday S22 or exact UNOPS missing-vacancy redirect is not a whole-source failure. Generic denials and certificate errors remain held.

The prepared integration preserves master's transport timing, nested budget/cooldown handling and legacy `source_health` / `read_worker_health` APIs. The added worker diagnostics use `worker_source_health`. All supplementary attachment flags remain false. [Vacancy tests](../tests/test_worker_vacancy_denials.py), [host tests](../tests/test_host_recovery.py), [health tests](../tests/test_source_health.py), and [terminal-page tests](../tests/test_unops_terminal_inventory.py) provide portable positive and negative cases.

## DNS recovery

A brief DNS incident caused typed EAI_NONAME errors to block tasks. The 08:29 EAT deployment included EAI_NONAME alongside EAI_AGAIN in existing bounded host recovery; exact five-task recovery followed at 08:42 EAT. No cached IP, fallback address, relaxed SSRF or certificate check was added. Existing initial cooldown 1,800 seconds, maximum 21,600 seconds, 900-second probe lease and three probes per rolling 86,400 seconds remain unchanged. [DNS tests](../tests/test_worker_dns_recovery.py) cover persistent NXDOMAIN, successful recovery and newly private DNS answers.

## OSCE recovery

The 08:58 EAT server-page deployment was followed by the 09:27:51 EAT listing-only transport/identity/publication repair. Exact recovery committed at 09:28:42 EAT. Listings use native public server-document pagination in one fresh anonymous session, with all advertised pages, unique identities and stable totals independently verified. Detail requests use the ordinary verified HTTP path. Stable keys are full URL slugs; numeric card IDs remain separate census evidence. Publication preserves source URLs so both canonical and historical numeric listing frames can be rechecked exactly.

The final historical worker accepted a complete 34-vacancy/four-page listing and two details. Publication at 09:49:55 EAT and subsequent exact readback verified those two texts. There were 49 fresh listing observations but only 22 independently complete inventories. The corrected OSCE listing frame was still 18th among 23 queued frames at the later historical queue observation. These observations do not establish current coverage, source-wide detail completeness or current publication freshness. Recovery preserved the recorded 20,699 jobs, 31,745 attempts, 663 documents and 299 blobs; this is historical transaction evidence, not a new exhaustive preservation audit.

[Identity tests](../tests/test_osce_identity_compatibility.py), [server census tests](../tests/test_osce_server_inventory.py), [server transport tests](../tests/test_osce_server_pages.py), [phase restrictions](../tests/test_osce_transport_scope.py) and [publication tests](../tests/test_osce_listing_publication.py) provide portable reproductions. The compressed public 19 September vacancy 4972 fixture has raw SHA-256 `1c08e293e200efc22538156e6ae55d7f08153f5ef28154cc75749a62dc90d928` and [provenance](../tests/fixtures/osce/detail_4972_20260919.html.provenance.json); no cookies or authentication headers are included.

## Captured transient response integration defect

Persisting bounded HTTP error bodies newly exposes a task/host mismatch in the combined code: a typed captured 429 or 503 creates host cooldown, while the task becomes blocked because the worker required `body_captured: false`. The two unmarked regressions failed before the fix after verifying exact body, status, Retry-After and host evidence. This is a P2 recovery defect attributable to the transferred capture behavior, not the previously rejected hypothetical lineage timeout finding.

The local fix permits captured-body retries only for typed supported HTTP statuses matching the capture category, source and job/phase identity, exact artifact path, byte count and body hash, plus the existing matching host evidence and absence of source review holds. It retains Retry-After, charged attempts, durable probe ownership and the existing rolling probe ceiling. No generic denial or semantic parse failure becomes retryable. [Retry tests](../tests/test_worker_timeout_recovery.py) cover all six supported statuses, changed evidence, restart/probe exhaustion and eventual success. The policy-only strict xfail assertions have been promoted to passing recovery regressions; this candidate does not claim production G1 was repaired.

## Validation and limits

The final isolated candidate was checked on 2 October against merged master
`c7cb8364ba81ed7564ef7128d8d37c4ca0f57ab5`: JobAgg 2,637 passed / nine skipped;
API and dispatcher 138 passed / 17 subtests; root security/client and vault
1,300 passed / five skipped / 12 subtests; Flutter API client nine passed;
Swift API client ten passed. Groups overlap and must not be summed. No expected
failures remain. JobAgg skips require ignored CCOG or private UNFPA/UNICEF
captures; root skips require optional native artifacts or local runtime files.
Bandit and the CI security Ruff selection passed. Independent scoped source
review identified no actionable P2-or-higher defect; this does not certify
all-source completeness or every cross-platform lifecycle.

SQLite online backups of the existing worker, combined output and OSCE output
were taken sequentially from explicit read transactions on read-only
connections, without pausing writers. Candidate initialization and integrity
checks on those isolated copies preserved exact hashed rows across the checked
jobs, events, attempts, receipts, observations, documents, blobs and published
listing tables. Each copy is internally consistent at its own snapshot time;
there is no claim of one transaction spanning all databases. A snapshot-pinned
OSCE listing and its immutable captured files reverified under the candidate,
and API reads of the copied outputs retained public evidence-path redaction.
Raw databases and capture files remain local and are excluded from this PR.

Final commands, results, proposal hashes and exact patch prerequisites are recorded in the accompanying local reconciliation report. Package, API and dispatcher tests use temporary fixture data; the separate database checks use the consistent isolated copies described above. No provider probes are used. Historical audit groups overlap and must not be summed. No live activation, hold reset, schedule change or rebind is performed. Inner independent-verifier deadlines and all-source main-text completeness remain separate gaps; publication success cannot certify either.

## Historical proposal bindings

| Audit folder | Proposal SHA-256 |
| --- | --- |
| `jobagg-stale-source-recovery-2026-10-01` | `42f42404e7ea31518e3fa0a0edddc2c33110a0381f76ef88914756a0b784f98e` |
| `jobagg-dns-recovery-2026-10-01` | `1f4c6d885f43508816c35c11df3b524737d3d896ac08dc282e600744d21fac4f` |
| `jobagg-osce-server-recovery-2026-10-01` | `41db23a8c333927298b6816275aba8fafaac6af69513a05d8de9f2a84bfd3b4a` |
| `jobagg-osce-detail-session-2026-10-01` | `2aac1c631c075b9a2fa5a90e0174cb430f73ba73663c639d9d543b3f1432c4b2` |
