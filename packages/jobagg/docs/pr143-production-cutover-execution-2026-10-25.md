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
