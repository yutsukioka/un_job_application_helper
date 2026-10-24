# PR #143 isolated idle-control repair

The user approved the [bounded repair proposal](pr143-control-rollback-repair-review-2026-10-23.md)
on **24 October 2026 (Africa/Nairobi)**. This increment implements that repair and
resumes preparation on the same isolated native copy. Production activation,
retirement, quota resets and historical proof reconstruction remain unauthorized.

## Repair contract

[isolated_control_repair.py](../jobagg/isolated_control_repair.py) is an explicit
operator API. Ordinary startup never invokes it. Normal runtime validation still
checks both the SQL structure and independent source head; splitting the internal
SQL-structure check does not make an uncertain head admissible.

The read-only preview requires a canonical copied `worker/jobs.sqlite3`, the exact
snapshot pinned by its native bootstrap, separation from original production
roots, the actual shared owner, both unchanged maintenance markers, the original
before-attempt receipt and failed pre-admission report, exact configuration and
registry hashes, complete copied export boundary, no accepted marker/run controls
or unresolved generation, and a copy-owned external source head. The entire SQL
state must equal the saved initial idle state with no run ID. The ahead head must
match its original epoch, owner/database identity and unchanged domain revision.
No missing or stale before-proof can be supplied by the current database alone.

Preview runs `quick_check(1)` and hashes the complete checkpointed database under a
finite ceiling of at most 900 seconds, SQLite progress checks and bounded file
reads. A timeout is an unfinished check, never an integrity certificate. The plan
pins exact bytes and physical identities. A nonempty WAL at the initial rollback
boundary requires separate review; it is not silently checkpointed by this tool.

Execution rechecks the same state, head, owner, markers and dependency identities,
and records an exclusive durable intent before its one SQL update. Its authorizer
permits only a compare-and-set of `jobagg_runtime_state.source_transaction_id`.
It completes the already attempted token handoff in SQL; **the independent ahead
head is not rewritten or rewound**. Jobs, attempts, charges, accepted evidence,
original control dispositions and dirty versions cannot be changed by this
operation. Commit and readback recheck the fences and exact expected state.

The same intent resumes after interruption before the update, before commit or
after commit. A rolled-back retry must still match the original database bytes;
a committed retry must have the original intent, exact resulting state and
unchanged independent head. Later legitimate transactions make this old repair
inapplicable. Exclusive completion receipts preserve the operation's evidence.
There is no startup retry, fresh epoch, database restore or blanket hold release.

## Operating-system boundary

The copied SQLite temporary directory fixes the reproduced statement-journal
failure without adding production write access. macOS's privileged `/bin/ps`
cannot execute inside the extra rehearsal sandbox. An exact-byte copy with its
privilege bits removed was also unusable; it was not adopted as a census tool.

The explicit repair preview/execution therefore run outside that extra OS profile
so that the real system process census can run. They retain the actual owner,
both maintenance markers, copy-root separation and the narrow SQL authorizer.
The dispatcher also needs the real process census for stale-run reconciliation,
so its parent runs outside the extra profile. The rehearsal harness wraps every
worker/publisher child with that unchanged OS profile, retaining production-write
and network denial and copy-owned temporary storage. A separate child probe
verifies both denials. These distinctions are recorded; neither the dispatcher
parent nor explicit repair is reported as having the extra sandbox protection.

## Regression scope

The tests use real native SQL fences, a real ahead source head and an induced
control-only rollback. They verify exact token-only success, unchanged head and
all other tables/debt, idempotent completion, interruptions on either side of the
commit, and authorizer refusal of domain writes. Negative cases cover absent /
changed approval proofs, active state, revision/schema/database/alias/head changes,
wrong owner, changed marker, accepted marker, run controls, unresolved generation,
registry changes, expired budget and process pressure. Refusal tests assert the
specific intended gate. Existing ordinary restore-fence and containment tests
remain part of validation; overlapping counts are not added to the full suite.

## Observed repair and validation

At **07:00 EAT on 24 October**, the same 4,155,944,960-byte copied database passed
full SQLite integrity and complete byte hashing in **223.9 seconds**. Its preview
SHA-256 was `9fabfda81afc23953906daf9b8b471bf47fe6d1ff602baf9f95c82cdebb9b47d`.
The explicit repair then completed in **2.757 seconds**. The native state validated;
only its attempted transaction token changed. The independent head bytes and all
11 counted domain/debt tables remained unchanged, including 23,438 jobs, 9,500
tasks, 67,017 attempts, 7,265 observations, 663 documents, 6,471 original controls,
4,452 accepted versions, 59 accepted events, 7,314 dirty units and 70,326 retained
dirty versions. This operation did not close historical controls.

Runtime commit `5b464480` is staged read-only with 1,982 source files and **177**
package modules sealed as
`338ab70868a45148b8e516acf111ed667753c6df13b2940e06ea187c6837b6aa`.
The full suite reports **4,049 passed / 9 skipped** (637.93 seconds); dispatcher and
repository checks report **169 passed plus 17 subtests** (25.31 seconds). The
43-test focused group overlaps the full suite and is not added. Changed Python
files pass Ruff; whitespace checks pass. No production provider or cutover result
is implied by these tests.

## Rehearsal procedure and preserved evidence

The caller hands off only its copied marker under the actual shared owner; the
real production marker remains present. Deliberate interruption before projection
retained accepted work without refetching. The original wrapper must terminate
before reconciliation: recovery correctly refused an immediate attempt from the
same still-running wrapper. An explicit journal-only recovery switch also
correctly refused the pre-intent window, which resumes through ordinary native
accepted-work continuation.

A second deliberate interruption occurred after durable publication intent and
before publisher spawn. The exact prior complete gate names the earlier copied
calibration journal root rather than the proposed new journal root. Automatic
path adoption refused this difference. An owner-held review verified the exact
canonical preceding plan/result, original hashes, chronology, complete copied
export boundary and absence of former processes. The existing no-write API then
closed only that demonstrably unstarted intent, preserving its original paths,
inputs and accepted work. No journal payload was copied, rebased or replaced.

Deliberate failures reached the existing end-to-end recurrence ceiling. Its
operator-review API authorized one bounded same-intent recovery, preserving the
failure history and counters. The initial automatic-root attempt refused the
journal-root difference; a new exact-root review authorized the next retained
batch only after canonical no-write reconciliation. There was no automatic
probe, counter reset or general hold release. Private receipts retain every
failed attempt and each distinct review.

## Verified retained-batch result

At **07:25 EAT**, the original accepted run completed a bounded publication-only
batch on the same isolated copy. Its selected projection was **7,524,352 bytes**;
**40 units** were processed, **39 jobs changed** in the source/consolidated stores,
and **eight exports** were verified. Generation
`7e9f782fa8364f408f59e865d6a9be44` is complete, with `readback_verified=true` and
the complete **50-store / 200-export** boundary still verified. The configured
new journal root now owns that new generation; the earlier calibration journal
and its exact reviewed references remain intact.

The wrapper ran **156.740 seconds** (159.516 seconds for the whole recorded
continuation procedure). This measures the real publisher/validation/export path,
not merely the earlier 0.754-second planner call. The two-second experimental
publisher floor cannot be approved for that complete path; production phase and
admission proposals must incorporate the actual cost and remaining cold/worst-case
review. The active rehearsal configuration is preserved rather than edited beneath
its accepted work. Production setting fields remain unapproved/null.

ADB 260912 was rejected because its replacement was shorter than retained live
text without the required full-source contract. Its dirty unit is held. Current
unit dispositions are **39 acknowledged, 37 held and 7,238 pending** (7,314 total);
these unit counts are separate from **5,483 historical control holds**, all of
which remain intact. Native domain counts and revision 48,664 are unchanged.
The authority is valid in accepted continuation for the same run, with no active
worker/publisher process. The remaining queue is resumable and is not empty.

No worker was dispatched or re-executed. The current batch reuses independently
accepted retained evidence; it certifies neither fresh provider coverage nor
whole-job completeness. Both copied and real maintenance fences were restored /
preserved. A real child probe verified OS-level network and production-write
denial, and the publisher child used that exact profile. The dispatcher parent
remained outside the profile for its required process census. Reconciliation and
one retained publication batch passed; a fresh fetch-to-publication cycle and
stock entry-watchdog rehearsal remain separate deployment validation.

## Deployment limits

A successful isolated repair or retained publication-only rehearsal cannot prove
fresh provider coverage, all-source main-text completeness, changed-generation
capacity or production cutover readiness. The separate 100 GB storage/reader plan
and production-scope setting approvals remain in force. No HDD payload copy or
production cleanup is part of this operation.
