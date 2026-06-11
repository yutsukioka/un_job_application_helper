You are operating in the `un_job_application_helper/` workspace root in
v2 multi-agent ensemble mode (server P0b — prep stage 2,
post-human-confirmation).

Before doing anything:
1. Load `apex-guardrails`.
2. Load `apex-agent-sync-protocol`.
3. Load `agents/apex/.github/agents/technical-lead.agent.md` and apply its
   **Writer Mode (v2)** section.
4. Join the coordination server with:
   python agents/apex/agent_sync/client_v6.py join technical-lead --port 9801

AGENT_NAME = technical-lead
SERVER     = P0b
PORT       = 9801
ROLE       = writer

Co-residents on this server:
- advisors: screening-lead, ats-format-lead
- canonical tester: qa-auditor

Preconditions (verify before IMPLEMENT pass 1):
- `<OUTDIR>/classification_proposal.md` exists AND is human-confirmed.
- `<OUTDIR>/metric_ledger.md` exists.
- `<OUTDIR>/phase1_2_core_requirements.md` exists.
If any is missing, abort and surface the missing precondition.

Common paths:
- CONTEXT = private/inputs/application_context.md
- OUTDIR  = private/output/generated_documents/history/<JOB_SLUG>
- CCOG_DB = agents/apex/skills/apex-ccog-resolver/resource/ccog_reference_full.md

Write scope on P0b (HARD):
- <OUTDIR>/ccog_reference_resolved.md
- <OUTDIR>/_discussion/run_manifest.json

Round plan on P0b:
1. IMPLEMENT pass 1:
   - Run `apex-ccog-resolver` against the confirmed classification.
   - Write `ccog_reference_resolved.md` (compact 10-20 entry subset).
   - Record the invocation:
     python agents/apex/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-ccog-resolver --server P0b --artifact ccog_reference_resolved.md
   - Call `impl-done` (advances IMPLEMENT -> TEST):
     python agents/apex/agent_sync/client_v6.py impl-done <AGENT_NAME> --summary "<short>" --port <PORT>
2. TEST / DISCUSS / loop pattern: standard pattern. DISCUSS: submit one structured `discuss`, then call `discuss-done` WITHOUT `--next-impl` so the barrier advances. (same as P0a) but for this single artifact.

Post-shutdown freeze:
- After P0b SHUTDOWN the four prep artifacts are READ-ONLY for all
  subsequent rounds:
  - classification_proposal.md
  - phase1_2_core_requirements.md
  - metric_ledger.md  (FROZEN per Tier E1)
  - ccog_reference_resolved.md

Hard rules:
- You are the only file-writer on this server.
- Do NOT modify the prep artifacts owned by P0a.
- Do NOT call `test-result` or `discuss-done --next-impl`.

Use this job input:
- JOB_SLUG = <JOB_SLUG>

Start now:
- join
- check status
- announce write scope before editing
- begin IMPLEMENT
