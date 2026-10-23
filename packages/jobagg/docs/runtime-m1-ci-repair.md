# M1 CI and PR review corrections

Observation: **23 October 2026, 09:41 EAT (Africa/Nairobi)**. Candidate branch
`codex/jobagg-m1-runtime`, pre-repair head
`0542ec8f7765c63f96d9fd13d64ac54b19318451`.

## Evidence and scope (R6–R8)

The Python job in [CI run 39905511998](https://github.com/yutsukioka/un_job_application_helper/actions/runs/39905511998/job/120256434989)
failed. The returned diagnostic log is truncated, but its available tail shows
`sqlite3.OperationalError: disk I/O error` and explicit
`OSError: [Errno 122] Disk quota exceeded` in the root test session's temporary
files. Root tests reported 107 failed, 463 passed and 3 skipped. The subsequent
dependency audit also failed with `Disk quota exceeded`; the separate security
rerun reported 18 failed, 105 passed, 2 skipped and 448 deselected. Those security
tests overlap the root suite and are not added to it. Other PR checks succeeded.
The fourth test invocation was not reached after the root suite failed.

This supports repairing temporary-storage retention. The available diagnostics
do not measure each suite's retained bytes or distinguish a byte quota from an
inode quota. They do not establish that every possible code defect is excluded
or that dependency auditing succeeded. The user-reported earlier suite totals
are not substituted for unavailable portions of the CI log.

The automated review subsequently posted three actionable findings against that
head at 09:12 EAT. All three were checked under R1–R8 and corrected below. The
initial absence of review threads did not constitute completed independent review.

## Review corrections (R1–R8)

1. **P1 — Acknowledgement cleared unprocessed accepted work**
   ([review](https://github.com/yutsukioka/un_job_application_helper/pull/142#discussion_r4356677649)).
   A selection can exceed `max_jobs` or the five-frame listing cap. The old
   acknowledgement cleared every admitted unit although only a prefix reached
   publication. Publisher plans/results now identify processed unit/version pairs;
   the dispatcher passes that exact evidence to conditional acknowledgement.
   Unvisited units remain pending, and only serviced sources advance fairness
   history. Missing processing evidence in older generation results conservatively
   retains work for another bounded generation. Existing N-versus-N+1 protection
   and destination/export readback gates remain. Health units have no public
   row/frame mutation and may complete only behind the same verified boundary.

2. **P2 — A listing rejection held unrelated sources**
   ([review](https://github.com/yutsukioka/un_job_application_helper/pull/142#discussion_r4356677664)).
   Listing rejections carry a source ID and no job key; treating null as a rejected
   job matched every listing/health unit. Rejections now match detail units by
   their non-null job key and listing units by their source. The regression
   combines two sources, their health units and an independent detail rejection,
   preserving unrelated successful dispositions rather than broadening holds.

3. **P1 — Repeated listing summaries conflicted as accepted evidence**
   ([review](https://github.com/yutsukioka/un_job_application_helper/pull/142#discussion_r4356677674)).
   Unsupported enumeration can return the same count-only proof for successive
   valid frames. Accepted listing keys now use the immutable frame hash, and
   explicitly prepared `remediation_sources.listing_event_version` points to the
   exact immutable accepted event. Native acceptance updates that pointer in the
   same transaction as the source head and successful attempt; a mismatch aborts
   acceptance. Legacy backfill attaches only the current task receipt's exact
   retained frame and refuses ambiguous duplicate occurrences. Selection,
   selective projection and listing publication use the pointer, retaining all
   older accepted events rather than choosing the newest match from history.
   Regressions cover equal counts with both different and identical timestamps,
   through actual selected projection, publication and destination readback.

Explicit compatible preparation is required for older pre-release M1 source
schemas/events without this pointer. Adding a column alone does not reconcile
existing native acceptance: retain those events and original controls for
reviewed head reconciliation and dirty-trigger/contract preparation. Nothing in
runtime startup silently migrates or approves those schemas.

## Repair choice

- Root `pytest.ini` and JobAgg's `pyproject.toml` default to
  `tmp_path_retention_policy = failed` and `tmp_path_retention_count = 1`.
- The Python CI job sets both options in `PYTEST_ADDOPTS`, covering all four
  sequential suite invocations and the later security rerun. This matters because
  VaultSync selects its own package configuration instead of either changed
  default. Test commands, security gates, action pins and permissions are retained.
- No blanket `/tmp/pytest-of-*` deletion is introduced. Pytest removes successful
  `tmp_path` fixtures during teardown and successful session directories at
  session completion; the latest failed session can retain diagnostic fixtures.
  Cleanup is best effort, not a hard bound on one test's peak space, leaked open
  resources, arbitrary temporary files or the size of failure evidence. Explicit
  `--basetemp` changes session cleanup semantics; CI does not supply it.

The behavior was checked against the installed pytest 9.1.1 implementation and
bounded subprocess fixtures. Both packages already require pytest >= 8.
Fetching classification, quota, retry ceilings, transport checks, original
recovery controls and storage-retirement policy remain unchanged. Publication
acknowledgement and listing evidence receive the scoped corrections above.

## Validation

A disposable Python 3.11.12 environment under `/private/tmp` installs the candidate
packages and workflow test dependencies. It does not modify the production
virtual environment. Linux/Python 3.12 CI remains the final environment check.
An initial local collection attempt lacked the workflow's AtlasVault package;
installing that package in the disposable environment corrected the setup.

- Seven bounded retention scenarios, 35 subprocess sessions: passed. Root and
  JobAgg defaults and each of the five CI invocations resolve to `failed` / `1`.
  Three consecutive passing sessions per scenario removed SQLite/payload and
  session-factory fixtures. Two deliberate failing sessions retained the latest
  failed payload and retired the older failure. These intentional failures are
  smoke-test inputs, not application regression failures.
- Retention-only change, before runtime review fixes: JobAgg storage/runtime/
  M0/contract/history-growth scope **220 passed**, 20.03 s.
- CI hardening and tracked-artifact hygiene: **4 passed**, 0.04 s.
- Review regressions before the seal-literal update: **7 passed**, 5.35 s,
  covering both publisher caps, two
  real bounded listing generations, scoped rejection, repeated occurrence
  identity, and missing/foreign processing evidence.
- Intermediate focused native worker/publication/work plus initial review
  regressions: **27 passed**, 70.41 s. The later full run covers the final guard
  and seal changes; this intermediate group is not final-tree validation.
- Retention-only change, before runtime review fixes: VaultSync **730 passed,
  2 skipped**, 45.44 s.
- Repository suite after runtime corrections: **570 passed, 3 skipped,
  12 subtests passed**, 49.79 s.
- Final API/dispatcher: **200 passed, 17 subtests passed**, 24.98 s.
- Final separate security rerun: **123 passed, 2 skipped, 448 deselected**, 3.11 s.
- Final full JobAgg validation: **3,825 passed, 9 skipped**, 538.88 s.
  The final runtime/test tree is committed as
  `efc7dce93388e1cf8f39a9521c2114bda7beff41`; subsequent documentation changes do
  not change its code or tests.
- Changed Python files pass Ruff; YAML/TOML/INI parsing, staged-diff checks, M0
  identity and complete implementation fingerprint checks pass.
- The nine package skips require the ignored full CCOG resource (one) or private
  audit captures (eight). CI on Linux/Python 3.12 and dependency auditing remain
  pending a new pushed-head run.

The 220-test scope overlaps the previously recorded full JobAgg suite; separate
scopes and the security rerun are not summed. The validation manifest retains the previous scoped review results separately
and identifies the final code commit and separate test denominators.

## Integrity and production boundary

M0 Markdown/JSON are byte-identical to `30ea183c`. The five changed worker
modules (including the seal literal) were reviewed as the exact corrections
above. All 167 package Python modules remain in the complete-tree fingerprint;
no exclusions or automatic approval were introduced. The explicitly recalculated
retry-successor scope is
`f7ddc51178e8d4ca8b825e8091117b122a668fe649ac86f883962d318c8f1824`;
the full implementation hash is
`3616569f42bcbb957f3bb802946fe5b3733fd19443b170981f75396faa0286c7`.
The dispatcher change only forwards processed-version evidence to the same
owner-held acknowledgement boundary. Existing reviewed retry predecessors and
charged history are retained.

JobAgg's `pyproject.toml` now hashes to
`9ccfb1f8bc9e1d1cc619dbacbfe80825d9fdae8faade4895f1044fd5989861c5`.
The retention-only configuration edits do not themselves alter the worker seal;
the review corrections above do. The broader
`runtime-publication-contract-1` includes the complete `pyproject.toml` hash.
A future activation therefore needs explicit matching contract preparation and
dirty-epoch revalidation; this repair does not automatically approve or reseal
that authority.

Only the isolated candidate is changed. No primary checkout/deployed worker,
production data, configuration, schedule, owner/fence, hold or observation window
is changed. Existing M2–M4 and independent review/activation gates remain.
