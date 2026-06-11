You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server R2 — remediation consensus and final
audit).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `.agents/.github/agents/qa-auditor.agent.md` and apply its
   **Role 2 — Writer on consensus servers C1 and C2** by analogy for R2.
4. Load `apex-application-audit`.
5. Join the coordination server with:
   python .agents/agent_sync/client_v6.py join qa-auditor --port 9861

AGENT_NAME = qa-auditor
SERVER     = R2
PORT       = 9861
ROLE       = writer (remediation consensus)

Co-residents on this server:
- advisors: screening-lead, technical-lead, ats-format-lead

Preconditions:
- R1 has SHUT DOWN.
- <OUTDIR>/panel_response.md exists.
- E1/E2 outputs and canonical option*.md files exist.

Common paths:
- OUTDIR = output/generated_documents/history/<JOB_SLUG>

Write scope on R2 (HARD):
- <OUTDIR>/remediation_plan.md
- <OUTDIR>/application_audit.md
- <OUTDIR>/_discussion/run_manifest.json
- <OUTDIR>/_discussion/round5_consensus.md
- <OUTDIR>/_discussion/disagreement_log.md   (append)

Forbidden:
- all option*.md files
- independent evaluation files
- panel_response.md
- phase1_7_strategy_report.md
- metric_ledger.md
- draft folders

Round plan on R2:
1. IMPLEMENT pass 1:
   - Read `panel_response.md`, E1/E2 outputs, canonical option*.md files,
     full context, frozen prep artifacts, and metric_ledger.md.
   - Run `apex-application-audit` and write the result to
     `application_audit.md`.
   - Run the three-layer coverage assessment:
     - E2a: confirm C2 phrase coverage floor result or rerun if absent.
     - E2b: requirement-by-requirement coverage matrix across Admin Profile,
       CV, Cover Letter, Qualification Answers, and Motivation Statement if
       present.
     - E2c: unsupported-claim scan against full context and `metric_ledger.md`.
   - Write `remediation_plan.md` consolidating accepted fixes, deferred
     items, rejected findings, owners, and exact follow-up server(s) if any.
   - Record final review skills:
     python .agents/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-application-audit --server R2 --artifact application_audit.md
   - Finalize `_discussion/run_manifest.json`:
     python .agents/scripts/write_run_manifest.py finalize --outdir <OUTDIR>
   - Write `_discussion/round5_consensus.md`; append unresolved disputes to
     `_discussion/disagreement_log.md`.
   - Do not rewrite candidate-facing documents.
   - Call:
     python .agents/agent_sync/client_v6.py impl-done qa-auditor --summary "<short>" --port 9861
2. TEST:
   - You are both writer and tester. Run grounding, metric-lineage,
     E2b coverage-matrix, E2c unsupported-claim, and placeholder checks.
   - Call `test-result` with PASS or FAIL.
3. DISCUSS / shutdown:
   - Advisors submit one structured `discuss` each.
   - Wait until advisors have called `discuss-done` without `--next-impl`.
   - Close or loop:
     python .agents/agent_sync/client_v6.py discuss-done qa-auditor --next-impl qa-auditor --port 9861
     python .agents/agent_sync/client_v6.py discuss-done qa-auditor --port 9861
     python .agents/agent_sync/client_v6.py shutdown --reason "R2 complete" --port 9861

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
