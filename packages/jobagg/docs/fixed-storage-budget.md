# Fixed active storage budget

This is an opt-in implementation for the deterministic dispatcher. It has not
been activated in production. Existing configurations retain their existing
behavior; enabling the policy requires an explicit, reviewed configuration and
a complete inventory of the actual deployed paths.

The fixed active total is **100,000,000,000 bytes** (decimal GB):

| Category | Ceiling | Contents |
| --- | ---: | --- |
| `essential` | 40,000,000,000 | Authoritative jobs, full descriptions, closed history, application records/documents and unique recovery data |
| `publication` | 15,000,000,000 | Live consumer exports, current and one usable previous publication, shared content-addressed objects |
| `evidence` | 10,000,000,000 | Explicitly selected, bounded incident/regression evidence |
| `diagnostics` | 5,000,000,000 | Control receipts, bounded logs and the durable budget ledger |
| `work` | 30,000,000,000 | Source/runtime/build/test support, scratch, projections, staging and temporary peak allocation |

The work allowance is inside the total. Categories cannot borrow from one
another. Every relevant filesystem must also retain at least **20 GiB** of
physical free space after the admitted growth. That floor is independent of
the active total. Essential data that no longer fits causes a storage deferral
and an operator decision; the implementation does not delete it or raise limits.

## Scope and enforcement

`storage_budget.roots` registers existing absolute directories, their category
and permitted writers. Register all active copies, archives, source checkouts,
runtimes, test/build outputs and scratch paths, including paths on other
volumes. Nested roots use the most specific registration. Inventory charges
the greater of logical and allocated file bytes, directory allocation, and
deduplicates hardlinked inodes. Incomplete, stale or changed inventories and
unregistered paths fail admission.

Mixed output directories can use `category_rules`, each with `root`, `pattern`
and `category`. Rules match immediate child basenames only, also for files not
yet created. For example, register the output root as publication and classify
`*.sqlite3`, `*.sqlite3-wal`, `*.sqlite3-shm` and `*.sqlite3-journal` as essential.
Temporary database names require their own work rules. Conflicting matches
fail closed. An arbitrary default category cannot establish that the full
deployment scope has been registered correctly; activation must review it.

Explicit `offline_backup_paths` must be disjoint from active roots. Summaries
list these paths and state `offline_backups_in_active_total: false`. This is an
explicit exclusion, not a claim that offline bytes disappeared. Any mounted
backup used as active runtime/recovery storage belongs in the active registry.

The supported mode is **cooperative** accounting, not a filesystem quota.
`hard` mode refuses activation because no verified allocation-enforcement
backend covers every writer. Arbitrary external tools, operating-system writes
and unregistered processes are outside cooperative enforcement; an operational
hard-cap claim requires a separately reviewed backend and writer boundary.

## Configuration contract

The machine-specific configuration must provide:

- `storage_budget.enabled: true`, `mode: cooperative`, the complete `roots`,
  one controller/worker/publisher in `writer_roles`, and an absolute
  `ledger_path` in diagnostics. Total/category constants cannot be overridden.
- `storage_cycle`: positive finite `ingress_cap_bytes`, `response_cap_bytes`,
  `accepted_cap_bytes`, `database_growth_cap_bytes`, `control_cap_bytes`, an
  existing registered work `scratch_dir`, complete `support_paths`, and one
  unambiguous `output_dir`. The response cap is at most 50 MiB and cannot exceed
  ingress. Control capacity must cover the ledger's 786,432-byte reserve.
- `publisher_write_limits` and `maintenance_growth`, each explicitly covering
  all five categories with finite values. Publisher limits must exactly match
  `publication_policy.write_limits`. Set these from bounded representative
  measurements; there is no unlimited fallback.
- Absolute Python commands for exactly `-m jobagg.remediation_worker` and
  `-m jobagg.publish_worker`. Only distinct `-B`/`-u` interpreter flags are
  accepted. Alternative modules/scripts fail before inventory or fetching.
- `sealed_publication_snapshots: true` and
  `publication_snapshot_mode: projection`; a publication policy with
  `schema_version: 1`, `export_storage: record_chunks_v1`, an explicit
  `policy_epoch` and finite `max_history_bytes` within essential capacity.
- Enabled `history_policy` with the same epoch, explicit activation timestamp
  and allowed roots; enabled `storage_retention`, `keep_completed: 2` and
  `max_groups` between 3 and 8. All three maintenance stages share one deadline
  and one group budget, with capacity reserved for each stage.
- If controller stdout is a regular file, an existing diagnostics
  `controller_log_path` matching that exact descriptor, opened for append.
  Unadmitted failures do not append unchecked output to that file.

The existing owner lock serializes inventory and cycle creation. The scope hash
binds the exact dispatcher configuration, commands, registered roots and finite
limits. Children receive only their role's capability. Unknown/stale cycles,
changed configurations and unfinished operations cannot create a fresh escrow.
Temporary paths and bytecode behavior are set for admitted children.

## Cycle, recovery and retention

Before fetching, admission includes bounded ingress/decoded responses, accepted
artifacts, database/WAL growth, controller logs, projection construction,
publisher writes and temporary coexistence. Actual cooperating writers charge
their operations before allocation. Interrupted reservations retain their full
uncertain charge; restart cannot obtain an optimistic refund.

After a successful worker, immutable exit/report/database proof seals accepted
work under the original cycle. A later projection/publication capacity hold
preserves that batch without refetching. Resume validates the original controls,
database writer fence and absent former processes. An uncertain child outcome
requires explicit review; an acceptance file alone does not prove success.
Publication intents retain the existing explicit recovery boundary. A published
terminal receipt can reconcile a crash before final ledger closure without
replaying either process. A deferred publisher can close its cycle only after
exact request, intent, acceptance, result and process evidence proves that no
generation started. Consumed publisher bytes stay charged and accepted data
stays intact; later cycles can continue without replaying the old worker.

The worker enforces wire and decoded response bounds, including robots and
redirects. A bounded retained prefix is reported as partial; storage holds do
not become provider failures or automatic retries. Unsupported browser buffering
defers before claim. Large database working envelopes can conservatively defer
before HTTP. Offline captured-response processing and operator retry grants are
separate, currently unsupported workflows.

Content-addressed publication chunks avoid repeated full retained JSON/CSV
copies while preserving the existing live JSON/CSV consumer files. Semantic
history preserves exact before/after jobs, useful text, first-seen identity and
document associations/blobs. Activated database writers require the bound
capability; unsupported full consolidation/rebuild fails before temporary work.
It does not silently replace the bounded incremental publisher.

Maintenance first retires eligible external projections, then terminal history
and shared objects, then eligible old copies. Current/previous, selected evidence
and exact unresolved dependencies remain protected. An unrelated terminal group
can retire while another publication remains unresolved. Activated fixed-budget
retention has no daily or monthly full-copy tier. Deployments without that
activation retain the legacy latest-per-day 30-day and latest-per-month 12-month
snapshot protection. Durable, identity-bound intents precede unlink;
receipts allow interrupted cleanup to resume. Unknown legacy layouts remain
protected; legacy retirement requires exact verified backup evidence. See the
[history contract](semantic-history.md) and
[retirement contract](terminal-history-lifecycle.md) for those authority schemas.

## Validation and activation limits

Synthetic tests cover category/total/physical limits, partial captures,
interruption, accepted-work resume, changed bindings, writer fencing, canonical
retired readers, selected/current/previous protection, shared-object GC and
bounded maintenance. The twelve-cycle fixture simulates 15-minute publications
and samples actual inventory while temporary exports coexist. Nine publications
and three unchanged outcomes stabilized at 16 shared objects; exact old job text
and documents remained readable after eligible payload retirement.

Those fixture measurements are not production capacity validation. Live
activation remains a separate reviewed task: preserve a verified backup,
inventory the real scope, establish protected/eligible sets, execute approved
cleanup, measure steady and peak usage, and reseal the complete implementation
fingerprint/configuration. Existing over-budget data or a failed physical floor
does not authorize automatic migration cleanup. This change does not restore
cron, run providers, edit production databases, merge or deploy.
