# Launch Order Runbook

This runbook defines the default operator sequence for the multi-agent v1
topology. It assumes the live `.agents/` workspace stays unchanged and that v1
is being piloted as a separate staging effort.

## Preconditions

- `inputs/application_context.md` exists and is ready for the run.
- The target output folder under
  `output/generated_documents/history/<JOB_SLUG>/` exists or can be created by
  the active writer flow.
- Each server runs from its own working directory under `tmp/agent_sync/` to
  avoid `logs/v6` collisions.
- If notifier processes are used, do not run concurrent notifier instances for
  the same agent name on different ports. Stock notifier files are keyed by
  agent name, not by port.

## Phase 0: Decide Active Writers

Read `## RUN_MODE` from `inputs/application_context.md`.

- `ENSEMBLE_PHASE_1_7`
  determines which of `S1/S2/S3` exist.
- `ENSEMBLE_PHASE_8`
  determines which of `D1/D2/D3` exist.
- `[]` means fall back to the existing single-agent path for that fold.
- One selected writer means skip the fold's consensus server.

## Phase 1: Prep

1. Start `P0a`.
2. Complete `classification_proposal.md`, `phase1_2_core_requirements.md`, and
   `metric_ledger.md`.
3. Pause for the human vacancy-type confirmation step.
4. Shut down `P0a`.
5. Start `P0b`.
6. Complete `ccog_reference_resolved.md`.
7. Shut down `P0b`.

At this point the prep artifacts are treated as read-only shared inputs for all
later servers.

## Phase 2: Strategy Fold

1. Start all active strategy author servers in parallel:
   - `S1` if `screening-lead` is active
   - `S2` if `technical-lead` is active
   - `S3` if `ats-format-lead` is active
2. Run each author server through its writer/TEST/DISCUSS loop.
3. Export advisor notes for each live strategy author server before shutdown.
4. Shut down all active strategy author servers.
5. If more than one strategy writer was active, start `C1`.
6. Produce:
   - `phase1_7_strategy_report.md`
   - `_discussion/round2_consensus.md`
   - `_discussion/disagreement_log.md`
7. Shut down `C1`.

If exactly one strategy writer was active:

- export advisor notes if any exist
- have that writer publish the final canonical `phase1_7_strategy_report.md`
  directly before shutdown
- skip `C1`

## Phase 3: Pre-Phase-8 Gate

Run `apex-user-feedback-revision` against the canonical
`phase1_7_strategy_report.md`.

Do not run `independent-shortlisting-redteam` here. It remains post-document
only.

## Phase 4: Document Fold

1. Start all active document author servers in parallel:
   - `D1` if `screening-lead` is active
   - `D2` if `technical-lead` is active
   - `D3` if `ats-format-lead` is active
2. Run each author server through its writer/TEST/DISCUSS loop.
3. Export advisor notes for each live document author server before shutdown.
4. Shut down all active document author servers.
5. If more than one document writer was active, start `C2`.
6. Produce canonical Phase 8 outputs:
   - `option1_admin_profile.md`
   - `option2_cv.md`
   - `option3_cover_letter.md`
   - `option4_qualification_answers.md` when selected
7. Shut down `C2`.

If exactly one document writer was active:

- export advisor notes if any exist
- have that writer publish the final canonical option file set directly before
  shutdown
- skip `C2`

## Phase 5: Post-Evaluation

After at least one candidate-facing document exists:

1. Run `independent-panel-evaluator`.
2. Run `independent-shortlisting-redteam`.
3. If needed, run the post-eval response/remediation loop defined in the
   canonical spec.
