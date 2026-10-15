# Local semantic history

This opt-in authority preserves exact useful job text, first-seen identity,
document bytes and association versions in the same SQLite transaction as a
change. Immutable content-addressed tree nodes share unchanged long strings and
nested values. Known observation timestamps in named proof envelopes are
coalesced; unknown provenance remains exact. Repeated preservation does not
copy a whole poll or dataset. Observation spans retain bounded first/last/count
fields per episode; they describe preservation observations, not an independent
provider event chronology.

`JobDatabase.initialize()` creates disabled history tables. Activation is an
explicit maintenance operation after every runtime table exists:

```python
activate_semantic_history(conn, policy_epoch=epoch,
                          max_history_bytes=allowance,
                          ledger_path=ledger_path, scope_hash=scope_hash)
with admitted_semantic_write(conn, policy_epoch=epoch, run_id=cycle_id,
                             lease=budget_lease, authorizer=optional_write_guard):
    # Domain writes and semantic capture share this caller-owned transaction.
    ...
```

Activation does not create a budget grant. Admission validates the real durable
`BudgetLedger`, exact cycle/scope/database path and allowed worker/publication
phase. Job and document triggers preserve old and new versions, including
closure and direct SQL updates. Before INSERT or UPDATE, they also preserve legacy rows
that conflict on primary keys, rowids or ordinary unique indexes, including
index collations. This covers SQLite REPLACE even with recursive triggers off;
activation does not need to copy all legacy rows. Expression/partial unique
indexes are explicitly unsupported and prevent activation because their
replacement conflict sets are not proven. Unknown connections lack the UDF grant and fail
closed. The composed SQLite authorizer rejects runtime schema changes and
rechecks COMMIT/RELEASE; each transaction's first guarded mutation expires
cached transaction statements so a reused SQL boundary cannot skip that check.
Caller commit/rollback ownership is unchanged. Repeated activation with changed
bindings is rejected. Runtime initialization/migration is not an activation API.

History objects, subjects, versions and coverage records reject replacement,
including rowid conflicts, update and deletion. Document blobs referenced by
legacy current associations or semantic versions reject byte replacement and
removal before the first history capture. A monotonically increasing mutation revision and complete trigger/table
validation back `semantic_database_binding(path)`. Legacy databases report
`writer_fenced=False`. This is a cooperative writer fence; privileged external
DDL/file replacement is a maintenance boundary and must remain excluded by the
shared owner lock and deployment controls.

`preserve_job`, `preserve_document_association`, `preserve_beforeimage`,
`preserve_listing_frame` and `preserve_publication_plan` never commit. They retain
exact prior text and associations independently of the current jobs table.
`record_semantic_coverage` binds final artifact hashes to those retained versions:
job/listing beforeimages are checked record by record; plan generation,
destination, old/incoming rows, listing frames, document text/metadata and blobs
must agree. Empty beforeimages may bind final versions. JSON artifact validation
is capped at 16 MiB and defers larger artifacts. Blob checks stream bounded
chunks. A separate read-only connection in `validate_semantic_coverage` requires
committed immutable coverage, correct epoch/subject/allowed database roots and
valid object/blob closure. Coverage alone never authorizes retirement.

Consolidation unions source history and the prior consolidated authority.
Previously retained jobs, exact document variants/blobs, first-seen values and
prior-only ordinary tables survive rebuilding. Canonical source job updates may
replace current fields while the old version survives. Conflicting user override
or unknown operational state, incompatible schemas, corrupt immutable records
and invalid identity abort before replacing the old database. Activated full rebuilds are deliberately unavailable, even with a valid
publication lease: the legacy rebuild writer has no complete physical envelope
covering temporary SQLite, rollback journals, VACUUM and all four exports. It
raises `StorageBudgetDeferred` before creating its temporary database or
changing existing exports. Use bounded incremental publication. Re-enabling
full rebuilds requires a reserved and constrained envelope plus coherent
replacement/export recovery; a lease or page cap alone is insufficient. The
existing shared owner lock remains required for legacy rebuilds.

The semantic allowance is a logical backstop including object/row overhead,
coverage payloads and referenced blob bytes. It is not an OS quota or a SQLite
WAL bound. Runtime workers/publishers must additionally reserve and enforce their
finite transaction/file envelopes against the global storage ledger. Capacity
exhaustion raises `SemanticCapacityError`; unique history is not silently dropped.
Unknown provenance may consume additional history capacity and trigger deferral.

Focused fixtures cover exact whitespace variants, unchanged observations,
reversions, corrected extraction sharing one binary, caller rollback, committed
coverage, false document coverage, direct/unknown writers, phase revocation,
cached COMMIT/RELEASE, legacy replacement on primary/rowid/unique keys,
multiple conflicting prior rows, legacy blob protection, immutable replacement
attacks, capacity deferral with the old row unchanged, prior-only
consolidated state and activated-rebuild rejection with exhausted work capacity. No production activation or
provider validation is implied by those fixtures.
