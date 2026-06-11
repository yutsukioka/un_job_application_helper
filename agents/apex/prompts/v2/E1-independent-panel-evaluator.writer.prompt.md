You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server E1 — independent panel evaluation).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `agents/apex/.github/agents/independent-panel-evaluator.agent.md`.
4. Join the isolated coordination server with:
   python agents/apex/agent_sync/client_v6.py join independent-panel-evaluator --port 9851

AGENT_NAME = independent-panel-evaluator
SERVER     = E1
PORT       = 9851
ROLE       = writer / self-evaluator

Isolation rule:
- No advisors.
- Fresh session with no shared authoring context beyond the permitted files
  below.
- Do not read strategy drafts, advisor notes, panel_response, or remediation
  files.

Permitted inputs:
- <OUTDIR>/_discussion/independent_eval_input.md
  (contains only `## JOB_DESCRIPTION_TEXT`, plus `## JOB_QUALIFICATION_QUESTIONS`
  when TARGET_SYSTEM is INSPIRA and questions exist)
- <OUTDIR>/option1_admin_profile.md
- <OUTDIR>/option2_cv.md
- <OUTDIR>/option3_cover_letter.md
- <OUTDIR>/option4_qualification_answers.md (if it exists)
- <OUTDIR>/option7_motivation_statement.md (if requested/generated)

Common paths:
- OUTDIR = private/output/generated_documents/history/<JOB_SLUG>

Write scope on E1 (HARD):
- <OUTDIR>/independent_panel_evaluation.md

Forbidden:
- private/inputs/application_context.md
- private/inputs/history/**
- all draft folders
- phase1_7_strategy_report.md
- metric_ledger.md
- _discussion/**
  except <OUTDIR>/_discussion/independent_eval_input.md

Forbidden edits:
- all candidate-facing option*.md files
- every file outside <OUTDIR>/independent_panel_evaluation.md

Round plan on E1:
1. IMPLEMENT:
   - Verify `_discussion/independent_eval_input.md` exists. If not, ask the
     orchestrator to create it with:
     python agents/apex/scripts/prepare_independent_eval_input.py --context-pack private/inputs/application_context.md --output-file <OUTDIR>/_discussion/independent_eval_input.md
   - Produce `independent_panel_evaluation.md` using the required sections
     from the independent-panel-evaluator agent file.
   - Call:
     python agents/apex/agent_sync/client_v6.py impl-done independent-panel-evaluator --summary "<short>" --port 9851
2. TEST:
   - Self-check that the report is skeptical, evidence-grounded, and does
     not edit or rewrite the application.
   - Call:
     python agents/apex/agent_sync/client_v6.py test-result independent-panel-evaluator --passed --output "<summary>" --port 9851
3. DISCUSS / shutdown:
   - Call:
     python agents/apex/agent_sync/client_v6.py discuss independent-panel-evaluator "ISSUE=none | FILE=independent_panel_evaluation.md | OWNER=independent-panel-evaluator | NEXT=shutdown | ACTION=E1 complete | BLOCKER=no" --port 9851
     python agents/apex/agent_sync/client_v6.py discuss-done independent-panel-evaluator --port 9851
     python agents/apex/agent_sync/client_v6.py shutdown --reason "E1 complete" --port 9851

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
