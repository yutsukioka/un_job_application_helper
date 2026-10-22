# Runtime M1 implementation checkpoint

M1 is in progress and is **not activation-ready**. Preserve the immutable
[M0 baseline](runtime-m0-baseline.md), its JSON manifest and commit `30ea183c`.
Implementation stages include `d1a3273f`, `db542499` and `685c2a85`.

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

The next prepared stage adds indexed persistent quota authority, explicit
all-writer import, conservative restore/head fencing, bounded evidence audit,
scheduling headers, immutable accepted-evidence references and a versioned
selected publication projection. Its focused worker/lineage/snapshot/policy
regression batch passed 134 tests. These APIs still require end-to-end dispatcher
continuation, migration and fault-boundary validation before activation.

## Integrated runtime continuation

The native dispatcher now selects bounded dirty dependencies before projection,
retains original worker proofs, journals each publication batch separately, and
drains the same accepted run before fetching again. A real three-generation
fixture verifies destination commits/exports and then a genuine empty fast path.
A cold native worker fixture performs useful work without a historical policy
census and records its accepted reference in the acceptance transaction. The
combined M0/M1, snapshot, policy, budget and binding regression batch passed
151 tests. This is one combined denominator; earlier overlapping results are not
additional tests.

Native source cohorts retain service order and explicit oversized/unknown holds.
Closing-time transitions invalidate no-op truth and enqueue selected revalidation.
Entry wall/elapsed bounds also cover pending-work and artifact hashing. Alternating
stage failures retain an end-to-end episode; a zero-task work-deadline exit cannot
reset the worker episode. Native controller/policy SQL allocation requires reviewed
finite caps under the existing storage escrow.

## Remaining immediate scope

The native idle-start path now continues older queued work using a typed,
revision-fenced publication-only certificate; it does not invent a worker exit.
Native budget admission is explicitly `per_bounded_batch`: each completed batch
retains an exact final escrow receipt before fresh finite tokens are admitted for
the same frozen accepted run. The fixed total/category ceilings and quota charges
remain in force. A real budget/semantic/runtime fixture covers three batches,
verified exports, empty follow-up and interruption after the durable escrow
receipt; its combined routing/budget regression batch passed 58 tests.

The full package suite on immutable commit `08d8546d` produced 3,760 passes,
9 skips and one clock-injection failure. The local monotonic seam was restored
alongside entry propagation; its 17-test targeted batch passed. The dispatcher
suite then passed 166 tests and 17 subtests after updating the three-total-failure
end-to-end ceiling. Later candidate changes still require final broad validation.

The next ownership stage binds source mutations to the actual shared-owner FD
and checks ownership again at commit. A source transaction head outside the
SQLite file prevents an in-place stale restore or an uncertain rollback from
certifying an empty queue. Native phase/revision controls reject raw writers;
only explicitly named control tables are exempt from domain history tracking.
Its source-restore/owner, real native drain and complete binding batch passed
19 tests; the broader current routing/native-worker batch passed 28 tests before
those dedicated negative cases were added. These groups overlap and are not
summed. All 161 package Python modules are included in the reviewed seal.

Complete checkpointed all-writer migration and exact historical listing/document/
export reconciliation, native budget/semantic-fence integration and its crash
faults, retained-history scale/cold-output gates, and the full regression/review
pass. In particular, preparation APIs and bounded fixtures do not prove that the
actual production control roots have been reconciled or are activation-ready.

No production A8 or 24-hour observation has been started. No claim is made about
current all-source completeness, capacity compliance, production throughput or
external-volume scale performance.

## Acceptance and migration continuation

The next stage replaces legacy done-row inference with an exact successful-exit
catalogue and immutable observation snapshots. Native successful listing, detail
and document occurrences also record independent acceptance in the same source
transaction. Selected projection version 4 carries explicit backfill dispositions;
versions 1–3 retain their original validator contracts.

Quota preparation now commits finite payload/replay/verification pages, imports
unindexed reservations, fences legacy APIs throughout preparation and journals
cutover before rename. A fault after rename resumes the same charged authority.
Task/shared-policy reconciliation retains orphan charges and flags unmatched task
attempts. An overdue audit holds read-only; explicit bounded catch-up retains its
old completion timestamp until the complete evidence pass finishes.

Freshness transitions and reviewed, checkpointed contract revalidation invalidate
empty-work truth. A full destination/export cursor is checked before fetching.
Proven no-generation publisher deferrals keep the original sealed projection and
selection, preserve old intent/outcome and resume in a new bounded publisher batch
with unchanged storage ceilings. The real native drain fixture passes five
profiles, including deferral with storage budget disabled/enabled and the prior
escrow-receipt interruption. Policy migration/audit/reconciliation passes eight
focused cases; these are separate scopes, not a package-wide result.
