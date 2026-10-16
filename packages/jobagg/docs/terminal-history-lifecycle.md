# Terminal publication history lifecycle

Activation is separate from deployment. The controller must register every active storage root and enforce the 100,000,000,000-byte total, its 40/15/10/5 GB category ceilings, and 30 GB operating reserve. These are ceilings, not allocation targets. Receipts, local backup verification proofs, semantic history, temporary files and audit/support artifacts remain charged active bytes. Deletion grants no capacity until a fresh complete inventory.

`terminal_history.maintain_history(root, policy=..., execute=False, owner_fd=None, shared_lock=None, deadline_at=None, max_groups=1, budget=None)` performs bounded maintenance. Execution requires the actual exclusive shared owner and an admitted `BudgetLedger`; the ledger classifies each receipt's real path before reserving and charging its temporary/control bytes. The result includes `protected`, `selected`, `deferred` and `deleted_bytes`. It never runs a worker, publisher, recovery or provider request.

The controller invokes this path before fetching and after verified publication. It passes the returned `protected` closure and the same `budget` to `storage_retention.prune_completed`, with `fixed_budget=True` only for admitted fixed-budget maintenance. Without that explicit activation, retention keeps its legacy latest-per-day 30-day and latest-per-month 12-month snapshot checkpoints. Optional incident/regression evidence requires explicit `selected_runs` or `selected_generations` identifiers; their local files and deduplicated referenced shared objects count against both the configured evidence limit and the 10 GB ceiling. The whole-root budget remains authoritative for all other selected evidence and temporary growth.

## Operational protection

The live publication gate must be complete. The actual current generation and one preceding usable generation remain online: exact plans, export evidence, referenced row beforeimages and document blobs are checked. Unknown/incomplete generations, active/unresolved runs and their generation references stay protected. A retired run discharges only its own old historical audit edge. No-write and recovered runs retain their historical generation dependencies until their own terminal receipt completes.

An unresolved run A does not prevent maintenance of a proven independent completed B. A pending retirement keeps its exact subject out of other cleanup readers; only its own sealed intent can resume absent payloads. Missing or contradictory proof never becomes a fabricated publication success. Historical missing recovered generation results are explicit deficits, and the opt-in classification of other authentic recovery controls alone does not grant deletion authority.

## Two distinct authorities

Future generations use `future_window_expiration`. They must be created after `activated_at`, belong to `policy_epoch`, and carry `semantic-coverage.json`. The validator opens only allowed active local databases read-only and validates committed coverage, exact object/document bytes and subject/epoch bindings. Every expiring plan, row beforeimage and blob must have exact path/hash/length coverage. The authority is useful semantic history; obsolete exact serialized plan/export bytes may expire. It never requests or opens an offline backup. If unique required history fills the cap, writes defer; history is not silently dropped or allowed to grow past the cap.

Legacy migration uses `legacy_backup_retirement` only for explicit `policy.legacy_retirements` entries:

```json
{
  "subject_kind": "generation",
  "subject_id": "0123456789abcdef0123456789abcdef",
  "backup_attestation": {"path": "proofs/verified-backup.json", "sha256": "<exact SHA-256>"}
}
```

The local attestation has `schema_version: 1`, `status: "verified"`, `verification_kind: "source_destination_full_sha256"`, a nonempty `backup_id`, absolute `backup_root`, exact `manifest_sha256`, and a `files` mapping keyed by absolute original source path. Every expiring file and shared export object must have equal `source_sha256`/`destination_sha256`, exact `size_bytes`, and a safe `destination_relative_path`. This document describes the consumer contract; it does not declare an unverified backup complete. The verified backup workflow must produce and bind this proof before any migration. Routine maintenance reads the local proof only; the offline copy is not a running-service dependency.

## Crash and shared-object behavior

`terminal-history-retirement.intent.json` is sealed before any unlink. It binds immutable controls, truthful terminal outcome/deficits, selected policy authority, payload path/hash/length/stat identity, and exact shared-object inventory. Completion is a separate immutable `terminal-history-retirement.json` bound to the intent hash. A restart validates remaining sources and unchanged controls, resumes only that intent, and writes completion only after all declared payloads are absent. Reappearing or unclassified payload prevents terminal classification. Completed availability is explicitly `backup_only` for legacy migration or `expired_unavailable` for future generated bytes.

Shared record-chunk objects are collected in a separate bounded pass. Every surviving sidecar, including nested verified rollback sidecars and unresolved/selected generations, remains a reachability root. GC considers only objects in a verified retired generation's inventory, checks full bytes, seals an exact GC intent, and then unlinks unreferenced objects. Unknown orphan objects are retained. A shared object already removed by another completed GC needs that exact prior receipt; unexplained absence fails closed. Recreated content-addressed objects are permitted after a completed collection and are protected by their new sidecar references.

The standalone cold/export readers recognize verified terminal receipts before attempting obsolete plan reads. They skip pending subjects without globally blocking unrelated maintenance. Legacy unknown artifacts and unsupported cold/extra payload layouts remain protected and require a separately reviewed migration; this implementation does not claim that every historical group is already eligible or that the current production dataset fits the cap.

## Validation scope

Synthetic tests cover exact backup coverage, missing shared-object proof, current/previous protection, independent cleanup with an unresolved run, interrupted payload and object deletion, changed GC membership, unexplained object absence, overlap with another completed GC, deadlines, quota requirements and changed semantic authority. A real three-publication integration test exercises committed SQLite semantic coverage, oldest-generation expiration, current/previous/shared-object survival and semantic/document readback. No provider or production execution is part of these tests.

## Accepted projections in registered work storage

Activated projections may live at the configured `scratch_dir/<run_id>/publication_snapshot.sqlite3`. Canonical classification accepts that direct metadata shape as an outcome fact; it grants no path-deletion authority. `retire_published_projections(root, scratch_dir=..., budget=..., owner_fd=..., shared_lock=..., deadline_at=..., max_groups=...)` requires the exact configured namespace and a currently registered work-category path. It preserves current/previous, selected and unresolved dependencies, verifies canonical request/intent/outcome/result controls and the complete source hash/stat identity, rejects filesystem aliases, and seals separate `projection-retirement.intent.json`/`projection-retirement.json` records in the originating run before/after unlink. No SQLite content is modified. Older cycle IDs are bound by their original run directory and snapshot request; the current controller ledger only authorizes this maintenance write.

The batch result's `selected` length counts completed retirement groups; deferred/already-retired subjects do not count. The controller shares one absolute deadline and remaining group allowance across external projections, whole-generation history and duplicate-copy pruning. A one-group allowance cannot maintain every continuously produced artifact class on every tick; configuration must provide enough bounded groups or rotate classes. Receipt growth and both current/previous projections remain part of the active budget.
