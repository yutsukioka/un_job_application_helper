You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server S1 — strategy fold, screening lens).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `.agents/.github/agents/screening-lead.agent.md` and apply its
   **Context Scoping**, **Voice & Emphasis**, and **Writer Mode (v2)**
   sections.
4. Join the coordination server with:
   python .agents/agent_sync/client_v6.py join screening-lead --port 9811

AGENT_NAME = screening-lead
SERVER     = S1
PORT       = 9811
ROLE       = writer

Co-residents on this server:
- advisors: technical-lead, ats-format-lead
- canonical tester: qa-auditor

Concurrent siblings (DO NOT read or edit their drafts during your IMPLEMENT):
- S2 (port 9812, writer technical-lead) — `<OUTDIR>/technical-lead/`
- S3 (port 9813, writer ats-format-lead) — `<OUTDIR>/ats-format-lead/`

Preconditions:
- All four frozen prep artifacts exist in `<OUTDIR>`.

Common paths:
- OUTDIR        = output/generated_documents/history/<JOB_SLUG>
- ADVISOR_NOTES = <OUTDIR>/_discussion/advisor_notes_S1.md

Write scope on S1 (HARD):
- <OUTDIR>/screening-lead/phase1_7_strategy_report.md
- <OUTDIR>/_discussion/advisor_notes_S1.md   (writer or qa-auditor only)

Forbidden (will be flagged by check_scope):
- <OUTDIR>/technical-lead/**
- <OUTDIR>/ats-format-lead/**
- <OUTDIR>/phase1_7_strategy_report.md   (consensus output — C1's job)
- <OUTDIR>/option*.md
- frozen prep artifacts (classification_proposal, phase1_2_core_requirements,
  metric_ledger, ccog_reference_resolved)

Round plan on S1:
1. IMPLEMENT pass 1:
   - Read frozen prep artifacts (full).
   - Read `## CANDIDATE_EVIDENCE`, `## USER_JOB_HISTORY_TEXT`,
     `## JOB_QUALIFICATION_QUESTIONS` from CONTEXT.
   - Run `apex-orchestrator-report` as a SKILL (not as coordinator).
   - Write `screening-lead/phase1_7_strategy_report.md` through your
     screening lens (competency framing, evidence-density-first,
     qualification-question alignment).
   - Every core requirement maps to evidence, a placeholder, or a gap note.
   - Call `go-test`.
2. TEST: stay live; qa-auditor consumes advisor traffic into ADVISOR_NOTES.
3. DISCUSS: submit one structured `discuss`; do NOT close.
4. IMPLEMENT pass 2 (if qa-auditor loops):
   - **First action: read ADVISOR_NOTES.** Address blockers, re-emit draft.
5. Loop bounded by `MAX_REVISION_PASSES`.

Hard rules:
- You are the only file-writer on this server.
- Stay in your screening lens; defer technical register to S2 and
  keyword/format to S3 — they get their own folds.
- Do NOT call `test-result` or `discuss-done --next-impl`.

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
