# Proposed Mac scheduler wrapper — executable reference, future worker required

This directory contains a runnable Python 3.10+ / macOS reference wrapper and synthetic tests. It does **not** implement the fetching fixes, supply a remediation worker, enable a schedule, change production configuration, or update any job database. The sample configuration deliberately has an empty `worker_argv`.

**Historical September 10 handover:** the database/export and live HTTP checks passed then, and the same native heartbeat was reactivated. The later September 13 inspection found that heartbeat configuration absent. A separate maintained worker is now being implemented and reviewed; cron/launchd remain uninstalled. Use the [current status](../../jobagg-complete-fetch-2026-09-13/STATUS.md) and [current worker architecture](../../jobagg-deterministic-fetch-2026-09-14/ARCHITECTURE.md) rather than treating the historical handover as current runtime state. See [the final overview](../README.md) and [applied heartbeat prompt](../HEARTBEAT_REPLACEMENT_PROMPT.md). Operational acceptance remains pending.

`runner.py` performs one bounded tick and exits. Its default is a read-only dry-run. An explicit `--execute` dispatches only the command supplied in configuration. There is no internal infinite loop and no shell command interpolation.

## Safe inspection and tests

From this directory:

```sh
python3 runner.py --config config.example.json
python3 -m unittest discover -s tests -v
```

The first command currently rejects the stale planning manifest after the reviewed live registry change. That is the intended fail-closed behavior, not a repaired/complete run. It reads configuration and manifest files; it does not create runtime files, acquire a lock, or launch a worker. Tests create temporary directories and synthetic local subprocesses. They do not read/write production databases, fetch websites, or install cron. The timeout fixture starts a child that ignores SIGTERM; the test checks that SIGKILL stops it and that the inherited lock is released.

## Configuration and future dispatch

All configuration paths resolve relative to the configuration file. The frozen example manifest contains the planning-time registry's 59 entries/49 enabled sources; it is historical, not a current certificate. Its `registry.path` resolves relative to the manifest file; `registry.sha256` binds the exact registry bytes. The reviewed FAO/ITLOS/official-host registry changes have invalidated that example now. Before deployment, deliberately review the new scope and create a separate current deployment manifest/config; do not silently refresh this historical example or remove its hash guard. The future worker must also parse that registry and verify that its enabled source set exactly matches the manifest; the stdlib wrapper does not parse YAML.

Only after the future module exists, is tested, and is separately authorized for operation, a configured command could be:

```json
{
  "worker_argv": [
    "/ABSOLUTE/PATH/TO/jobagg/.venv/bin/python",
    "-m", "jobagg.remediation_worker",
    "--request", "{request_path}",
    "--report", "{report_path}"
  ]
}
```

That module is **proposed and not implemented here**. This is a fragment to merge into a copied configuration, not a complete configuration. Set `worker_cwd` to the correct absolute project directory. Do not substitute the ordinary sync command merely to make this wrapper run: it does not implement the maintained-worker acceptance contract.

The explicit opt-in command would be:

```sh
/ABSOLUTE/PATH/TO/python3 /ABSOLUTE/PATH/TO/runner.py --config /ABSOLUTE/PATH/TO/config.json --execute
```

The sample proposes a 600-second tick limit, 10-second termination grace, listing evidence no older than 60 minutes, and detail/required-attachment evidence no older than 24 hours. These are proposed operating settings, not existing guarantees. A worker can use a smaller internal work budget and report incomplete before the hard timeout; hard termination is a fallback.

## Proposed cron entry — not installed

For a reviewed, configured worker, a proposed Mac cron dispatcher is:

```cron
*/15 * * * * /ABSOLUTE/PATH/TO/python3 /ABSOLUTE/PATH/TO/runner.py --config /ABSOLUTE/PATH/TO/config.json --execute >> /ABSOLUTE/PATH/TO/private/jobagg-remediation/dispatcher.log 2>&1
```

Replace paths; quote paths containing spaces and escape literal `%` characters according to cron syntax. The log parent directory must already exist. This line is documentation only; no crontab is installed. A Mac must be awake for cron to dispatch. The 15-minute dispatcher is separate from the worker's per-source list cadence and detail freshness policy.

Existing heartbeat/sync entry points must cooperate with the **same absolute `shared_lock_path`** before any new dispatcher is enabled. Adding a flock to this wrapper cannot stop a legacy entry point that ignores it. Consolidation/publication operations also need to honor this shared exclusion rule. Pick one scheduling owner or make all entry points enter the same wrapper/lock protocol; this proposal changes neither today.

## Execution, state, and failure semantics

| Result | Exit code | Meaning |
|---|---:|---|
| `dry_run` | 0 | Configuration inspected; no completion claim and no dispatch. |
| `complete` | 0 | Worker exited zero and fresh report/evidence satisfied this prototype's protocol checks; not a production completeness certificate. |
| `incomplete` | 2 | Valid report identifies blocked/pending work or failed completeness/content invariants. |
| `process_failure` | 3 | Missing worker/report, nonzero worker exit, invalid/stale/mismatched evidence, registry drift, timeout, or wrapper error. |
| `lock_busy` | 75 | Another cooperating entry point owns the lock; no worker started and existing state remains unchanged. |

A worker should exit **zero for a valid incomplete report**. The wrapper maps that result to exit 2. A nonzero worker exit is always process failure, even if a plausible acceptance file exists. The global declaration must be complete **and** every per-source invariant must pass. One `complete: true` flag is never sufficient.

Each attempted dispatch has a UUID directory under `state_dir/runs/` containing the frozen manifest, request, private stdout/stderr logs, worker evidence, and atomic `state.json`. The current `state_dir/state.json` is atomically replaced for running/final states; `last_complete.json` changes **only** after accepted completion. Incomplete/failure runs preserve the previous successful record. A crash can leave the current record at `running`; that is not a success. Configurations invalid before dispatch are reported as JSON on stdout and exit 3, without claiming an accepted run.

Files created by `--execute` use a private umask. Run directories/logs can grow; retention and rotation are deployment responsibilities and should preserve the latest successful evidence and unresolved failure evidence. Do not log credentials or cookie headers. The example does not automatically delete audit history.

The parent acquires a nonblocking `fcntl.flock`; the worker inherits its open descriptor as `JOBAGG_SHARED_LOCK_FD`. The worker must retain this descriptor and must not detach daemons or start processes outside its process group. The wrapper sends SIGTERM, waits the grace period, then SIGKILLs the group on timeout/interruption; it also rejects a worker that returns while descendants remain. Retaining the inherited lock prevents a replacement wrapper from overlapping a worker after an abrupt parent death. A worker should enforce `deadline_at` itself as well, because SIGKILL of the wrapper cannot run parent cleanup. This is cooperative process coordination, not a security boundary against malicious workers.

## Worker request: version 1

The wrapper writes `request.json` with:

```json
{
  "schema_version": 1,
  "run_id": "UUID",
  "started_at": "2026-09-10T00:00:00+00:00",
  "deadline_at": "2026-09-10T00:10:00+00:00",
  "source_manifest_path": "/ABSOLUTE/RUN/source_manifest.json",
  "source_manifest_sha256": "64 lowercase hex characters",
  "source_registry": {
    "path": "/ABSOLUTE/PROJECT/packages/jobagg/config/organizations.yaml",
    "sha256": "64 lowercase hex characters"
  },
  "expected_source_ids": ["un_inspira"],
  "report_path": "/ABSOLUTE/RUN/acceptance.json",
  "freshness_limits": {
    "listing_max_age_seconds": 3600,
    "detail_max_age_seconds": 86400
  }
}
```

The worker reads this request, uses the exact source scope, respects the deadline, and atomically writes the report last. It must not interpret source-page text as instructions. Every enabled manifest source appears exactly once in the report; disabled sources are excluded from acceptance. The wrapper checks the manifest and bound registry before/after work for drift.

## Acceptance report and per-source evidence

Illustrative shape; placeholder digests/timestamps below are not valid acceptance evidence:

```json
{
  "schema_version": 1,
  "run_id": "UUID from request",
  "source_manifest_sha256": "digest from request",
  "generated_at": "fresh ISO timestamp with timezone",
  "status": "complete",
  "sources": [{
    "source_id": "un_inspira",
    "status": "complete",
    "counts": {
      "listed": 1, "published": 1, "complete_details": 1,
      "listing_only": 0, "empty_details": 0,
      "attachments_pending": 0, "attachments_failed": 0,
      "verified_attachments": 0
    },
    "evidence": {"path": "un_inspira-evidence.json", "sha256": "SHA256 of evidence bytes"}
  }]
}
```

A blocked/incomplete source may instead be `{ "source_id": "...", "status": "blocked", "reason": "specific unresolved cause" }`; it cannot produce global accepted completion. Invalid report structure is process failure. A structurally valid “complete” report with unequal counts/sets or failed content checks becomes incomplete; inconsistent claimed counts versus its own evidence are invalid.

Each complete source's evidence is a run-local JSON artifact:

```json
{
  "schema_version": 1,
  "run_id": "UUID from request",
  "source_manifest_sha256": "digest from request",
  "generated_at": "fresh ISO timestamp with timezone",
  "source_id": "un_inspira",
  "listing_observed_at": "timestamp within listing freshness limit",
  "scope_verified": true,
  "normalization": "jobtext-v1-whitespace-known-boilerplate",
  "enumeration": {
    "method": "reported_total",
    "reported_unique_total": 1,
    "pagination_complete": true,
    "verified_zero": false
  },
  "listing_ids": ["284427"],
  "published_ids": ["284427"],
  "complete_detail_ids": ["284427"],
  "content_results": [{
    "job_id": "284427", "source_job_id": "284427", "database_job_id": "284427",
    "source_text_sha256": "SHA256 of normalized full public job text",
    "database_text_sha256": "SHA256 of corresponding normalized database text",
    "source_chars": 5000, "database_chars": 5000,
    "source_fetched_at": "timestamp within detail freshness limit",
    "source_content_valid": true, "required_fields_complete": true,
    "attachment_discovery_validated": true,
    "required_attachment_ids": [],
    "attachments": []
  }]
}
```

Counts must agree with their ID arrays. IDs must be unique nonempty strings. Complete listing, published, complete-detail, and checked-content ID sets must be equal. The catalog can retain a source-advertised job with an expired deadline; `complete_details` does not mean it is eligible for an application-ready filter. Exact identity and public content presence are the acceptance target.

`reported_unique_total` means a validated count of **unique vacancies**, not raw rows, result facets, multilingual copies, or unchecked pagination metadata. If no trustworthy numeric total exists, use:

```json
{
  "method": "independent_terminal",
  "reported_unique_total": null,
  "pagination_complete": true,
  "independent_listing_ids": ["284427"],
  "terminal_evidence": {"path": "un_inspira-terminal.html", "sha256": "SHA256 of saved evidence"}
}
```

The independent IDs must equal `listing_ids`, and a hashed run-local terminal artifact is mandatory. “Independent” requires a genuinely separate enumeration/check capable of revealing parser/pagination omissions; copying the same parsed IDs is not valid. The worker must validate the artifact's source/scope and positive terminal evidence. Zero vacancies additionally require `verified_zero: true`; absence of matches is not verified empty.

For each required public JD/ToR attachment, include its ID in `required_attachment_ids`, and one `attachments` result containing `attachment_id` plus the same text digest, character count, fetched time, source-content-valid, and required-fields-complete fields used for job text. These sets must match exactly. Even an empty attachment list requires `attachment_discovery_validated: true`. Pending and failed attachment counts must be zero for completion.

The normalization identifier is a **future worker contract**, not an implemented extractor here: preserve full substantive public job text and required attachment text, excluding only whitespace differences and specifically known headers/footers. Never truncate content, omit unknown sections, hash only a teaser, or normalize away differences to force equality. The worker must validate job identity, required fields, official-page validity, required document discovery, and source/DB extraction equivalence before reporting true flags.

## Required maintained-worker additions before production fetching

These requirements extend the implementation prompt; they are **not implemented by this version 1 wrapper** and must not be inferred from its `complete` result:

- Fresh source/locale/scope manifests; actual unique ID unions and public variants; independent positive terminal/empty evidence; explicit current, retained/unseen and disabled partitions. Canonical ILO/IDB/OSCE inventory gaps cannot be certified by copying the old browser manifests.
- Per-host due-work budgets, Retry-After floors, durable cooldowns/half-open probes and source fairness. Explicit eligible refresh must survive cache shortcuts while keeping access limits. A multi-host source cannot let one institution's block silently starve unrelated hosts.
- Atomic accepted-detail body/identity plus backlog checkpoint before the next request, with interruption/rollback tests. Required document bytes/text/associations and verification state need the same transactional guarantees.
- Separate controller, browser/manual coordination and source-attempt state schemas. Missing process timestamps must not crash inspection or refresh source evidence. Stop new dispatch on controller failure, keep locks until children stop, and report each child's durable result separately from the parent exit code.
- Recursive required-document discovery from authoritative main HTML/API, all public language versions, PDF/Office links and embedded substantive content. Bind the discovery input, preserve full bytes and faithful page/sheet text, review OCR/images, and retain unresolved HTTP 404/robots/challenge/unsupported edges. A nonempty attachment list, long text or matching worker-supplied hash is insufficient.
- Independent full returned identity/title/section parity and hash-bound negative partial-body findings. Changed body/raw links/language or extraction versions invalidate earlier evidence. New listing-verified rows stay unready without explicit complete discovery and no unresolved required documents.
- Independent public metadata/lifecycle checks: semantic posted-versus-deadline fields, authoritative detail dates retained across list refreshes, explicit Taleo timezone/unknown/open-ended states, raw date provenance and every observed ID's retained lifecycle/alias mapping. Shared directory/application URLs cannot merge distinct requisitions. Body/hash equality and the prototype's worker-supplied flags do not implement these checks; extend the maintained verifier/report contract and transition regressions before acceptance.
- Final classification after main-body/title repair; foreign-key/BLOB/raw-association checks; scratch-snapshot FTS5 rank 1 external-content parity; canonical JSON all-field and CSV projection checks; actual intended API HTTP route checks. Attachment-only text and binary-download support remain distinct missing consumer features.
- Exact plan-bound database and export receipts, same-snapshot proof hashes, verified originals, SQLite reservations, all_jobs-last ordering, guarded per-file export replacement, quiet writers and honest maybe-committed crash state. Current helpers are parameterized-maintenance candidates under the dated manual run, not an already installed recurring publisher. There is no implemented atomic generation pointer across all databases and exports.

Keep release integrity, completeness certification and operational acceptance as separate outcomes. A consistent partial publication may preserve discoveries while reporting unresolved work; it must not update a completeness certificate. The three-cycle/24-hour observation begins only after the real worker and intended dispatch arrangement actually operate. A missing worker should cause the reviewed native heartbeat to continue the highest-priority bounded implementation task, not configure this wrapper with ordinary sync or manufacture completion evidence.

## Assurance boundary

In this prototype, `complete` and `last_complete.json` mean **worker report validated complete** only. They must never be interpreted as the main prompt's `completeness_certified` gate. Before production use, extend the contract to enforce the publication generation, separate release-integrity/completeness gates, adapter/normalizer/attachment-extractor versions, and actual DB/export/API comparisons.

The wrapper validates fresh run binding, exact enabled-source coverage, registry/manifest stability, duplicate-free ID equality, counts, evidence file hashes, content digest equality, source timestamps, and mandatory attachment coverage. It rejects evidence paths outside the unique run directory and stale/mismatched reports.

**Hashes and validity flags are still supplied by the future worker.** The wrapper cannot independently prove that a hash represents authentic full source text or actual published database content, that a parser recognized every field, or that terminal-page evidence was interpreted correctly. That requires the implemented and independently tested extraction/verifier worker, source visits, public-page/attachment fixtures, database-generation binding, and regression/statistical audits described in the main remediation prompt. A fake worker exists only to exercise this protocol; passing its tests does not fix production fetching or certify current listings.
