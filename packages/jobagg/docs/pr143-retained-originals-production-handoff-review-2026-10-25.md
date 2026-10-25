# Retained-originals production handoff review — 25 October 2026

The operator authorized deployment with the complete retained tree above the
100 GB target and archival migration deferred, preserving all originals and the
20 GiB physical reserve. This supersedes the storage prerequisite in the
[previous preflight](pr143-authorized-production-preflight-2026-10-24.md).
It does not require changing the fixed-budget constants: that opt-in feature and
retirement remain disabled, while the existing physical storage guard stays active.

The exact all-green PR head is `81f4681d2398e6a74403ba48de931f268df84823`.
Its 180-module runtime seal remains
`f9b8ecbce83be2ef854ef8fc4afd13646f01b9a300129658cef1870bb65a13d1`.
A new immutable source and independent current-volume rehearsal snapshot are
prepared. The prior accepted rehearsal batch and M0 remain unchanged.

## Demonstrated authority handoff gap

The production worker database has no native publication-work, accepted-evidence,
task-header, historical-catalogue or runtime-authority tables. Existing native
bootstrap/containment evidence belongs to the earlier isolated copy. It must not
be transplanted as a production activation receipt.

`runtime_containment.prepare` explicitly requires a staged snapshot whose worker
database differs from the original production database, whose paths are disjoint
from production roots and whose policy authority is inside that copy. These are
valuable isolation guards. The ordinary dispatcher requires the native historical
catalogue and current-state containment before unrelated new work can proceed
around retained historical debt. There is no reviewed production counterpart to
this preparation path. Waiving storage migration cannot make a copy-only grant
valid for production.

## Proposed substantial increment for consultation

Add a separate explicit production bootstrap/containment procedure; preserve the
copy-only API's existing refusals. Bind it to an independently hashed operator
handoff review, current source path/identity and logical data inventory, original
and candidate configuration hashes, actual owner and unchanged operation-owned
maintenance marker, complete writer/control inventory, native policy revision and
full publication boundary. Verify current volume identity and every applicable
physical/content binding before publishing a new authority head.

Use existing paged schema, policy-import, evidence-backfill, historical catalogue
and destination-export primitives. Preserve source rows and useful evidence,
provider charges, old controls, unresolved accepted work and exact historical
holds. Reuse historical review evidence only after rechecking its original
producer/current-state bindings; copy no old active-run/restore head and
manufacture no missing exit. Keep missing/ambiguous proofs held.

Journal every finite preparation page and authority/configuration handoff. No
provider request occurs during bootstrap. Leave maintenance in place until all
bootstrap, pending-work reconciliation, measured settings and fresh-cycle/recovery
checks pass. An uncertain commit remains fenced and resumes its original journal.
Before activation, rollback selects the coherent retained release/configuration;
after native activation, preserve new data and charges and use a compatible
reader/runtime or fenced roll-forward, never a stale M0 writer.

Required regressions cover correct production binding, original copy-only refusal,
wrong root/configuration/owner/volume, changed data or controls, missing policy
charges, a stale publication certificate, pending or held work, interrupted pages
and uncertain source-head commits. Compare retained domain rows, policy counts and
hold dispositions before/after. Explicitly reseal all package modules, run focused
and complete runtime/dispatcher checks, and repeat the isolated rehearsal using
the final immutable code/configuration before applying the production procedure.

This is a consultation proposal, not an implemented migration or activation
receipt. Production stays fenced while this substantial authority operation is
prepared and reviewed. Archival migration and original removal are outside it.

## Independent preparation completed for consultation

The new SSD-only snapshot verified all 72,362 copied files against their source
hashes, totaling 10,815,879,338 logical bytes, in 101.934 seconds. It preserves
original canonical historical locators and copied policy data; it copies no HDD
payload. Its current-volume owner identity is independent of the old copy's
receipt. M0, the production marker and the earlier accepted batch are unchanged.

The actual immutable configuration loader accepts the retained-originals candidate
for all 49 enabled sources, with retirement and fixed-budget activation disabled.
Its runtime dependency contract is sealed. Remaining-time checks admit the
measured projection/publication floors when sufficient time remains and reject
the deliberately late case, retaining accepted work. These checks are configuration
and clock evidence, not a complete admitted cycle or approval of untested settings.

Under an OS sandbox denying network and production writes, the new copy completed
native worker schema/dirty bootstrap and prepared all 50 destination fences.
Worker integrity returned `ok`; all four checked domain counts stayed unchanged:
23,438 jobs, 9,500 tasks, 67,017 attempts and 7,265 observations. The first
600-second allowance ended during policy replay. Its original failure report,
import journal and completed pages are retained; continuation uses the same copy
and authority-import journal. Policy completion, full export certification and
fresh-cycle results must be reported from their own receipts, not inferred here.

The production counterpart above remains a substantial-change consultation. No
production authority head, source database, live configuration or schedule has
been changed by these preparations.

## Completed component checks at 09:23 EAT

The same policy journal completed through a separate policy-only continuation in
126.695 seconds after the two preserved 600-second preparation expiries. The
copied native authority retains all 71,888 charges, verifies 72,357 evidence
records and remains at revision zero. The 469 relocated HDD evidence files were
verified in place against their original hashes; no HDD payload was copied.
This receipt combines no invented successful worker exit or aggregate rehearsal
success with the earlier timeout reports.

A separate complete copied publication bootstrap certified all 50 destination
stores and all 200 exports, totaling 3,609,807,203 bytes under the 4 GiB export
bound, in 289.043 seconds within its 600-second preparation allowance. These are
current copied export bindings, not historical acceptance, provider coverage or
production cutover receipts.

The immutable supervised dispatcher then stopped at the new copy's maintenance
marker with `maintenance_paused`, exit 75, in 0.094 seconds. The copied worker
database family and both markers were unchanged. No worker or publisher was
dispatched by that entry check.

All these operations used the network/production-write-denying sandbox and a
copy-owned temporary directory. The source runtime authority is not activated in
this new copy; complete native admitted-cycle/watchdog/recovery/rollback validation
still remains. The existing accepted native rehearsal batch is untouched. The
production-bound authority procedure remains awaiting the operator's substantial
code-change consultation decision. Production remains fenced.
