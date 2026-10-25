# PR #143 in-place production cutover — 25 October 2026

The operator explicitly approved direct production preparation through handoff
verification, preserving maintenance and all originals. Archival migration is
P1 issue #144; fixed-budget activation and retirement remain disabled, with a
20 GiB physical reserve. M0 is preserved as evidence, not a compatible writer
after native authority preparation.

`runtime_containment.prepare(..., production_cutover=True)` accepts only the
exact configured production database, owner, policy and output paths, bound to
its original configuration and unchanged operation-owned maintenance marker.
Default calls retain copy-only refusals. Evidence, charges, catalogue and scoped
holds use the same existing checks in both modes.

`m1_production_cutover.py --operation PRIVATE_DESCRIPTOR` backs up the affected
SQLite databases consistently and preserves mutable control files before calling
the existing native schema, policy import, catalogue, export, accepted-evidence
and containment primitives in place. It automatically continues finite pages
within one explicit preparation allowance (default 3600 seconds). Existing
journals and stage receipts own resume. An invariant error retains the fence;
the tool never dispatches, removes maintenance, installs cron or enables storage
retirement. After verification, an explicit descriptor option journals and installs
the matching configuration while maintenance remains present. No rehearsal database or source head is transplanted.

The policy importer preserves the original JSON index as
`authority_legacy_index.json`. The auxiliary historical catalogue records this
explicit locator and original hash; no charged request is dropped or marked
verified merely because of the locator change. Prior publication contradictions
are rechecked through existing hold APIs. The existing sealed three-generation
index is used only to verify original completed recovery, retaining separate
acceptance/export debt and current task/unit holds.

Focused validation before preparation: 62 passed in 19.73 seconds, including
production/default binding, wrong fence/root/candidate/owner, idempotent
containment, automatic page continuation, invariant failure, domain preservation,
and existing policy/import/export/source-head regressions. Production execution
results must come from the private operation journal, not these fixture results.

## Boot-first correction and retained production checkpoints

The 25 October execution stopped at catalogue ordinal 832 because a historical
September process group number had been reused after the October reboot. Kernel
boot identity now precedes PID/PGID probes. Prior-boot references receive an
explicit boot-domain proof without inspecting current unrelated processes;
same-boot leaderless groups and live/unknown identities still refuse admission.

The operator explicitly authorized continuing the `ee6e8d9d` production
checkpoint. The resumed descriptor pins the exact original descriptor and
progress bytes, same operation root/epoch/inputs, completed backup/policy/schema
stages, ordinal 832, and 71,888 charge / 72,357 evidence rows. Only the reviewed
release/configuration bindings change. Existing dirty-contract revalidation
updates the runtime seal without rerunning backups, policy import, schema
preparation, or the first 832 historical catalogue records. This is a specific
operator handoff, not automatic compatibility inference on future code edits.

Regression checks: 26 passed in 0.34 seconds, including a prior-boot reused
leaderless PGID that must perform no PID/PGID probes, and the same-boot case that
must retain its refusal. Production dispatch remains conditional on completing
the existing handoff checks and the operator-owned maintenance removal.


### Read-only sealing transition correction

The resumed preparation reached catalogue ordinal 6,464/6,471. Three legacy policy
controls had unchanged device, inode, size, mtime and SHA-256 against the retained
Step 3 originals; native policy migration removed their write permissions, changing
ctime. The explicit production operation now pins that diagnostic/baseline receipt.
Only named native immutable controls with the exact baseline inode, size, mtime,
SHA-256 and reviewed read-only mode may cross that transition. Catalogue proofs
record it separately; historical dependency hashes remain NULL and history held.
The same content/metadata check runs before preparation and at final handoff.

The checkpoints for 51 backups, worker schema, 71,888 charges, 72,357 evidence
records and 6,464 catalogue entries are retained. Only derived dirty-work contract
bindings are renewed through the existing paged primitive after this explicit seal.

The remaining handoff audit distinguishes mutable SQLite files from sealed files:
worker preservation compares ordered retained domain rows, not physical DB/WAL
bytes; policy authority checks its committed revision/transaction/schema with a
stable database inode; destination export certificates use native store revisions.
Exports record identities after read-only sealing. Containment evidence is pinned
after collection and is not chmodded later. Those guards require no generic ctime,
mode, WAL or header exemption. The maintenance marker remains until the native
handoff verifies; Step 6 then uses the actual owner lock to remove only this
operation's marker and executes one normally bounded production invocation.
