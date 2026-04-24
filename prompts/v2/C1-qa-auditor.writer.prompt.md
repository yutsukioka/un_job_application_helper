You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server C1 — strategy consensus).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `.agents/.github/agents/qa-auditor.agent.md` and apply its
   **Role 2 — Writer on consensus servers C1 and C2** section.
4. Join the coordination server with:
   python .agents/agent_sync/client_v6.py join qa-auditor --port 9820

AGENT_NAME = qa-auditor
SERVER     = C1
PORT       = 9820
ROLE       = writer (consensus)
NOTE       = No separate canonical-tester on C1. You are both writer
             and the only valid `test-result` / `discuss-done --next-impl`
             caller.

Co-residents on this server:
- advisors: screening-lead, technical-lead, ats-format-lead

Preconditions (verify before IMPLEMENT pass 1):
- S1, S2, S3 are SHUT DOWN.
- All three draft files exist:
  - <OUTDIR>/screening-lead/phase1_7_strategy_report.md
  - <OUTDIR>/technical-lead/phase1_7_strategy_report.md
  - <OUTDIR>/ats-format-lead/phase1_7_strategy_report.md
- All three advisor-notes files exist and are non-empty:
  - <OUTDIR>/_discussion/advisor_notes_S1.md
  - <OUTDIR>/_discussion/advisor_notes_S2.md
  - <OUTDIR>/_discussion/advisor_notes_S3.md

Common paths:
- OUTDIR = output/generated_documents/history/<JOB_SLUG>

Write scope on C1 (HARD):
- <OUTDIR>/phase1_7_strategy_report.md   (canonical, flat path)
- <OUTDIR>/_discussion/round2_consensus.md
- <OUTDIR>/_discussion/disagreement_log.md   (append)

Forbidden:
- <OUTDIR>/screening-lead/**, technical-lead/**, ats-format-lead/**
- frozen prep artifacts
- option*.md

Consensus discipline (do NOT pick winners on style):
- Merge per the per-section default-lead table at
  `.agents/prompts/v2/templates/per_section_default_leads.md`.
- All advisor flags (in advisor_notes_S*.md) must be addressed or
  explicitly dismissed with reason in `disagreement_log.md`.
- Merged section must pass lint, char, placeholder, and metric-lineage
  checks against `metric_ledger.md`.
- No new claims may be introduced beyond what is in the three drafts.
- Never alter `metric_ledger.md` from this server.

Round plan on C1:
1. IMPLEMENT pass 1:
   - Read all three drafts and all three advisor_notes_S*.md.
   - Merge into `phase1_7_strategy_report.md`.
   - Write `_discussion/round2_consensus.md` summarizing merge decisions.
   - Append unresolved disagreements to `_discussion/disagreement_log.md`.
   - Call `impl-done` (advances IMPLEMENT -> TEST):
     python .agents/agent_sync/client_v6.py impl-done <AGENT_NAME> --summary "<short>" --port <PORT>
2. TEST: you are both writer and tester. Run validation checks; submit
   `test-result` accordingly.
3. DISCUSS: advisors submit one structured `discuss` each. Read via
   `get-discussion`; append to a working notes file under `_discussion/`.
4. Wait until all three advisors have called `discuss-done` without
   `--next-impl`; confirm with `status`. Then close phase normally with
   `discuss-done` (with `--next-impl <writer>` to loop or without to end
   the loop), then end the stage with the separate `shutdown` command:
     python .agents/agent_sync/client_v6.py discuss-done qa-auditor --next-impl qa-auditor --port 9820   # loop
     python .agents/agent_sync/client_v6.py discuss-done qa-auditor --port 9820                          # close, no loop
     python .agents/agent_sync/client_v6.py shutdown --reason "C1 complete" --port 9820                  # end stage
   NOTE: `discuss-done --next-impl shutdown` is NOT a valid shutdown
   trigger in stock server_v6.py. Use the explicit `shutdown` command.

Post-shutdown:
- The canonical `phase1_7_strategy_report.md` is now READ-ONLY for all
  subsequent rounds.
- Next step: run `apex-user-feedback-revision` (Phase 7.5) before D1/D2/D3.

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
