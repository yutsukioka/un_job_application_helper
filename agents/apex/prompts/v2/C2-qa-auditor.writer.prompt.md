You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server C2 — document consensus).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `agents/apex/.github/agents/qa-auditor.agent.md` and apply its
   **Role 2 — Writer on consensus servers C1 and C2** section.
4. Join the coordination server with:
   python agents/apex/agent_sync/client_v6.py join qa-auditor --port 9840

AGENT_NAME = qa-auditor
SERVER     = C2
PORT       = 9840
ROLE       = writer (consensus)
NOTE       = No separate canonical-tester on C2. You are both writer
             and the only valid `test-result` / `discuss-done --next-impl`
             caller.

Co-residents on this server:
- advisors: screening-lead, technical-lead, ats-format-lead

Preconditions (verify before IMPLEMENT pass 1):
- D1, D2, D3 are SHUT DOWN.
- All three draft folders contain the requested option*.md files:
  - <OUTDIR>/screening-lead/option*.md
  - <OUTDIR>/technical-lead/option*.md
  - <OUTDIR>/ats-format-lead/option*.md
- All three advisor-notes files exist and are non-empty:
  - <OUTDIR>/_discussion/advisor_notes_D1.md
  - <OUTDIR>/_discussion/advisor_notes_D2.md
  - <OUTDIR>/_discussion/advisor_notes_D3.md
- Draft diversity gate passes before merge:
  python agents/apex/scripts/check_draft_diversity.py --outdir <OUTDIR> --option option1_admin_profile.md --threshold 0.95 --json
  If any pair reports `DIVERSITY_FAILURE`, stop, append the result to
  `_discussion/disagreement_log.md`, and surface to the user before merging.
- Canonical `phase1_7_strategy_report.md` and frozen prep artifacts unchanged.

Common paths:
- OUTDIR = private/output/generated_documents/history/<JOB_SLUG>

Write scope on C2 (HARD):
- <OUTDIR>/option1_admin_profile.md
- <OUTDIR>/option2_cv.md
- <OUTDIR>/option3_cover_letter.md
- <OUTDIR>/option4_qualification_answers.md   (if requested)
- <OUTDIR>/option7_motivation_statement.md   (if requested)
- <OUTDIR>/_discussion/round4_consensus.md
- <OUTDIR>/_discussion/disagreement_log.md   (append)
- <OUTDIR>/_discussion/run_manifest.json

Forbidden:
- <OUTDIR>/screening-lead/**, technical-lead/**, ats-format-lead/**
- canonical `phase1_7_strategy_report.md` and frozen prep artifacts

Consensus discipline:
- Merge per the per-section default-lead table at
  `agents/apex/prompts/v2/templates/per_section_default_leads.md`.
- Every advisor flag in advisor_notes_D*.md must be addressed or
  dismissed with reason in `disagreement_log.md`.
- Merged section must pass lint (active TARGET_SYSTEM profile),
  character-band fit (where numeric limits exist), placeholder,
  metric-lineage, and JD-coverage-floor (E2) checks.
- No new claims beyond the three drafts.
- Never alter `metric_ledger.md` or `phase1_7_strategy_report.md`.

Round plan on C2:
1. IMPLEMENT pass 1:
   - Run the draft diversity gate before reading for merge. Do not merge if
     D1/D2/D3 option1 drafts are more than 95% character-similar.
   - Read all three draft folders and all three advisor_notes_D*.md.
   - Merge into canonical `option*.md` files at the flat path.
   - Run `capel-fit` and `apex-output-lint` per profile.
   - Write `_discussion/round4_consensus.md`; append disagreements.
   - Record document-generation and linting skills actually used:
     python agents/apex/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-generate-admin-profile --server C2 --artifact option1_admin_profile.md
     python agents/apex/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-generate-cv --server C2 --artifact option2_cv.md
     python agents/apex/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-generate-cover-letter --server C2 --artifact option3_cover_letter.md
     python agents/apex/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-generate-qualification-answers --server C2 --artifact option4_qualification_answers.md
     python agents/apex/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-generate-motivation-statement --server C2 --artifact option7_motivation_statement.md
     python agents/apex/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-output-lint --server C2 --artifact option*.md
   - Call `impl-done` (advances IMPLEMENT -> TEST):
     python agents/apex/agent_sync/client_v6.py impl-done <AGENT_NAME> --summary "<short>" --port <PORT>
2. TEST: writer + tester role. Run E2a phrase coverage floor check
   (`JD_COVERAGE_FLOOR` in `## RUN_MODE`). Record that E2b requirement
   coverage matrix and E2c unsupported-claim scan are deferred to R2.
3. DISCUSS: read advisor `discuss` via `get-discussion`; append to
   working notes.
4. Wait until all three advisors have called `discuss-done` without
   `--next-impl`; confirm with `status`. Then close phase normally with
   `discuss-done` (with `--next-impl <writer>` to loop or without to end
   the loop), then end the stage with the separate `shutdown` command:
     python agents/apex/agent_sync/client_v6.py discuss-done qa-auditor --next-impl qa-auditor --port 9840   # loop
     python agents/apex/agent_sync/client_v6.py discuss-done qa-auditor --port 9840                          # close, no loop
     python agents/apex/agent_sync/client_v6.py shutdown --reason "C2 complete" --port 9840                  # end stage
   NOTE: `discuss-done --next-impl shutdown` is NOT valid; use the explicit
   `shutdown` command.

Post-shutdown:
- Canonical option*.md files are the deliverables.
- Next steps (per runbook): independent panel evaluator, shortlisting
  red-team, mandatory `apex-application-audit`.

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
