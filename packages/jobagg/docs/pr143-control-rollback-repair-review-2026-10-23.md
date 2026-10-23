# PR #143: copied pre-admission rollback repair proposal

Observed 23 October 2026, 22:21 EAT (Africa/Nairobi). **Proposal only.** No native
head has been reset, no uncertain database restored and no production activation
performed. The copied and real production maintenance markers remain present.

## Reproduction and boundary

The complete current-state containment review passed on the existing isolated
native copy: 6,471 controls reviewed, 5,483 unresolved controls retained as explicit
historical holds, all 7,265 current detail observations crosschecked, all 67,017
worker charges present in the copied 71,888-row policy authority, current listing /
document proof gaps scoped, and the complete copied export boundary verified.

The first admission rehearsal discovered a nested helper resolution defect;
the dispatcher treated `pipelines.worker_policy` as a top-level filename. The fix
loads only the nested helper within the selected release and rejects a foreign
symlink. No worker/publisher was dispatched by that failed attempt.

The corrected attempt failed during the two-statement transition from idle to
publication-only accepted work. SQLite needed a temporary statement journal for
the **94,896-byte control row**, but the rehearsal OS sandbox allowed writes only
inside the isolated roots. A tiny synthetic WAL database reproduced the exact
failure: `SQLITE_IOERR_GETTEMPPATH` (6410). The same reproduction passes with
`SQLITE_TMPDIR` and `TMPDIR` set to a copy-owned directory. Disk capacity was not
exhausted (616 GiB available on the external SSD, 221 GiB on the internal volume).
No additional production write access is needed to correct the sandbox setup.

The failed SQL transaction rolled back. The entire persisted native state row
matches its immutable pre-attempt preparation receipt, including idle phase,
null run ID, revision 48,664, original transaction token, publication cursor,
bootstrap and schema bindings. The independent source head had conservatively
advanced its transaction token before SQL committed. It now rejects admission as
`runtime_source_restore_or_uncertain_transaction_requires_repair`. That refusal
is expected. Existing code provides no explicit source-head repair operation.
Do not delete/rewrite that head, restore a prior database or create another epoch
to bypass this state. The mismatch is not permission to certify unrelated history.

## Proposed bounded repair increment (requires consultation)

This would add an explicit **isolated-copy-only**, owner-held operation for this
verified control-only rollback, together with regression fixtures. Ordinary
startup would retain the current refusal. It would not be a general stale-copy
adoption or production restore tool.

The read-only preview would bind the exact before-attempt preparation receipt,
failed rehearsal receipt, current implementation/configuration, canonical copy
root and database identity, current complete SQL state, actual owner identity,
real production fence and copy fence, independent attempted head, complete export
boundary and bounded database integrity check. It must establish that the failed
operation was pre-admission control work, no run or publication intent became
active, no accepted marker was created, and the entire current state equals the
previously certified idle state. Unknown writer, changed revision/state, missing
before-proof, wrong database/epoch, changed schema/fence, domain mutation, live
process, partial commit or unresolved generation must refuse repair.

After review, a durable exclusive intent would preserve both original tokens,
all evidence hashes and the exact database/fence binding. The mutation would
**complete the attempted token handoff in the SQL row**, leaving the existing
ahead source head intact. It would not rewind or automatically replace the head.
An authorizer would permit only the exact compare-and-set update of
`jobagg_runtime_state.source_transaction_id`; no jobs, attempts, charges, original
control dispositions or acceptance/publication proofs could change. Recheck the
owner and markers at commit and read back the complete state afterward. Retain
an immutable completion receipt and handle interruption before/after this one
commit idempotently using the same intent.

Required regressions: known idle control rollback; normal unknown pending state
still refused; stale/aliased/restored database; changed revision, state, schema or
owner; missing/changed before-proof; active worker/publication; token/head change;
nonmatching marker; forbidden domain writes; and interruption on either side of
the one permitted commit. Test the copied temporary-directory boundary separately.

Only after that review and explicit repair should the same isolated state resume
its admitted publication-only batch, receive an injected pre-intent interruption,
and recover the same run. The real maintenance fence remains present throughout.
The copy marker must be handed off under the actual owner and restored at the end.
No fresh-provider or changed-generation claim follows from a no-change publication
batch. The full 100 GB storage migration and reader compatibility remain separate
production readiness gates.
