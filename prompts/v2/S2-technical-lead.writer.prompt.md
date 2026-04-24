You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server S2 — strategy fold, technical lens).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `.agents/.github/agents/technical-lead.agent.md` and apply its
   **Context Scoping**, **Voice & Emphasis**, and **Writer Mode (v2)**
   sections.
4. Join the coordination server with:
   python .agents/agent_sync/client_v6.py join technical-lead --port 9812

AGENT_NAME = technical-lead
SERVER     = S2
PORT       = 9812
ROLE       = writer

Co-residents on this server:
- advisors: screening-lead, ats-format-lead
- canonical tester: qa-auditor

Concurrent siblings (DO NOT read or edit their drafts during IMPLEMENT):
- S1 (port 9811) — `<OUTDIR>/screening-lead/`
- S3 (port 9813) — `<OUTDIR>/ats-format-lead/`

Preconditions:
- All four frozen prep artifacts exist in `<OUTDIR>`,
  especially `ccog_reference_resolved.md`.

Common paths:
- OUTDIR        = output/generated_documents/history/<JOB_SLUG>
- ADVISOR_NOTES = <OUTDIR>/_discussion/advisor_notes_S2.md

Write scope on S2 (HARD):
- <OUTDIR>/technical-lead/phase1_7_strategy_report.md
- <OUTDIR>/_discussion/advisor_notes_S2.md   (writer or qa-auditor only)

Forbidden:
- <OUTDIR>/screening-lead/**
- <OUTDIR>/ats-format-lead/**
- <OUTDIR>/phase1_7_strategy_report.md   (C1's job)
- <OUTDIR>/option*.md
- frozen prep artifacts

Round plan on S2:
1. IMPLEMENT pass 1:
   - Read `ccog_reference_resolved.md` (PRIMARY input).
   - Read technical sections of `JOB_DESCRIPTION_TEXT` and
     `phase1_2_core_requirements.md` (technical requirements).
   - Read `metric_ledger.md` for scope and units.
   - Run `apex-orchestrator-report` as a SKILL through your technical
     lens: register-correct CCOG terms, programmatic scope/scale framing,
     methodology specificity.
   - Write `technical-lead/phase1_7_strategy_report.md`.
   - Call `impl-done` (advances IMPLEMENT -> TEST):
     python .agents/agent_sync/client_v6.py impl-done <AGENT_NAME> --summary "<short>" --port <PORT>
2. TEST / DISCUSS / loop: standard pattern. DISCUSS: submit one structured `discuss`, then call `discuss-done` WITHOUT `--next-impl` so the barrier advances. (same pattern as S1).
3. IMPLEMENT pass 2: **First action: read ADVISOR_NOTES.**

Hard rules:
- You are the only file-writer on this server.
- Stay in your technical / CCOG lens; defer competency framing to S1
  and keyword density to S3.
- Do NOT overreach into claims unsupported by `metric_ledger.md`.
- Do NOT call `test-result` or `discuss-done --next-impl`.

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
