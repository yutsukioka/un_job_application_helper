# Runtime M1 implementation and validation

The complete immediate M1 correction is prepared on `codex/jobagg-m1-runtime`.
This is an isolated implementation candidate, **not production activation or
service-restoration acceptance**. The immutable [M0 baseline](runtime-m0-baseline.md),
its [manifest](runtime-m0-baseline.json) and commit `30ea183c` are preserved.
Final runtime code is commit `11bdab2340286cc724b5817f9c8d5996c6a63394`;
subsequent handoff changes contain only tests and documentation.

The 22 October production cron algorithm handover is the runtime contract;
[the change policy](change-and-review-policy.md) and its
[evidence map](change-and-review-evidence.md) remain authoritative. This record
supersedes intermediate M1 checkpoints and their overlapping test counts.

## Implemented behavior

- Accepted work is fenced before projection, independently of storage-budget
  enablement. Both worker-result-to-marker and marker-to-intent interruptions
  continue the original accepted run before new fetching. Native atomic
  acceptance survives worker loss without inventing exit zero; separate
  certificates retain failed controls and exact original request identity.
- One indexed SQL quota authority retains all charges, failures, missing finishes,
  imported reservations and historical source scope. Transactional task/service/
  count/hold summaries and scheduling headers replace repeated history discovery.
  Durable claims recheck actual owner, revision, source/host gates and quotas.
  Reports do not call `choose()` or silently treat partial eligibility as zero.
- Immutable accepted detail, listing and document references bind exact attempts,
  observations, identities, artifacts and validation contracts. A failed refresh
  cannot erase prior acceptance. Legacy backfill requires exact successful-exit
  controls; done rows alone are rejected.
- Atomic dirty versions cover details, metadata, freshness, listing presence/
  absence, lifecycle/readiness/holds, source health and document/blob dependencies.
  Time and reviewed contract changes enqueue revalidation. Version N cannot
  acknowledge N+1. Unknown and oversized work remains explicitly held.
- A verified empty path skips projection/publisher execution only with zero new
  acceptance, unchanged source revision, an empty authoritative work set and
  verified source/consolidated destinations and all four exports per destination.
  Missing authority or uncertain export references require repair.
- Projection version 5 copies exact selected accepted occurrences and required
  dependencies, rather than all retained versions or a broad database snapshot.
  Earlier validators retain their contracts. Selected artifact hashing, identity,
  main-text, replacement, proof and complete-listing checks remain enforced.
- Finite source/cohort, candidate, unit, destination-union, byte and measured time
  admission includes listing fan-out, consolidated output, DB/WAL/SHM, comparator/
  document/blob reads, beforeimages and current/history JSON/CSV exports. Aged
  cohorts retain service order; individually oversized work stays visible.
- Entry deadlines precede configuration/fingerprinting; a supervisor covers
  imports and blocking work before cooperative checks. Elapsed and wall clocks
  propagate without resetting the bound. Projection/publication share one phase
  envelope with measured viability and cleanup/report reserve. Inner SQL, hashes
  and decompression check budgets. Local verification expiry retains charges,
  captures and pending tasks, without provider-failure or progress credit.
- Each bounded publication batch retains its own intent, original controls,
  exact terminal/export evidence and storage settlement. Further admission uses
  fresh finite tokens under unchanged ceilings. Proven no-generation/no-write
  deferrals reuse sealed selections in separate child batches. Escrow-settlement
  interruption resumes that exact settlement without refetching.
- Stage/class and end-to-end recurrence survives WAL changes and alternating
  failures. Only relevant actual progress earns stage credit; zero-task deadline
  exits do not reset worker episodes. Reviewed probes are single use; episode
  history is retained.
- The read-only monitor has an executable loop, bounded producer-index polling,
  overflow-as-incomplete behavior, restart deduplication and failure/recovery.
  It distinguishes verified readback, genuine no-op and stalled work, and never
  dispatches work.

## Authority, migration and rollback boundary

Native source/destination fences bind complete schema, actual owner FD, store
identity, epoch, revision and contract. External transaction heads reject stale
in-place restores and uncertain transactions. Commit checks reject owner loss;
raw control writers cannot rewrite phase, revision or acknowledgements.

The full runtime contract covers all **166 package Python modules**, resources,
dispatcher/helpers, relevant Python/runtime/dependency bytes and configuration/
registry identities. It is checked per invocation and by worker/publisher
children. Timestamps never approve changed bytes. Dirty-epoch changes require
explicit reviewed revalidation; startup never reseals or rebuilds authority.
Reviewed successor-scope seal:
`66a4a5330d9695297b955f35a1f53d2d4769e69b6af415d6118827efe26f47bb`.
The validation manifest records the full implementation fingerprint separately.

Preparation order: retain M0; review every control root and writer; page quota/
control imports and accepted-occurrence backfill; prepare summaries and dirty/
freshness schema; bind destinations/exports; reconcile exact legacy outcomes and
unresolved generations; establish the native source epoch. Every bounded step
needs the actual owner and applicable reviewed storage/maintenance budgets.
Missing or conflicting proof remains repair.

Policy authority version 2 / import version 3 uses checkpointed evidence
verification and owner-fenced cutover. A nonempty `attempt_index.json` directory
stops actual old reserve ordering before provider dispatch; original JSON remains
archived evidence. Original bootstrap proofs join bounded audits. Source-hold
edits require review. Orphan policy charges stay charged; unmatched task attempts
are flagged. Overdue audit holds until explicit bounded catch-up completes.

The legacy control catalogue binds reviewed complete root/writer inventories,
source hashes, schema and epoch. Indexed unresolved references replace normal
completed-history scans. Closure recomputes exact occurrence, destination-journal
and current-export reconciliation; later rows and supplied booleans cannot
substitute. Existing generations retain original journals/configuration.
Earlier pre-release native schema/staging versions require explicit compatible
migration. No native M1 schema was activated here. Never restore an old database
as a code rollback or refund uncertain charges.

## Regression evidence

Exact results, timestamps, code provenance and synthetic growth measurements are
in [the validation manifest](runtime-m1-validation.json). Package, dispatcher and
security results are separate scopes; focused overlapping runs are not summed.
Validated at **00:37 EAT on 23 October 2026** against the immutable runtime commit:

- Package: **3,798 passed, 9 skipped**, 504.09 seconds.
- Dispatcher: **166 passed, 17 subtests passed**, 21.09 seconds.
- HTTP/SSRF security: **14 passed**, 0.03 seconds.
- Tracked-artifact hygiene: **1 passed**. New-module/fixture lint, configured
  JobAgg security lint and `git diff --check` passed.

One skip requires the ignored full CCOG resource; eight require private original
or staged audit captures. Full repository/service security and dependency audit
are not claimed: this local environment lacks `pydantic` for the B2 suite and
has no `bandit`. Full CI and independent review remain release gates.

| Contract | Principal package fixtures |
| --- | --- |
| M0 joint schema, eight timeouts/WAL and interruption baseline | `test_runtime_m0_baseline.py` |
| Both pre-intent windows, budget absent/false/true, empty path and clocks | `test_runtime_m1.py` |
| Cold first useful worker, real quota/storage/semantic fences, killed-worker acceptance | `test_native_worker.py` |
| Quota equivalence, paged migration/cutover, old-writer fence and audit | `test_policy_authority.py` |
| All-root catalogue and exact legacy readback | `test_runtime_control_catalog.py`, `test_runtime_reconciliation.py` |
| Mutation versions, CAS, selected inputs, freshness/contracts, aged/oversized/fan-out work | `test_publication_work.py` |
| Multiple generations, no-write deferral and escrow crash continuation | `test_native_publication_drain.py` |
| Restore/owner/target/schema/export uncertainty | `test_runtime_source_head.py`, `test_publication_fence.py` |
| Runtime byte changes and full reseal | `test_runtime_contract.py`, `test_worker_implementation_binding.py` |
| Inner expiry with retained capture/charge | `test_verification_budget.py` |
| Qualified three-cycle overlap/downshift and readback | `test_runtime_a8.py` |
| Fixed work with 0/1,024/8,192 retained versions | `test_runtime_history_growth.py` |

The local growth fixture keeps one active job. First/repeat passes use separate
fresh SQLite connections; OS cache is **not flushed**. These measurements prove
selected-work behavior on synthetic local storage, not external-volume cold/warm
performance or production capacity. SQL, metadata/open/read, hashing, parsing and
proof costs are reported separately.

The A8 fixture runs actual offline worker/publisher work for three cycles,
selects `[1, 1, 2]` only through qualifying controller history, verifies distinct
source/host overlap and exact readback, then checks pressure downshift and
duplicate callbacks. It does not establish natural-cycle acceptance.

## Production separation and release gates

The live cron dispatcher is in the primary checkout; this candidate remains
isolated. Do not merge/copy candidate dispatcher files there during development.
No completed native M1 cutover, provider probe, control migration, schedule/
configuration change or hold reset was performed. The early live-path incident
is retained below.

Initial M1 edits mistakenly touched the live dispatcher. Internal receipts show
five held scheduled invocations at 19:30, 19:45, 20:00, 20:15 and 20:30 EAT on
22 October 2026, each reporting missing `runtime_authority` in the selected
release and explicitly recording no worker/publisher dispatch. Original files
were restored byte-for-byte in primary commit `c636e280` at 20:31:52 EAT.
No process, schedule, database, journal or hold was reset. The health record
retains interruption evidence.

Restored live-path SHA-256 values:

- `runner.py`: `c27dcd9856c53905d21722ce94db376e55b0af540f840ccf7ed6a521336ed306`
- `runner_observability.py`: `bc0e2b718e24ea1bdfd217b080c3b07685a1b33ee31593fa2181e04a4c8a2f58`

External-volume cold/warm scale, under-two-second ordinary pre-dispatch and
840-second whole-cycle objectives, throughput, backlog drain/age and production
fairness remain unproved. Independent R7 review, full CI/dependency-security
review, exact activation/rollback approval and M4 natural A8/24-hour observation
remain release gates. No production observation window was started. No all-source
completeness, capacity compliance or restored-service claim is made. M2
generalization, compression/retirement enablement and dispatcher relocation remain
separate deliverables.
