# Runtime M1 implementation checkpoint

M1 is in progress and is **not activation-ready**. Preserve the immutable
[M0 baseline](runtime-m0-baseline.md), its JSON manifest and commit `30ea183c`.
Implementation stages are committed as `d1a3273f` and `db542499`.

## Development and production separation

Continue implementation in the attached `codex/jobagg-m1-runtime` worktree.
The primary checkout's tracked dispatcher is an actual cron entry point. Do not
merge or copy candidate dispatcher files there during ordinary development.
Prepare candidate code, tests and review in isolation; activation remains a
separately approved, fenced operation under the handover and R1–R8.

The initial M1 edits were mistakenly made at that live dispatcher path. A bounded
read of internal attempt receipts establishes five held scheduled invocations at
19:30, 19:45, 20:00, 20:15 and 20:30 EAT on 22 October 2026, each reporting that
`runtime_authority` was missing from the selected production release. All five
explicitly record `worker_dispatched=false` and `publisher_dispatched=false`.
The original runner and observability helper were restored byte-for-byte and
committed in the primary checkout as `c636e280` at 20:31:52 EAT. No process,
schedule, configuration, database, recovery journal or hold was reset. The health
record retains the interruption evidence, including fields introduced by the
candidate helper; do not infer accepted backlog from those fields alone.

Restored live-path SHA-256 values:

- `runner.py`: `c27dcd9856c53905d21722ce94db376e55b0af540f840ccf7ed6a521336ed306`
- `runner_observability.py`: `bc0e2b718e24ea1bdfd217b080c3b07685a1b33ee31593fa2181e04a4c8a2f58`

## Prepared components

- Budget-independent active-run authority with transactional source revision,
  complete schema binding, actual owner inode binding and explicit legacy repair.
- Both pre-intent windows preserve and resume the original accepted batch, with
  storage budget absent, false and true; failed/killed workers are not accepted.
- Task-led selective lineage with shared canonical identity and legacy crosswalk;
  older successful evidence survives newer failures. SQL and artifact costs are
  instrumented separately from deterministic projection manifests.
- Transactional task-count summaries; shared-policy owner-batch snapshots and
  source revision caches; final evidence audit; report eligibility derived from
  bounded health summaries without report-time `choose()`.
- Entry-anchored clocks, supervisor entry watchdog and measured phase-admission
  contract. Missing viability measurements defer accepted work rather than
  fabricating a numerical production threshold.
- Stable stage/class recurrence, one reviewed probe per evidence version, durable
  episode history and stage-success-only reset.
- Bounded producer-owned attempt index, tested observer entry point, restart
  deduplication, failure/recovery and quiet-versus-readback distinctions.
- A real local fixture runs worker and publisher for three cycles, selects limits
  `[1,1,2]` through qualified controller history, verifies actual overlap and text
  readback, then verifies conservative pressure downshift and duplicate callbacks.

## Remaining immediate scope

The publication boundary is being completed. A worker revision alone does not
prove that source/consolidated destinations, exports and older pending work are
clear; the fast no-op path stays disabled without that independent validation.
A completed bounded generation with residual accepted/rejected work currently
remains explicitly fenced. It must gain bounded continuation before M1 can be
called complete. Persistent all-writer policy authority/migration and historical
listing/document/export reconciliation still require acceptance evidence.

No production A8 or 24-hour observation has been started. No claim is made about
current all-source completeness, capacity compliance, production throughput or
external-volume scale performance.
