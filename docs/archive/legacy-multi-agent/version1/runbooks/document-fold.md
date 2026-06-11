# Document Fold Runbook

This runbook covers the Phase 8 authoring fold for the initial v1 topology.

## Inputs

Required upstream artifacts:

- canonical `phase1_7_strategy_report.md`
- `_discussion/round2_consensus.md`
- `metric_ledger.md`
- `ccog_reference_resolved.md`
- any required output selections for Option 1, 2, 3, and optional 4
- the result of `apex-user-feedback-revision`

Required fold config:

- `ENSEMBLE_PHASE_8`
- `MAX_REVISION_PASSES`

## Initial Scope

The v1 document-fold topology covers:

- `option1_admin_profile.md`
- `option2_cv.md`
- `option3_cover_letter.md`
- `option4_qualification_answers.md` when selected

Options 5-8 are not modeled in the v1 multi-server consensus topology.

## Active-Writer Rules

- If `ENSEMBLE_PHASE_8` is `[]`, use the existing single-agent path.
- If one writer is selected, launch only that writer's document server, skip
  `C2`, and have that writer publish the final canonical option file set
  directly before shutdown.
- If two or three writers are selected, launch one server per selected writer
  and then run `C2`.

For each active author server:

- writer = selected writer for that server
- advisors = other selected writers for the same fold
- canonical tester = `qa-auditor`

## Author-Server Loop

### IMPLEMENT

- Writer creates or revises its draft set under:
  - `screening-lead/option*.md`, or
  - `technical-lead/option*.md`, or
  - `ats-format-lead/option*.md`

### TEST

- `qa-auditor` submits `TEST::PASS` or `TEST::FAIL`.
- Advisors may send review notes via `send` or `broadcast`.
- Advisors must not call `test-result`.

### DISCUSS

- Each advisor submits one structured discuss message for the round.
- `qa-auditor` decides whether a bounded revision pass is needed.

### Optional Second Pass

- Same writer revises.
- Stop after `MAX_REVISION_PASSES`.

## Export Step

Before a document author server shuts down, the writer or `qa-auditor` exports:

- DISCUSS notes from `get-discussion`
- any TEST-phase advisor notes that were consumed live

Destination:

- `_discussion/advisor_notes_D1.md`
- `_discussion/advisor_notes_D2.md`
- `_discussion/advisor_notes_D3.md`

## Consensus

`C2` reads:

- all active document drafts
- exported advisor note files for the active document servers

`C2` writes the canonical output set:

- `option1_admin_profile.md`
- `option2_cv.md`
- `option3_cover_letter.md`
- `option4_qualification_answers.md` when selected
- `_discussion/round4_consensus.md`
- `_discussion/disagreement_log.md`

Required consensus behavior:

- prefer facts from `metric_ledger.md` and the canonical strategy report
- preserve cross-document consistency
- avoid introducing unsupported claims
- keep unresolved tradeoffs visible in the disagreement log when needed

## Post-Document Evaluation

After at least one canonical candidate-facing document exists:

- run `independent-panel-evaluator`
- run `independent-shortlisting-redteam`

The red-team remains post-document only in v1.
