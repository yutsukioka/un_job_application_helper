# PR #143 bounded archival reader and migration proposal

Prepared 24 October 2026, Africa/Nairobi, following the user's instruction to
implement deployment steps 1–7. That instruction authorizes a conditional
production cutover after the gates pass. The standing instruction to consult on
substantial code changes remains applicable. The archival implementation below
is the specific substantial increment raised for consultation; it has not been
implemented or activated by this proposal.

The [100 GB review](pr143-storage-100gb-review-2026-10-23.md) and
[engineering policy](change-and-review-policy.md) remain controlling. No caller
assertion that readers support manifests is evidence that they actually do.

## Bounded implementation scope

Use the existing `storage_compression` representation format, explicit Zstandard
level 3 without a dictionary, original logical SHA-256 and byte length. Preserve
legacy-only, dual-representation and compressed-only reads. Do not add a global
`Path` replacement, symlinks masquerading as originals, a new admission authority,
a blanket hash waiver, or an implicit startup migration. The exploratory fixed
1 MiB chunk measurement is not a newly approved representation format.

1. Add an explicit bounded historical-artifact reader over `stream_artifact`.
   Require canonical root and logical path, expected original hash/length, a
   reviewed representation-manifest binding, byte/retained-memory bounds and the
   caller's existing absolute deadline. Preserve corruption failures when an
   original exists; only a missing original with an exact representation grant
   can resolve through compressed storage. Do not replace original producer
   identities with a hash computed from today's unverified file.
2. Adapt `historical_plans` and `historical_occurrences` to that reader without
   changing JSON selectors, strict parsing, original logical paths or producer
   hashes. Hash every logical byte even when projecting a small subset. No
   partial stream or expired parse becomes a complete occurrence proof.
3. Add an owner-reviewed representation attestation for historical catalogue
   dependencies. Preserve original physical identity as provenance, original
   proof bytes, logical identity and disposition. A new physical representation
   must bind its own verified object identity and the old logical bytes. A missing
   file without that exact attestation stays held. Recompute the current-state
   containment review after any explicit dependency migration; ordinary startup
   cannot bless a changed catalogue.
4. Keep live worker databases/WAL/SHM, current captures/documents, all 469 mapped
   HDD policy-evidence files, current public exports and current/previous or
   unresolved publication inputs hot. Historical SQLite images require an exact
   original seal and a bounded verified scratch restore before SQLite opens them.
   Unknown snapshots stay unknown/hot; byte survival cannot certify consistency.
5. Extend only applicable historical generation/recovery readers, preserving the
   same-generation request, intent, plan, destination and export bindings. Exact
   compressed reconstruction grants neither a historical worker exit nor a
   publication/retirement certificate. Never edit the currently accepted rehearsal
   configuration or source head to introduce the migration beneath its batch.
6. Prepare an operator page executor with an immutable eligible inventory, actual
   owner, unchanged operation-owned maintenance marker, expected source bytes and
   identity, finite byte/time/physical-reserve bounds, durable intent and restart
   checkpoint. Install and verify complete reconstructed bytes before an approved
   representation handoff. Preserve protected inputs and unresolved evidence.
   Keep the HDD backup untouched; this operation requires no HDD-to-SSD payload
   copy. No semantic expiration or blanket legacy deletion is included.

## Capacity and rollback gates

The complete measured artifact set, not five samples, must establish distinct
object sizes and actual category allocation. Include rounded filesystem blocks,
protected hot copies, manifests/journals, current and usable previous publication,
source/runtime support, active HDD references, staging, restore scratch and the
next-write envelope. The 40/15/10/5/30 GB category limits cannot borrow. Preserve
20 GiB physical reserve on every relevant filesystem. An incomplete inventory,
an unmeasured object or an unknown eligibility edge cannot become free capacity.

A backup may be excluded only when explicitly offline and not serving normal
runtime/recovery reads. Do not merely relabel a mounted active resolver root as
an offline backup. Review which old preparation trees can become inactive;
retain every unique receipt and dependency. The measured native copy remains
preserved while it owns accepted work.

Before switching readers, rehearse exact reconstruction and interrupted page
recovery. Before switching writers, rehearse a compatible authority/code rollback
or preserve the stopped, fenced roll-forward path. M0 remains byte-for-byte
preserved, but its old file-based writer cannot run against a native authority or
stale compatibility exports without a reviewed catch-up/handoff. Never restore an
old database over legitimate later attempts, observations or publications.

## Required regressions and acceptance

Cover old-only, dual and compressed-only artifacts; original Unicode/JSON bytes
and null/absent distinctions; wrong logical/object hash and size; missing codec;
path alias or replaced manifest/object; oversized/truncated/decompression input;
expiry during staging/read/parse/restore; same-intent crash recovery and exact
retirement credit. Retain existing uncertain-manifest and interrupted-retirement
regressions. Exercise historical occurrence selection, protected recovery,
interrupted publication/readback and compatible rollback with compressed-only
eligible inputs. Compare acceptance, output bytes and rejection reasons.

Run the complete package/dispatcher checks, review R1–R8, explicitly reseal every
package Python module after implementation, stage a new immutable release, then
repeat the admitted-cycle rehearsal on that exact layout. Compression tests and
capacity measurements alone do not authorize deployment. Production readiness
remains false until these results exist.
