You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server R1 — panel-response round).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `agents/apex/.github/agents/screening-lead.agent.md` and apply its
   v2 writer-mode rules.
4. Join the coordination server with:
   python agents/apex/agent_sync/client_v6.py join screening-lead --port 9860

AGENT_NAME = screening-lead
SERVER     = R1
PORT       = 9860
ROLE       = writer

Co-residents on this server:
- advisors: technical-lead, ats-format-lead
- canonical tester: qa-auditor

Preconditions:
- E1 and E2 have SHUT DOWN.
- <OUTDIR>/independent_panel_evaluation.md exists.
- <OUTDIR>/independent_shortlisting_risk_review.md exists.
- Canonical option*.md files exist.

Common paths:
- OUTDIR        = private/output/generated_documents/history/<JOB_SLUG>
- ADVISOR_NOTES = <OUTDIR>/_discussion/advisor_notes_R1.md

Write scope on R1 (HARD):
- <OUTDIR>/panel_response.md
- <OUTDIR>/_discussion/advisor_notes_R1.md   (writer or qa-auditor only)

Forbidden:
- all option*.md files
- independent evaluation files
- phase1_7_strategy_report.md
- metric_ledger.md
- draft folders

Round plan on R1:
1. IMPLEMENT pass 1:
   - Read E1/E2 outputs, canonical option*.md files, full context, frozen
     prep artifacts, and metric_ledger.md.
   - Write `panel_response.md` accepting, contesting, or deferring each
     E1/E2 finding. For every accepted finding, identify the smallest
     viable fix owner and file.
   - Do not rewrite candidate-facing documents.
   - Call:
     python agents/apex/agent_sync/client_v6.py impl-done screening-lead --summary "<short>" --port 9860
2. TEST / DISCUSS / loop:
   - Standard author-server pattern. Do not call `test-result` or
     `discuss-done --next-impl`.
   - During DISCUSS call:
     python agents/apex/agent_sync/client_v6.py discuss screening-lead "<structured discuss>" --port 9860
     python agents/apex/agent_sync/client_v6.py discuss-done screening-lead --port 9860
3. IMPLEMENT pass 2 if qa-auditor loops:
   - First action: read ADVISOR_NOTES.

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
