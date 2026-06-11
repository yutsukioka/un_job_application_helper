# README Revised

Date: 2026-05-06

## What Changed

This revision hardens the v2 multi-agent workflow against shallow review,
copied D-server drafts, noisy CCOG scoring, non-isolated independent evaluation,
and missing run-level audit metadata.

The previous server topology remains intact, including live E1/E2/R1/R2 support
for different models and sessions.

## Revised Files

### New Helper Scripts

- `.agents/scripts/validate_advisor_notes.py`
- `.agents/scripts/check_draft_diversity.py`
- `.agents/scripts/prepare_d_writer_scaffold.py`
- `.agents/scripts/prepare_independent_eval_input.py`
- `.agents/scripts/write_run_manifest.py`

### Previously Added And Retained From The Prior Revision

- `.agents/scripts/launch_v2_servers.sh`
- `.agents/scripts/start_agent_sync_server.py`
- `.agents/prompts/v2/E1-independent-panel-evaluator.writer.prompt.md`
- `.agents/prompts/v2/E2-independent-shortlisting-redteam.writer.prompt.md`
- `.agents/prompts/v2/R1-screening-lead.writer.prompt.md`
- `.agents/prompts/v2/R2-qa-auditor.writer.prompt.md`

### Revised Workflow Contracts

- `.agents/skills/apex-agent-sync-protocol/SKILL.md`
- `.agents/skills/apex-ccog-resolver/SKILL.md`
- `.agents/skills/apex-ccog-resolver/scripts/resolve_ccog.py`
- `.agents/spec/05_tier_e_integrity_guardians.md`
- `.agents/topology/runbook.md`
- `.agents/topology/server_manifest.yaml`

### Revised Agent and Prompt Files

- `.agents/.github/agents/qa-auditor.agent.md`
- `.agents/.github/agents/independent-panel-evaluator.agent.md`
- `.agents/.github/agents/independent-shortlisting-redteam.agent.md`
- `.agents/prompts/05-independent-panel-evaluator.prompt.md`
- `.agents/prompts/06-independent-shortlisting-redteam.prompt.md`
- `.agents/prompts/v2/_advisor.template.prompt.md`
- `.agents/prompts/v2/_canonical-tester.template.prompt.md`
- `.agents/prompts/v2/P0a-screening-lead.writer.prompt.md`
- `.agents/prompts/v2/P0b-technical-lead.writer.prompt.md`
- `.agents/prompts/v2/C1-qa-auditor.writer.prompt.md`
- `.agents/prompts/v2/C2-qa-auditor.writer.prompt.md`
- `.agents/prompts/v2/D1-screening-lead.writer.prompt.md`
- `.agents/prompts/v2/D2-technical-lead.writer.prompt.md`
- `.agents/prompts/v2/D3-ats-format-lead.writer.prompt.md`
- `.agents/prompts/v2/E1-independent-panel-evaluator.writer.prompt.md`
- `.agents/prompts/v2/E2-independent-shortlisting-redteam.writer.prompt.md`
- `.agents/prompts/v2/R2-qa-auditor.writer.prompt.md`

## How To Use The New Gates

### Advisor Notes Substantive Check

Before shutting down author servers, `qa-auditor` should run:

```bash
python .agents/scripts/validate_advisor_notes.py \
  --advisor-notes output/generated_documents/history/<JOB_SLUG>/_discussion/advisor_notes_<SERVER>.md \
  --advisors "<advisor1>,<advisor2>" --json
```

Shutdown fails if an advisor only wrote a rubber-stamp confirmation.

### D-Writer Scaffold

Each D writer runs before IMPLEMENT pass 1:

```bash
python .agents/scripts/prepare_d_writer_scaffold.py \
  --outdir output/generated_documents/history/<JOB_SLUG> \
  --role technical-lead
```

Use the matching role for D1/D2/D3. The script fails if the writer folder
already contains non-empty option drafts.

### C2 Draft Diversity Gate

C2 runs before merge:

```bash
python .agents/scripts/check_draft_diversity.py \
  --outdir output/generated_documents/history/<JOB_SLUG> \
  --option option1_admin_profile.md \
  --threshold 0.95 --json
```

If it reports `DIVERSITY_FAILURE`, C2 must stop and surface the issue.

### Independent Evaluation Input

Before E1/E2:

```bash
python .agents/scripts/prepare_independent_eval_input.py \
  --context-pack inputs/application_context.md \
  --output-file output/generated_documents/history/<JOB_SLUG>/_discussion/independent_eval_input.md
```

E1/E2 may read only this sanitized benchmark plus canonical Option outputs.

### Run Manifest

Initialize in P0a:

```bash
python .agents/scripts/write_run_manifest.py init \
  --outdir output/generated_documents/history/<JOB_SLUG> \
  --job-slug <JOB_SLUG> \
  --target-system INSPIRA
```

Record a skill:

```bash
python .agents/scripts/write_run_manifest.py add-skill \
  --outdir output/generated_documents/history/<JOB_SLUG> \
  --skill apex-ccog-resolver \
  --server P0b \
  --artifact ccog_reference_resolved.md
```

Finalize in R2:

```bash
python .agents/scripts/write_run_manifest.py finalize \
  --outdir output/generated_documents/history/<JOB_SLUG>
```

## CCOG Resolver Changes

The resolver now:

- Uses IDF-style weighting instead of raw overlap counts.
- Ignores generic verbs and scope words when they are not domain-specific.
- Extracts vacancy title from table-style JD fields such as `Job Title`.
- Expands title semantics, for example `data` to `information`, `database`,
  `statistics`, and `analytics`.
- Penalizes entries with no meaningful title/domain overlap.
- Sorts title/domain-aligned entries ahead of generic high scorers.

## Plan Only: Mandatory Execution Trace

No code change was made for this item in this revision.

Detailed plan:

1. Define the execution trace as a separate artifact from `run_manifest.json`.
   The manifest remains run metadata; the trace records step-by-step skill
   execution.
2. Standardize the output path as
   `output/generated_documents/history/<JOB_SLUG>/_discussion/execution_trace.md`.
3. Add a lightweight reconstruction script that reads:
   - `_discussion/run_manifest.json`
   - `advisor_notes_*.md`
   - `round*_consensus.md`
   - `disagreement_log.md`
   - `tmp/agent_sync/<server>.log`
4. Reconstruct each step with:
   - server
   - phase
   - writer
   - skill invoked
   - input sections consumed
   - artifacts produced
   - next dependency
   - PASS/WARN/FAIL status
5. Make R2 responsible for running the reconstruction script before final
   shutdown.
6. Add a validation rule: R2 cannot finalize if a required server has no trace
   entry.
7. Later, integrate `agent-execution-tracer` as the skill-level writer of the
   trace entries, replacing post-run reconstruction with live append-only
   recording.

## Plan Only: Term Extractor Canonical Path

No code change was made for this item in this revision.

Detailed plan:

1. Define a canonical term snapshot:
   `output/generated_documents/history/<JOB_SLUG>/term_extractor_snapshot.md`.
2. P0a writes the snapshot inside its output scope whenever
   `inputs/application_context.md` has an empty or stale `## TERM_EXTRACTOR`.
3. Downstream agents read the snapshot first, then fall back to the context
   section only if the snapshot is absent.
4. Add an explicit user/orchestrator authorization gate for updating
   `inputs/application_context.md`.
5. Create a deterministic section updater that can modify only the
   `## TERM_EXTRACTOR` block, leaving all other context sections untouched.
6. Add a QA check in P0a: if context `## TERM_EXTRACTOR` is empty but the
   snapshot exists, advisor notes must say which source downstream agents
   should use.
7. Add a final R2 manifest field showing whether the canonical context was
   updated, snapshot-only, or unresolved.
