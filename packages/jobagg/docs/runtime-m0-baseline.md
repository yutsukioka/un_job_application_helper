# Runtime M0: deployed overlay reconciliation and bounded fixtures

Observed **22 October 2026, 19:06 EAT**. Scope: the two M0 tasks requested from
[the production cron handover](https://chatgpt.com/space/page_d7c49c508e7c81918886358d1d67261d).
R1–R8 in [the change policy](change-and-review-policy.md) apply.

## Reconciled code

Starting branch `jobagg-storage-compression`, HEAD
`a76e6b55055377d3a2849f8a9e1eaca1a155527a`. Before this work the only tracked
working-tree modification was the existing publication performance documentation;
it was preserved. Existing untracked files were preserved as well.

Compared every package Python module against the selected deployed overlay:
`/Users/yutsukioka2/git/un_job_application_helper-releases/pr141-25f278b5-local-4642596ad8c1/packages/jobagg/jobagg`.
Exactly three files differed. Their deployed bytes now occupy the canonical source paths:

| File | Preserved deployed behavior |
| --- | --- |
| `jobagg/db.py` | Detect existing FTS schema before creation, rebuild once on initial migration, avoid recurring external-content counts |
| `jobagg/pipelines/worker_policy.py` | Deadline-checked full metadata census; reuse unchanged parsed events, reject aliases/missing history, return independent copies |
| `jobagg/remediation_worker.py` | Correlated task attempt maximum; all four local scheduling indexes including `idx_remediation_attempts_kind_started`; exact retry predecessors and successor scope seal |

All **152** package Python modules match the overlay. The existing complete
implementation algorithm yields
`4642596ad8c19900a6d2cf474c144d22907df89b90e26ee78aa7d0c709f70c39`.
The retained retry successor scope is
`7dc2c0261e714b644b0a546163fd1d3a10566076ad3916dfdaeb511e6e00027c`.
No manifest exclusions or new compatibility aliases were introduced.
[The machine-readable baseline](runtime-m0-baseline.json) records the complete
module manifest, before/overlay hashes, captured query plans and local timings.
The deployed release was read only; this is source reconciliation, not activation.

## Reusable bounded fixtures

`tests/test_runtime_m0_baseline.py` runs against temporary roots and fixture
subprocesses. Its 12 cases cover:

- Both worker-result→marker and marker→intent interruption windows with storage
  budget absent, false and true. Durable request, report and exit proof remain;
  no publisher dispatch or intent is created. The marker-to-intent replay checks
  original evidence bytes and whether a second worker is dispatched.
- Zero accepted/attempted tasks still reaching the real publication-request and
  snapshot preparation path, with and without older synthetic acceptance files.
  Neither case treats missing markers as authoritative empty-backlog proof.
- Eight chronological timeout outcomes while synthetic WAL/SHM sizes change,
  preserving the earlier six-cycle subset. All eight currently reset the identical
  failure counter to one. An unchanged-condition control reaches its three-failure hold.
- Worker-initialized indexes copied into the publisher fixture together, including
  the competing kind-started index. Fixed accepted detail/document work with 128
  then 2,048 unrelated orphan attempts; newer failed refresh retained. Exact accepted
  attempt `accepted-001`, publication plan/document output and tampered-artifact
  rejection are equivalent before and after projection.
- Policy-cache invalidation, returned-object isolation, unindexed charged events
  and removed-history rejection; first-migration FTS rebuild and repeat initialization.

The joint fixture enforces a **32,000,000-byte** temporary-file ceiling, bounded
history sizes and no additional fixture network calls during publication.
Projection faults are injected immediately at the snapshot boundary; crash tests
use exceptions after durable writes, not process termination or power-loss simulation.
The zero-task report and validation verdict are fixture doubles; these tests assess
dispatch routing, not the acceptance validator's schema contract.

The observed SQL VM work was **1,012 → 14,405 steps** as unrelated history grew.
The captured local SQLite plan chose the kind-started index before the task join.
These are baseline measurements, not portable optimizer-string assertions or M1
selectivity acceptance. SQL timing is collected without artifact reads; total lineage
validation also includes SQL, metadata, hashing and parsing. Projection table-copy,
readback and lineage timings are recorded separately. Metadata/hash/parse costs
are not yet individually isolated. These tiny local, instrumented fixtures do not
establish production throughput or cold/warm external-volume behavior.

The old migration test's exact EXPLAIN assertions failed once the real competing
indexes were restored. It now checks preserved state/index presence and successful
finished outputs; the joint fixture records work and exact output/rejection equivalence.

## Remaining acceptance boundary

This is M0 characterization. M1 still must fix budget-independent continuation,
both crash windows, authoritative empty no-op, selective bounded work, entry/phase
budgets and stable recurrence. Current failing service behavior is deliberately
visible as baseline assertions, not called corrected or hidden behind skips.
The fixture's synthetic older acceptance is not an audit of the 40 historical
accepted task outcomes. Those exact identities still require separate reconciliation.

No provider requests, live database queries, process/cron/configuration changes,
production migration, compression, retirement, recovery, deployment or monitoring
were performed. No claim is made about current live controller or active-state health.

## Validation

Final local validation on 22 October 2026:

- **139 passed**: M0 fixtures, publication performance, shared policy and parallel
  policy, full implementation/retry binding, budgeted dispatcher, pending publication,
  remediation worker and database tests. Includes all **12 M0 cases**; no skips or xfails.
- **161 passed** in a separate, non-overlapping selection: accepted-attempt lineage,
  projection, parallel worker, timeout/DNS recovery, vacancy lifecycle, storage ingress,
  worker progress and HTTP SSRF security regressions. No skips or xfails.
- Ruff checks passed on the three reconciled modules and both changed/new test files;
  `git diff --check` passed. Complete package bytes independently matched the deployed
  overlay and its recorded full implementation fingerprint.

The focused test command is reproducible with
`packages/jobagg/.venv/bin/python -m pytest` followed by the named files under
`packages/jobagg/tests/`, with `-q -o junit_family=legacy --junitxml=/tmp/jobagg-m0.xml`.
The joint fixture emits `m0_joint_schema` and `m0_projection_timings` properties in
that XML; the committed JSON is one dated observation, not a regenerated authority.

The full repository suite, remote CI/dependency audit, independent review and
production/external-volume benchmarks were not run. This work does not claim M1,
A8 or cutover acceptance. Files remain local for review; no commit or PR was created.
