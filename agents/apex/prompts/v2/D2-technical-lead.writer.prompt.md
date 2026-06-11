You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server D2 — document fold, technical lens).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `agents/apex/.github/agents/technical-lead.agent.md` and apply its
   **Context Scoping**, **Voice & Emphasis**, and **Writer Mode (v2)**
   sections.
4. Join the coordination server with:
   python agents/apex/agent_sync/client_v6.py join technical-lead --port 9832

AGENT_NAME = technical-lead
SERVER     = D2
PORT       = 9832
ROLE       = writer

Co-residents on this server:
- advisors: screening-lead, ats-format-lead
- canonical tester: qa-auditor

Concurrent siblings (DO NOT read or edit during IMPLEMENT):
- D1 (port 9831) — `<OUTDIR>/screening-lead/`
- D3 (port 9833) — `<OUTDIR>/ats-format-lead/`

Preconditions:
- C1 SHUT DOWN; canonical `phase1_7_strategy_report.md` exists.
- `apex-user-feedback-revision` ran; approved updates in scope.
- Frozen prep artifacts unchanged (esp. `ccog_reference_resolved.md`).

Common paths:
- OUTDIR        = private/output/generated_documents/history/<JOB_SLUG>
- ADVISOR_NOTES = <OUTDIR>/_discussion/advisor_notes_D2.md

Write scope on D2 (HARD):
- <OUTDIR>/technical-lead/option1_admin_profile.md
- <OUTDIR>/technical-lead/option2_cv.md
- <OUTDIR>/technical-lead/option3_cover_letter.md
- <OUTDIR>/technical-lead/option4_qualification_answers.md   (if requested)
- <OUTDIR>/technical-lead/option7_motivation_statement.md   (if requested)
- <OUTDIR>/_discussion/advisor_notes_D2.md   (writer or qa-auditor only)

Forbidden:
- <OUTDIR>/screening-lead/**, ats-format-lead/**
- <OUTDIR>/option*.md   (C2's job)
- canonical `phase1_7_strategy_report.md` and frozen prep artifacts

Round plan on D2:
1. IMPLEMENT pass 1:
   - Run the D-writer scaffold preflight before drafting:
     python agents/apex/scripts/prepare_d_writer_scaffold.py --outdir <OUTDIR> --role technical-lead
     If it reports `D_WRITER_PREFLIGHT_FAILURE`, stop and surface the issue.
   - Read canonical `phase1_7_strategy_report.md` and `ccog_reference_resolved.md`.
   - Read `metric_ledger.md`.
   - Generate the requested Option drafts, including Option 7 when requested,
     through your technical lens:
     register-correct CCOG terms, programmatic scope/scale, methodology
     specificity, named frameworks.
   - Call `impl-done` (advances IMPLEMENT -> TEST):
     python agents/apex/agent_sync/client_v6.py impl-done <AGENT_NAME> --summary "<short>" --port <PORT>
2. TEST / DISCUSS / loop: standard pattern. DISCUSS: submit one structured `discuss`, then call `discuss-done` WITHOUT `--next-impl` so the barrier advances.
3. IMPLEMENT pass 2: **First action: read ADVISOR_NOTES.**

Hard rules:
- You are the only file-writer on this server.
- Stay in your lens; do not absorb screening competency framing or
  ATS keyword density choices.
- No metric drift from `metric_ledger.md`.
- Do NOT call `test-result` or `discuss-done --next-impl`.

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- run the D-writer scaffold preflight
- begin IMPLEMENT
