# Strategy Fold Runbook

This runbook covers the strategy authoring fold only.

## Inputs

Required prep artifacts:

- `classification_proposal.md`
- `phase1_2_core_requirements.md`
- `metric_ledger.md`
- `ccog_reference_resolved.md`

Required fold config:

- `ENSEMBLE_PHASE_1_7`
- `MAX_REVISION_PASSES`

## Active-Writer Rules

- If `ENSEMBLE_PHASE_1_7` is `[]`, use the existing single-agent path.
- If one writer is selected, launch only that writer's strategy server, skip
  `C1`, and have that writer publish the final canonical
  `phase1_7_strategy_report.md` directly before shutdown.
- If two or three writers are selected, launch one server per selected writer
  and then run `C1`.

For each active author server:

- writer = selected writer for that server
- advisors = other selected writers for the same fold
- canonical tester = `qa-auditor`

## Author-Server Loop

### IMPLEMENT

- Writer creates or revises the draft:
  - `screening-lead/phase1_7_strategy_report.md`, or
  - `technical-lead/phase1_7_strategy_report.md`, or
  - `ats-format-lead/phase1_7_strategy_report.md`

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

Before a strategy author server shuts down, the writer or `qa-auditor` exports:

- DISCUSS notes from `get-discussion`
- any TEST-phase advisor notes that were consumed live

Destination:

- `_discussion/advisor_notes_S1.md`
- `_discussion/advisor_notes_S2.md`
- `_discussion/advisor_notes_S3.md`

If no advisor notes were received, still export a file that explicitly states
that no advisor input was captured for that server.

## Consensus

`C1` reads:

- all active strategy drafts
- exported advisor note files for the active strategy servers

`C1` writes:

- `phase1_7_strategy_report.md`
- `_discussion/round2_consensus.md`
- `_discussion/disagreement_log.md`

Required consensus behavior:

- resolve factual conflicts against the shared truth hierarchy
- preserve unresolved disagreements in `## Open Questions`
- do not introduce new claims beyond the frozen prep artifacts and selected
  writer drafts

## Exit Condition

The strategy fold is complete when:

- all active strategy drafts are finished
- all active strategy advisor note files are exported
- the canonical `phase1_7_strategy_report.md` exists, or one selected writer has
  written the canonical output directly in single-writer mode
- `apex-user-feedback-revision` has been identified as the next step
