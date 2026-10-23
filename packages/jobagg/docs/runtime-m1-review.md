# M1 scoped corrective review

Reviewed candidate `34052ea8` against the complete 22 October production cron
algorithm handover, its October 16 amendments A1–A10 / R-A–R-E, and
[JobAgg R1–R8](change-and-review-policy.md) with its
[evidence map](change-and-review-evidence.md). Corrections are committed in
`43b5a61f0eb5ed33c0ae10de697becbc6ec5bc28`. The isolated branch also reconciles
current `master` (`25f278b5`, PR #141); the sole merge conflict was the reviewed
successor seal, and the merge retained identical M0/M1 runtime bytes.

This is a scoped corrective pass by the implementing agent, not independent
human approval. Local results do not close the external-volume, capacity,
CI/security, production cutover or natural-cycle acceptance gates. The final
results and observation timestamp are in [the validation manifest](runtime-m1-validation.json).

## Actionable findings corrected

### P1 — Recheck the actual destination owner through commit (R4/R6)

Trigger: a publisher connection remains open while its actual owner FD is
unlocked, or the owner pathname is replaced, after DML and before COMMIT.
`publication_fence.configure()` checked only the FD's device/inode on DML and
installed no commit guard. Reusing a prepared COMMIT or entering the semantic
write scope also bypassed the intended lifetime boundary. A late publisher could
commit after another coordinator became eligible.

The new regressions failed against the reviewed candidate in all three lifetime
cases (unlock, replacement and semantic-authorizer composition). Destination
writes and transaction/release boundaries now verify the actual held lock,
pathname identity and live scope. Every mutation invalidates cached transaction
statements. The semantic/storage authorizer composes this check rather than
replacing it; its lease/schema guards remain active.

Evidence: `test_destination_cannot_mutate_or_commit_after_owner_loss` and
`test_semantic_authorizer_preserves_destination_commit_fence` in
[test_publication_fence.py](../tests/test_publication_fence.py).

### P1 — Protect destination clocks and authority metadata (R4/R8)

Trigger: a raw connection updates/deletes `jobagg_publication_revision`, or an
admitted manual writer resets its revision after changing domain data. The M1
clock table was excluded from all mutation triggers. Resetting it could conceal
a retained-live comparator change while old export identities still matched,
allowing a false no-op certificate.

The raw-update regression failed on the reviewed candidate. Publication fence
version 2 now guards authority rows and immutable identity fields, and permits
revision advancement only from the known domain triggers. Even an ordinary
owner-held writer cannot reset the clock. Unknown/older native fences require
explicit compatible preparation; startup does not upgrade them.

Evidence: `test_destination_clock_and_binding_cannot_be_rewritten`, existing
changed-destination/export no-op regressions, and real native publication drains.

### P2 — Make native acceptance durability match its contract (R4/R5)

Trigger: a native source transaction commits accepted observations, task completion
and dirty versions using `JobDatabase`'s inherited `synchronous=NORMAL`, followed
by power loss. NORMAL may lose recent WAL commits; a durable external head can
then only report repair, not recover the promised accepted SQL occurrence.
The new M1 authority made this setting part of an explicit durability promise.

Native source and publication stores now open with FULL synchronization. Legacy
unmigrated stores retain their prior setting. Policy reservations already use
FULL. The actual native worker/storage/semantic fixture and destination mutation
fixture assert the real connection setting; this is configuration verification,
not a power-loss/filesystem guarantee. External-volume fsync cost remains a
release benchmark.

Evidence: [test_native_worker.py](../tests/test_native_worker.py) and the owner-loss
fixture's `PRAGMA synchronous` assertion.

### P2 — Use a boot-bound clock through entry and every phase (R5/R6)

Trigger: system sleep stops CPython's Mac monotonic clock while a UTC correction
masks elapsed wall time. The old paired comparisons can both remain below their
limits. They also had no boot-domain binding, and a new phase could derive a
fresh allowance after an earlier preparation phase consumed the budget.

A suspend-aware raw budget now supplements the existing monotonic/UTC guards:
Darwin uses `CLOCK_MONOTONIC_RAW`; Linux uses `CLOCK_BOOTTIME`. Host, boot UUID,
provider and UTC origin are recorded. Guarded HTTP admission/timeout clipping
also uses that earlier bound; known local expiry does not create a provider
cooldown, while certificate/identity/access review remains conservative. Foreign/stale clock domains fail closed.
The supervisor/runner, worker startup and parallel task contexts, selected
projection, same-generation recovery, publisher and logged/nonlogged waits all
inherit an earlier absolute bound. The agreed 840-second entry ceiling and
existing smaller phase/report/cleanup allocations cannot be extended by later
configuration or nested scopes. This changes no source/quota/request/concurrency
ceiling and installs no supervisor or schedule.

Legacy verifier helpers with an infinite local deadline add no new allowance;
they retain any finite ambient task clock. Direct legacy `perform()` and retained
metadata behavior are covered by the complete regression run. Work expiration
keeps the distinct worker reporting reserve.

The dispatcher needs a byte-identical bootstrap clock copy before selected-package
imports. That copy is explicitly included in the runtime contract and tested
against the package module; it is not an unsealed deployment overlay. All 167
package Python modules remain in the complete implementation fingerprint.

Evidence: [test_runtime_clock.py](../tests/test_runtime_clock.py), including masked
sleep, startup consumption, nested phase expiry, boot/host/provider mismatch,
expired-before-config/spawn, child waits and real pipe-selector expiry; existing
publication sleep and pre-intent/phase viability fixtures remain active.
Policy/report readers keep the inherited clock even after the batch callback is
cleared; their busy waits and chunk audits remain bounded. Expired delivery or
report reads perform no authority mutation.

Primary clock definition: [Apple's clock implementation](https://github.com/apple-oss-distributions/Libc/blob/main/gen/clock_gettime.c)
uses continuous time for the selected Darwin clock; [Apple's continuous-clock API](https://developer.apple.com/documentation/kernel/1646199-mach_continuous_time)
documents its equivalent time interface.

### P2 — Journal native JSON compatibility delivery (R2/R4/R6)

Trigger: a native reservation or finish commits only to the SQL authority while
legacy JSON remains frozen. The previous M1 API had no pending-delivery record,
bounded exporter or acknowledgement boundary for those new events. Original
legacy JSON preservation alone did not satisfy the handover's compatibility
journal requirement.

Policy authority 3 / import 4 adds a transactional versioned delivery queue for
native changes. An explicit owner-held `export_json_page()` accepts finite row,
byte and deadline limits, writes/readbacks the JSON before acknowledging its exact version,
and resumes after a file-write/checkpoint interruption. A late finish cannot be
acknowledged by an older reservation export. Imported original evidence is not
rewritten, charges are never refunded, and the old `attempt_index.json` writer
fence stays closed. Reports expose pending delivery through one indexed frontier.

The mirror is a compatibility projection, not a second admission authority.
An empty delivery checkpoint does not prove a safe legacy rollback. Old writers
require separately reviewed full catch-up/reconciliation and authority handoff;
if unavailable, retain the admission hold and roll forward with the journal.

Evidence: `test_native_json_delivery_is_durable_bounded_and_cannot_enable_old_writers`
in [test_policy_authority.py](../tests/test_policy_authority.py): lag, byte deferral,
file-write interruption, restart, late finish/CAS, exact JSON and retained charge.

### P2 — Unknown quota scope is repair, not positive eligibility (R2/R8)

Trigger: configuration/tasks refer to a source missing from the migrated native
`sources` table. `due()` returned the current time just as it did for a correctly
migrated source with zero charges. Actual reservation already refused that source,
so this was not a provider-dispatch bypass; selection/reporting could nevertheless
claim admissibility and fail later.

Missing scope now raises explicit repair before reporting a due time. It does
not manufacture a zero quota window or change any retained source charge.
The native delivery regression also verifies this negative admission case.

### P2 — Bound report row allocation independently of due-time diversity (R5/R8, R-A)

Trigger: one source/kind/host has many pending tasks with distinct eligibility
timestamps. The native health query grouped by every timestamp and materialized
one Python row per group, repeatedly allocating an active backlog just to report
admission. A 1,024-task reproduction emitted 1,024 groups despite fixed host work.

SQLite now aggregates due/future classes per kind/host, retaining exact weights
and the earliest future instant. The same fixture reports 512 eligible and 512
deferred tasks with one emitted SQL group. Held gates remain applied to both
classes. SQL still visits the relevant active headers under its inherited budget;
this does not claim constant CPU over arbitrary active backlog or replace the
external-volume performance gate.

Evidence: `test_report_groups_pending_times_without_materializing_every_due_timestamp`
in [test_task_headers.py](../tests/test_task_headers.py), plus source-health,
retained-listing and native worker regressions.

## Complete handover and policy coverage

| Contract | Review disposition and evidence |
| --- | --- |
| R1; A1, R-A/R-E | Source/vacancy/host classification, primary-content provenance, TLS/DNS/redirect gates remain unchanged. Existing denial, identity, transport and controller regressions stay in the full package run. Local expiry earns no new provider/completeness claim. |
| R2; A1/A7/A9 | Charges, task/host gates, pacing, Retry-After, probe ceilings and review holds retained. Native migration includes unindexed/missing-finish/disabled-scope charges; missing authority/audit/scope is repair. JSON delivery never reopens old admission. |
| R3; A1/A3/A4 | Joint schema/selective lineage, exact acceptance/backfill, complete listing frames and current observation effects retained. Failed refreshes keep old accepted evidence. Selected projection fixtures compare outputs/rejections; unsupported whole-public-text coverage remains explicit. |
| R4; A4/A6/A7, R-C/R-D | Both pre-intent windows with budget absent/false/true, killed-worker independent acceptance, stale-source restore, destination ownership, exact journals/exports and version-CAS acknowledgement covered. Source/consolidated commits remain separate with explicit recovery. |
| R5; A5, R-B/R-D | Entry and phase continuous clocks supplement paired clocks and the supervisor. SQL/verifier/decompression/read/report checks, useful work preservation, byte/target admission and escrow continuation retained. Kernel I/O cannot be cooperatively interrupted by these checks. |
| R6; A2/A5/A6/A7 | Explicit schema transitions, source/owner receipts, complete 167-module reseal and bootstrap-helper binding. No automatic schema migration/reseal/rollback, quota reset, forced concurrency or production operation. Existing preparation and data-preserving roll-forward boundary remains mandatory. |
| R7 | Only actionable M1 defects or explicit immediate-contract gaps above were corrected. The accepted compression P2/P2/P3 follow-ups and dispatcher relocation remain separate; the withdrawn hardlink finding is not reopened. This review does not replace independent sign-off. |
| R8; A8/A9/A10, R-E | Stable stage/end-to-end episodes, truthful zero-task/unknown eligibility, old-work versus genuine no-op, source/cohort age, controller `[1,1,2]` qualification/downshift and read-only monitor fixtures retained. JSON delivery debt and boot/UTC provenance are visible. Separate test scopes are not summed. |

M0's baseline Markdown/JSON remain byte-identical to `30ea183c`. No files in the
primary checkout or deployed worker were changed by this review. No production
config, data, owner, maintenance marker, schedule, hold or provider request was
changed. The earlier live-path incident and byte-for-byte restoration remain
recorded in [the implementation handoff](runtime-m1-progress.md).

Original production/deployment receipts remain machine-local; this PR contains
portable synthetic fixtures and sanitized provenance. Historical hashes and
incident summaries do not establish current production service.

## Remaining release gates

M1 is a prepared draft candidate. M2 generalizes the active authority/outbox and
storage model; it does not excuse any corrected M1 durability/no-op boundary.
M3 still needs representative cold/warm external-volume 10k/100k/1m history,
throughput/backlog drain/oldest age, large-item fairness, actual fsync/capacity and
full CI/dependency-security plus independent review. Local one-active-job history
fixtures do not establish those results or the 100 GB target.

M4 requires separately approved final reconciliation, owner/fence handoff,
coherent rollback/roll-forward evidence, three successful natural cycles with
actual qualified overlap/downshift and the authorized 24-hour observation.
No observation window, cutover, compression/retirement or relocation was started.
