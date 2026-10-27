# PR #143 review corrections — 26 October 2026

Scope: the two unresolved comments reviewed at PR head
`075cd7ac21e6e501462c0d1dd52632755fa1a848`. The fixes were prepared in an
independent checkout. They do not activate a release, reconcile live pending work,
rerun migration, release holds or change the deployed dispatcher/configuration.
The [JobAgg policy](change-and-review-policy.md) and its
[evidence map](change-and-review-evidence.md) remain authoritative.

## Reviewed listing locator

[Review comment](https://github.com/yutsukioka/un_job_application_helper/pull/143#discussion_r4369834610).
Backfill resolved a reviewed final-component symlink, but `record_event()` read
the original path again through the strict JSON reader. The regression reproduced
a missing listing head for the correctly reviewed alias while the direct-path
case passed.

The event recorder now resolves the same receipt path/hash through the existing
reviewed-location authority and carries the supplied deadline into the JSON read.
The stored attempt, original artifact path, digest and event identity remain
unchanged. Unreviewed aliases, changed bytes and retargeted aliases remain held;
independent worker-exit proof is still required. Existing failed-checkpoint rows
are not automatically deleted or replayed by this change.

`test_native_backfill.py` covers original/reviewed/unreviewed/retargeted paths,
changed content, original-path provenance and preservation of the later failed
refresh task. Existing artifact-location and historical-occurrence regressions
remain applicable.

## Interrupted historical index finalization

[Review comment](https://github.com/yutsukioka/un_job_application_helper/pull/143#discussion_r4369834614).
An interruption after index chmod and before receipt creation left a complete
read-only database. Resuming entered the write-oriented `prepare()`/`step()` path
again. Fault injection reproduced that replay and read-only write failures.

Resume now recognizes an already read-only index and finalizes through read-only
validation. The shared finalizer also validates newly sealed indexes: complete
state, exact references/mapping contract, complete generation inventory, original
dependency identities, no unresolved SQLite sidecars, destination bindings,
bounded SQL work and unchanged index bytes. It retains the original journal
verification counts, including unverified occurrences. It never converts a held
occurrence into verified history. A writable partial index keeps the existing
paged continuation path.

`test_historical_index_operator.py` injects the receipt-boundary interruption,
forbids prepare/step on resume, verifies unchanged index/production/fence bytes,
and checks idempotent receipt readback. Incomplete, wrong-contract, missing-plan
and sidecar cases fail closed. A real nonempty index fixture separately verifies
retained journal counts and changed plan/result/mapping rejection; the empty
operator fixture substitutes only its destination mapping.

## Complete implementation binding

All **180** package Python modules remain included in the full fingerprint:

`5717744700ea40c90339eeaa6487cc2fd96d12bc22f711e958ed75bd5b780f29`

The explicit reviewed retry scope is:

`8815ed5c61d8de508aeb9b4bfae1495a38d5e1784079b6935a45309acb16fc98`

The exact deployed predecessor
`ab4ec954db6f68b7964bb2d4672e561e5ed5a4d971d25dcafb46dcf0870932e9`
is added to the reviewed retry-equivalence set. These evidence-reader corrections
change neither provider requests nor retry budgets; predecessor retry counts and
exhaustion must survive a future authorized activation. Startup integrity and new
receipts still require the complete new fingerprint. M0 baseline Markdown/JSON
are byte-identical to the starting commit. No production seal was changed.

## Validation

Results below apply to offline fixtures, not production acceptance or A8.
The test interpreter is `/private/tmp/pr143-venv/bin/python`; candidate imports
are pinned with `PYTHONPATH` where required. Existing Mac ownership checks need
the real boot identifier. Restricted execution could not read that identifier;
the final offline suites ran with that host check available.

An initial dispatcher run used an older editable-install path and failed three
selected-package integrity checks. Pinning imports to this checkout corrected the
test environment; the runtime integrity refusal was retained. A focused seal
check also caught stale candidate bytecode after an immediate same-size seal edit;
only that checkout's cached worker bytecode was removed before final validation.

Final validation:

- Focused evidence/locator/index/retry/implementation suite: **173 passed**
  in 34.34 seconds. This group overlaps the package suite and is not added to it.
- Final index helper after adding the SQL deadline guard: **9 passed** in
  3.08 seconds; also overlaps the preceding suites.
- Dispatcher: **175 passed, 17 subtests passed**, 33.36 seconds.
- Repository hygiene plus HTTP/SSRF security: **15 passed**, 0.09 seconds.
- Ruff E4/E7/E9/F on all changed Python files, full Ruff on the new test file,
  configured JobAgg security lint, and `git diff --check`: passed.
- Full package suite: **4,141 passed, 54 skipped**, 508.08 seconds. Skips
  cover unavailable optional browser support and local/ignored evidence inputs;
  skipped cases are not validated by this run.

Commands from the candidate repository root (using the interpreter above):

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q packages/jobagg/tests --tb=short
PYTHONPATH=packages/jobagg PYTHONDONTWRITEBYTECODE=1 python -m pytest -q reports/jobagg-remediation-plan-2026-09-10/proposed_runner/tests --tb=short
python -m pytest -q tests/test_repo_hygiene.py tests/security/test_B3_http_safe_ssrf.py --tb=short
```

Representative external-volume performance, live recovery of prior failed
checkpoints, production activation and natural A8 cycles were not exercised.
