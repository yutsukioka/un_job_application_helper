# Cron recovery repair

The 21 September audit found local code defects in retained 15-minute runs. This
change repairs those mechanisms without relaxing source identity or body checks.
It does not establish that all organizations are currently healthy.

## Repaired behavior

- Publication preserves current UNV proof pairs, exact labelled public fields
  (including empty strings and explicit unknowns), and Workday date precision.
  Historical values cannot fill a field owned by a valid current source contract.
  In-memory replay passed all 213 previously rejected saved binding cases.
- HTTP admission reserves at least five seconds of useful transport time (or the
  configured timeout if shorter), plus one second for completion, **after** host
  pacing. Local deadline deferrals do not charge a recovery probe. A timeout
  caused by clipping the configured allowance remains a local budget event,
  rather than starting a host cooldown. Genuine transport failures still use
  the existing 30-minute-to-six-hour backoff, Retry-After and bounded probes.
  The admission allowance is `min(configured_timeout, 5) + 1` seconds; the
  effective transport timeout is `min(configured_timeout, remaining - 1)`.
  Thus the admission allowance is six seconds for configured timeouts of five
  seconds or more; using the full configured timeout needs that timeout plus
  one second of remaining batch time. Recovery probes require the full
  configured timeout plus two seconds before admission. Checkpoint timing
  measures both standard HTTP and native browser dispatch: only a timeout
  that consumes the clipped allowance becomes a local budget event. Early
  timeouts still count toward recovery; observed status and diagnostics are
  preserved, and known HTTP errors retain their status-based handling.
- Typed budget errors remain retryable through exception wrappers. Successful
  completion clears the current task error; old attempt receipts stay immutable.
  Blocked/dead-letter tasks with a recorded retry fingerprint can re-enter only
  after their semantic listing input or reviewed code/config binding changes.
  New frame paths and observation timestamps alone are not changes. Legacy
  untyped blocks still require evidence-checked repair.
- Taleo requires a returned requisition ID matching the listing plus a bound
  title and description. All 50 saved navigation templates now fail acceptance.
  Missing-identity/body responses get three total attempts, with five- and
  ten-minute retry floors; scheduler/policy limits can make the actual delay
  longer. A different returned ID remains an integrity failure. An OPCW-specific
  unavailable panel becomes an inventory-reconciliation outcome, not a job body.
  Standard sync also records explicit unavailable outcomes from OPCW and Taleo,
  preserving the listing and prior detail evidence. Requests still consume the
  run allowance, but unavailable outcomes receive neither detail success nor
  breaker success/failure credit. Persisted diagnostics and operator reports
  expose `detail_unavailable` separately. Unchanged listings wait for inventory
  reconciliation; a changed listing, reappearance or explicit refresh permits a
  new detail observation. Older backlog schemas migrate atomically.
- Inspira changes consisting only of U+200E left-to-right marks in otherwise
  identical LTR text pass a narrow shortening exception. Original bytes and
  hashes remain intact. Other Unicode controls, RTL text and substantive
  shortening still require their existing source contract/review.
- Worker reports include current fetch state, last attempt, last success, retry
  due time and queue counts. `JOB_API_WORKER_DB` connects `/api/sources` to that
  worker database read-only. A missing configured worker reports unavailable,
  never an old OK value. Unconfigured legacy OK diagnostics expire after six
  hours. Sources without published jobs are included when the worker is wired.
  Fetch success does not certify coverage or publication completeness.
  Effective listing/API hosts are persisted using the scheduler's resolver, and
  document URLs are included when reporting host holds. The read-only reader
  remains compatible with older databases lacking host metadata. Worker-wired
  API responses preserve the three legacy diagnostic fields without inheriting
  stale legacy health or timestamps for missing workers or sources.

## World Bank's historical budget block

The queue-only repair command now recognizes the old World Bank listing wrapper
only when its durable claim/attempt matches, **every failure capture** is a typed
pre-dispatch deadline failure, other captures are successful, and current host,
source and database circuit policies allow recovery. It neither clears holds nor
changes old attempts, jobs or captures. A plain error-string match is insufficient.

Use the existing `python -m jobagg.remediation_repair` preview and hash-bound apply
workflow after creating a reviewed worker generation bound to the new code. Its
plan includes `captured_legacy_listing_budget_deferral` when that evidence exists.
Do not edit the old workspace marker merely to bypass the code-drift guard.

## Lossless export retention

The largest observed growth came from full materializations, not fetch captures.
`python -m jobagg.retained_artifacts` adds a bounded, preview-first archival path:

```sh
python -m jobagg.retained_artifacts \
  --state-dir /path/to/publication-state \
  --output-dir /path/to/live-output \
  --shared-lock /path/to/existing-shared-owner.lock \
  --retain-days 7 --max-files 100
```

Add `--execute` to apply the inspected scope. The command takes the **same owner
lock used by the dispatcher/publisher**, requires a completed publication gate,
excludes the current generation, and selects only old completed generations with
matching plan hashes and verified export journals. Legacy generations lacking
sufficient evidence are retained. The maximum file count bounds originals
selected for retirement, not the historical scan or its verification reads.

Every archived object encountered before the candidate limit ends the scan is
stream-verified against its receipt, once per unique content hash per invocation.
A scan can decompress all historical unique blobs while holding the shared owner
lock, so its duration grows with retained history. Schedule it accordingly;
successful preview/apply verifies the entries encountered, not the whole store
when the candidate limit stops the scan early. Truncated, malformed or changed
gzip content fails with `retained_artifact_hash_changed` before any new originals
are retired.

Eligible prepared exports and verified export backups become gzip objects named
by their original SHA-256 under `retained-blobs/`. Atomic tombstones preserve the
original path/hash/size. Compression is round-trip verified and fsynced before
retiring the full copy. Export validation and recovery read and verify the original
bytes through that receipt; interrupted archival can be safely resumed. Objects
are deduplicated and are never garbage-collected by this command.

Raw HTTP captures, attempt/plan manifests, row before-images, SQLite publication
snapshots, unknown backups and live outputs are retained. Snapshot retention is
not implemented here because those files also serve as immutable recovery and
publication evidence. This reduces export-copy growth; it does not establish a
hard bound on all historical storage. No production history was archived as part
of developing this PR.

## Early storage warnings

A configured dispatcher storage guard now persists `storage-health.json` beside
its independent internal attempt receipts. Optional guard settings are:

- `warning_free_bytes`: defaults to max(three times reserve, 64 GiB).
- `publication_headroom_bytes`: defaults to 8 GiB; warning becomes critical below
  reserve plus this headroom. Size it to the deployment's largest transaction.
- `warning_runway_seconds`: defaults to 48 hours.

Runway uses measured volume free-space changes over up to 24 hours and is clearly
labelled as including other writers. It is an estimate, not guaranteed jobagg-only
growth. State changes and recovery/clear events are persisted; repeated unchanged
warnings do not append duplicate transitions. The existing hard reserve still
stops work. These are local status alerts; no external messaging is configured.

## Deployment and validation

Use a checkout pinned to the reviewed commit, a frozen registry/robots manifest,
and a separately reviewed worker generation. Switch the dispatcher command paths
between ticks under its owner/maintenance procedure. Never modify Python/config
files in the checkout used by an active worker: that caused the observed morning
drift failures. Point the API at the active generation with `JOB_API_WORKER_DB`.
Run the queue-repair preview, inspect its evidence-bound candidates, then apply
only that plan. Schedule archival through the same owner after previewing it.

Regression tests use temporary databases and retained public fixtures. The saved
production-case replay used a sealed worker snapshot, read-only live rows and
in-memory publication previews; it made no organization requests or production
writes. Negative tests retain mismatched body/identity checks, access holds,
substantive shortening, changed archive hashes and unresolved generation guards.
