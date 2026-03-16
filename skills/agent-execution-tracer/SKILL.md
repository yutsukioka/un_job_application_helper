---
name: agent-execution-tracer
description: >-
  Log the execution graph of ApexStrategist skill invocations during a
  pipeline run. Records each skill invoked, its triggering source,
  input sections consumed, artifacts produced, and the next skill in
  the chain. Use this skill during pipeline testing or debugging to
  visualize execution flow. This is a diagnostic skill — it does not
  produce application documents.
---

# agent-execution-tracer

## Purpose

This skill produces a sequential execution graph showing which skills
were invoked during a pipeline run, what triggered each invocation,
what inputs each consumed, and what artifacts each produced. The graph
makes it possible to:

- Verify that `apex-orchestrator-report` invokes skills in the correct
  phase order.
- Detect skipped or out-of-order steps.
- Identify dependency chains (skill B depends on output from skill A).
- Spot unnecessary re-invocations or circular calls.

It can run:
- **Standalone** — to trace a single skill invocation and its context.
- **As a sub-module of `agent_test_suite`** — called iteratively during
  Step 3 (Pipeline Execution Trace) to build the full graph.

## Shared definitions

Reference the guardrails and error handling patterns defined in
`apex-guardrails`. This skill's own output uses format profile:
`strategy_markdown`.

---

## Inputs

Required:

- `skill_name`: the skill being traced.
- `execution_context`: summary of the current pipeline state (which
  phase, what has been produced so far).

Optional:

- `parent_skill`: the skill or orchestrator step that triggered this
  invocation (default: `apex-orchestrator-report` or `user-direct`).
- `step_number`: sequential step ID within the pipeline run (default:
  auto-increment).
- `input_sections_consumed`: list of `application_context.md` sections
  the skill read (e.g., `USER_JOB_HISTORY_TEXT`, `TERM_EXTRACTOR`).
- `artifacts_generated`: list of output files or sections produced.

---

## Rules

1. **Append-only log.** Each invocation appends one step entry to the
   graph. Never overwrite prior entries.
2. **Observable facts only.** Record what actually happened, not what
   was expected. If a skill was skipped, record the skip — do not
   fabricate an entry.
3. **No content capture.** Record artifact file names and section
   headings, but do not duplicate full artifact content into the graph.
   The graph is metadata, not a content store.
4. **Dependency tracking.** When a skill consumes an artifact produced
   by a prior step, note the dependency explicitly so broken chains are
   visible.
5. **Timestamps optional.** If runtime timestamps are available, include
   them. If not (e.g., simulated run), omit rather than fabricate.

---

## Steps

1. Receive the invocation context (skill name, parent, step number).
2. Identify which `application_context.md` sections the skill requires
   by reading its SKILL.md `## Inputs` block.
3. Compare required inputs against the pipeline state to determine which
   sections were actually available.
4. Record the artifacts the skill produced (or was expected to produce).
5. Determine the next skill in the pipeline sequence from
   `apex-orchestrator-report` Steps.
6. Append the step entry to the output artifact.
7. If any required input was missing or any expected artifact was not
   produced, annotate the entry with a `WARN` or `FAIL` flag.

---

## Output Artifact

File: `output/tmp/test_suite/0x_agent_execution_graph.md`

Each pipeline run produces one file with all step entries appended
sequentially.

### Output Format

```
# Execution Graph

## Run Metadata
- Pipeline: <apex-orchestrator-report | custom>
- Context file: <path>
- Total steps recorded: <n>
- Date: <ISO date or "simulated">

## Execution Steps

### Step <step_number>

Skill Invoked:
<skill_name>

Phase:
<phase number from orchestrator, or "standalone">

Triggered By:
<parent_skill or "user-direct">

Input Sections Consumed:
- <section_name_1> (available: yes/no)
- <section_name_2> (available: yes/no)

Dependencies on Prior Steps:
- Step <n>: <artifact_name> (resolved: yes/no)

Artifacts Generated:
- <file_path_or_section_name>

Execution Status:
<OK / WARN / FAIL>

Status Notes:
<reason for WARN or FAIL, or "—" if OK>

Next Skill:
<skill_name or "END">

---

(Repeat for each step)

## Execution Summary

| Step | Phase | Skill | Status | Dependencies Met |
|---|---|---|---|---|
| 1 | 0 | <skill> | OK | yes |
| 2 | 1.2 | <skill> | OK | yes |
| ... | ... | ... | ... | ... |

## Dependency Graph (text)
Step 1 (<skill>) → Step 2 (<skill>) → Step 3 (<skill>) → ...
  ↳ Step 2 output feeds Step 4
  ↳ Step 3 output feeds Step 5

## Issues Detected
- <list of WARN/FAIL items with step references, or "None">
```
