# PR #143 guarded historical-original removal in isolated copies

The user explicitly authorized this increment on 24 October 2026: guarded removal
of verified eligible originals **in isolated copies only**, preserving M0 and the
production maintenance fence, with no production activation. That authorization
supersedes the earlier code-preparation approval gap for this isolated operation;
it does not authorize production retirement.

`isolated_archive_removal` is separate from the additive installer. It accepts a
pinned additive review, completed verified grants, an independently pinned copy
manifest and an explicit finite removal selection. There is no production mode or
startup hook. The additive page executor still retains every original.

## Scope, separation and eligibility

The copy manifest binds a canonical copy root and retained-source root by device
and inode, the original configuration and production fence, the copy configuration,
and each source/copy path, original hash and physical identity. Every selected
source must still exist, retain its pinned identity and match its original logical
hash. Both source and copy are independently checked. A copy cannot be a hardlink;
its physical identity must differ from the retained source and its link count must
be one. Symlinks and path aliases are rejected.

The declared production root must contain the original state, publication, output,
owner, maintenance and configured worker-database paths. The copy root must be
separate from that entire root and the original configuration. Its own state,
publication/output paths, lock, marker, controls, removal journal and scratch must
remain inside the copy. The actual copy owner descriptor is checked throughout;
the original production marker is read and never removed. A bounded process
census refuses a running worker, publisher or dispatcher.

The prior protection census is repeated before mutation and on restart. Current
and previous complete generations, unfinished and dispatcher-referenced recovery
generations, additional protected dependencies and directly read producer inputs
stay hot. Every producer must retain its original hash and physical identity.
Snapshot removal additionally reuses the existing derived-snapshot retention
eligibility checks, with bounded pinned acceptance/request/outcome/result controls.
Unresolved snapshots and recovery variants without this reviewed terminal proof
remain hot. A sealed SQLite byte image alone does not prove terminal eligibility.

If a copy-owned native worker database is configured, its authority must validate
and remain idle under the matching owner. Admitted native work prevents removal.
Originals listed in the native control catalogue remain protected: an explicit
offline representation grant does not migrate ordinary catalogue consumers.
This preserves the accepted rehearsal batch and existing historical holds without
resetting their authority or rewriting their dependencies. The bounded operation
can proceed on demonstrably independent historical inputs only.

## Durable removal and conservative accounting

The operation preserves the original 64-artifact/8 GiB review ceilings. Execution
defaults to four items / 2 GiB, with an explicit cursor; completed revisits count
against the page bound. One absolute finite deadline, no greater than 900 seconds,
covers source verification, producer/control checks, reconstruction, parsing,
SQLite scratch restore and journaling. The operation retains 20 GiB physical
reserve plus control headroom on affected paths; restoration reserves its full
logical output as well. Cooperative checks do not interrupt a blocked kernel
filesystem call, so external supervision is still required.

Before unlink, the operation fully reconstructs the grant, uses the bounded strict
parser for plans, and validates SQLite snapshots in private read-only scratch.
An exclusive durable per-item intent records the exact plan and grant. The
existing representation store lock serializes the default manifest update;
its entry must retain the same pinned logical identity and compressed object.
The existing retirement primitive performs the verified unlink and manifest
update. The immutable per-artifact grant remains unchanged and continues to read
the exact original logical bytes. A separate removal receipt precedes completion.

Restart validates prior receipt bindings before extending the page. A lost unlink
or manifest-update receipt leaves an open intent. The next attempt syncs the
original parent directory to make observed absence durable and confirms the
compressed bytes again. It records **zero confirmed removed bytes** for that
uncertain window, explicitly reporting uncredited absent bytes. A durable known
removal receipt is counted once; repeat execution cannot add credit. Reappearing
originals, changed controls, corrupt objects or damaged checkpoints stop the
operation and preserve unrelated items.

Confirmed byte counts describe logical originals removed in the isolated copy.
They do not certify physical space reclaimed on a clone/compressed filesystem,
production storage category compliance, headroom or historical acceptance. Every
result leaves production activation/readiness and storage-admission headroom false.
Retained-source payloads, worker observations, charged attempts, proofs and holds
are not changed by removal.

## Validation and rollout limit

Regressions cover every supported artifact family; compressed-only reads and
SQLite reconstruction; interruption before/after removal and durable receipts;
unlink directory-sync failure and restart; exact-once/unknown credit; hardlinks,
aliases, changed source/copy/fences/producers, production overlap, missing originals,
corrupt archives and checkpoints, unavailable codecs, byte/time/reserve bounds,
idle native authority preservation and admitted-work rejection. The [validation record](pr143-isolated-removal-validation-2026-10-24.md) supplies
actual full-suite results and the all-module seal.

A separate immutable-source component rehearsal uses synthetic copy/source roots
on the SSD, injects interruption, resumes the same plan and checks the real
production marker and preserved M0 separately. It does not copy HDD payloads or
retire any actual production artifact. The final exact-layout admitted-cycle
rehearsal, complete active-root inventory, installed/peak 100 GB capacity, measured
runtime-setting approval, remount re-attestation and production authority/code
handoff remain deployment gates. No successful test waives those gates.

R1/R3: representation preservation adds no provider or whole-text completeness
claim. R2/R4: source, proof, charge, hold and owner/fence boundaries survive.
R5/R6: finite pages, cooperative deadlines, durable recovery and an explicit full
package reseal apply. R7/R8: report this isolated scope and its actual evidence,
including uncredited absence, separately from deployment readiness.

## CI fixture repair carried with this increment

The preceding head's Python CI failed six positive archive cases with
`archive_physical_reserve_unavailable`: 4,073 passed, 18 skipped and six failed.
The hosted runner had less than the intentionally enforced 20 GiB reserve. This
was an unmodeled environment assumption in the synthetic fixtures, rather than
permission to lower the runtime floor or delete unrelated runner data.

The small archive-page fixture now explicitly models reviewed capacity. Both the
additive and removal low-reserve negative cases still override that model and
require refusal. The separate immutable-source SSD rehearsal declines the
fixture's capacity/process models and exercises the actual filesystem reserve and
bounded process census. The runtime floor, category ceiling and production
readiness policy are unchanged. A new PR head needs its own Linux CI result.
