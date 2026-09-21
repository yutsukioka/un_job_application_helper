# Staged dispatcher reliability repair

This directory is a reviewed deployment candidate. No live tick, schedule, database, shared-policy store or dispatcher-history file was changed by this work. `dispatcher.integration.example.json` targets the eventual installed paths and is a proposal; its dry-run accepted the current 49-source enabled manifest.

The existing runs show repeated 600-second wrapper timeouts without an acceptance report. The wrapper previously saved only a generic process failure, did not record child identity/phase or durable terminal outcomes, and never reconciled an abandoned `running` state. The 10:15 UTC run remains historical evidence; its age alone is not proof that a process has ended. Worker pacing/deadline defects are a separate staged worker repair owned by root.

The runner now:

- Keeps the actual shared owner across worker, validation and publication. Both subprocesses inherit its descriptor, run in separate process groups, and use explicit working directories and Python import paths.
- Records phase, PID, report hashes and graceful deadline outcomes. A worker returning exit0 and a valid acceptance during TERM grace is incomplete; a killed/nonzero worker remains a process failure even if it wrote a report. TERM then KILL covers descendants.
- Journals `outcome.json` before replacing per-run and latest state. Later owner acquisition repairs interrupted summaries from that journal. Abandoned runs require the exact owner inode, absent recorded PIDs/groups and an actual process-command census. Their original state bytes are preserved in a hash-named archive; execution is not replayed.
- Supports optional `publication_argv` after a valid worker result, including incomplete worker coverage. It pins enabled scope, registry, acceptance bytes, worker database/WAL bytes and an absolute publication deadline. The publisher separately proves its transactional observation-set digest. Publication does not certify job completeness.
- Records an immutable publication intent before spawning. A normal next tick automatically resumes a valid interrupted or incomplete publication before fetching more work. It uses the same intent/configuration/source snapshot and the publisher's idempotent generation journal, with a new bounded recovery request linked to the original. It never reruns the worker for that recovery. Invalid evidence remains held. `--recover-publication --execute` is also available for an explicit recovery-only invocation.
- Preserves the original unknown/incomplete outcome and records a separate publication resolution. A committed receipt surviving an interrupted final-state write is reconciled without reissuing publication. Changed configuration, worker bytes or borrowed resolution evidence is rejected.
- Checks an optional `maintenance_file` under the shared owner before dispatch. Presence returns `maintenance_paused` (exit75) without starting work. Dry-run reports its state and creates no runtime files.

## Configuration and timing

The staged example preserves `--max-seconds 540`, `timeout_seconds: 600`, `terminate_grace_seconds: 10`, 60 task/30 detail ceilings and existing source/host limits. It adds:

```json
{
  "publication_timeout_seconds": 180,
  "total_timeout_seconds": 840,
  "publication_worker_database": "/absolute/worker/jobs.sqlite3",
  "publication_cwd": "/absolute/packages/jobagg",
  "maintenance_file": "/absolute/remediation/dispatcher.maintenance"
}
```

600 seconds worker +180 publication +two 10-second cleanup reserves =800 seconds, within the 840-second total budget and the 900-second scheduling interval. Each command uses remaining monotonic budget; publication receives the corresponding absolute `deadline_epoch` and dynamic `--max-seconds`. Process reaping, filesystem flushes and OS scheduling introduce small overhead; the interval retains 60 seconds beyond the configured total. Publication limits remain bounded in its own CLI; root selects its listing-frame/detail caps.

The publication argv uses `{publication_request_path}`, `{publication_report_path}` and `{publication_max_seconds}`. `{publication_deadline_at}` and `{shared_lock_fd}` are also supported. `JOBAGG_SHARED_LOCK_FD` is always inherited, so the publisher must validate and reuse it rather than acquire a different owner.

When publication is omitted, the existing worker-only configuration and exit codes remain compatible: complete0, incomplete2, process_failure3, lock_busy75. The new maintenance state also uses75. Incomplete coverage is not a process failure, and a successful publication does not upgrade an incomplete worker result to complete.

## Validation

33 tests plus17 existing subtests pass, including real temporary subprocess deadline/signals, descendant cleanup, stale-state refusal under a busy owner/live PID, interrupted final-state writes, publication receipt tampering, SIGKILL after commit before receipt, and valid incomplete deferral. Recovery checks assert one worker start and one commit across two ticks. Ruff passes. Tests use only temporary fixtures, with no public network, live database or schedule access. Process-census tests require ordinary local process visibility; restricted sandbox denial fails closed.

The original runner hardlink was severed by atomic replacement before modification. The original installed/proposed runner remains a separate 19,839-byte file; its hash is recorded in `reliability_review_receipt_001.json`. Root owns deployment, maintained worker/publisher changes and schedule operation.
