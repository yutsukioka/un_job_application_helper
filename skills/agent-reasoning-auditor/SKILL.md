---
name: agent-reasoning-auditor
description: >-
  Record reasoning summaries that explain how the AI agent interpreted
  skill instructions and selected actions during pipeline execution.
  Verifies instruction alignment and flags deviations without exposing
  raw chain-of-thought. Use this skill during pipeline testing or when
  debugging unexpected agent decisions. This is a diagnostic skill — it
  does not produce application documents.
---

# agent-reasoning-auditor

## Purpose

This skill captures a structured reasoning trace for each skill
invocation, explaining:

1. How the agent interpreted the skill's instructions.
2. Why the agent selected a particular action or approach.
3. Whether the agent's behaviour aligned with the skill's stated rules
   and output format.
4. Where and why any deviations occurred.

The trace provides transparency for debugging without violating the
`apex-guardrails` rule against exposing chain-of-thought in production
outputs. Reasoning audits are diagnostic-only artifacts stored in the
test suite directory.

It can run:
- **Standalone** — to audit a single skill invocation.
- **As a sub-module of `agent-test-suite`** — called once per skill
  during a full pipeline test pass.

## Shared definitions

Reference the guardrails, source-grounding rules, and error handling
patterns defined in `apex-guardrails`. This skill's own output uses
format profile: `strategy_markdown`.

---

## Inputs

Required:

- `skill_name`: the skill whose execution is being audited.
- `skill_prompt`: the instructions from the skill's SKILL.md (or the
  relevant excerpt: Purpose, Rules, Steps, Output Format).
- `generated_output`: the actual text output the skill produced.

Optional:

- `execution_context`: pipeline state at the time of invocation (which
  phase, prior outputs available).
- `checkpoint_number`: sequential ID when called in a batch by
  `agent-test-suite` (default: 1).
- `input_sections_available`: which `application_context.md` sections
  were present — helps judge whether deviations were caused by missing
  inputs.

---

## Rules

1. **Diagnostic only.** This skill produces audit entries for developer
   consumption. Its output must never appear in production application
   documents.
2. **No raw chain-of-thought.** Summarize reasoning in structured,
   professional language. Do not dump internal deliberation, scoring
   rubrics, or loop counters.
3. **Evidence-based.** Every reasoning claim must reference a specific
   instruction or rule from the audited skill's SKILL.md and a specific
   observable behaviour in the generated output.
4. **Deviation ≠ failure.** A deviation is a mismatch between
   instruction and behaviour. It may be acceptable (e.g., skill adapted
   to missing optional input) or problematic (e.g., wrong format
   profile). Classify both — do not assume all deviations are errors.
5. **Append-only.** When used in batch mode, each invocation appends a
   checkpoint entry. Never overwrite prior entries.

---

## Audit Checks

### A1: Instruction Interpretation

Summarize how the agent interpreted the skill's key instructions:
- What did the agent understand as the primary task?
- Which rules did it prioritize?
- Which optional inputs or features did it engage vs. skip?

### A2: Decision Logic

Explain the agent's major decisions:
- Why was this approach chosen over alternatives?
- Were there trade-offs (e.g., compression vs. detail)?
- Did the agent apply the recursive quality loop (and how many cycles)?

### A3: Source Grounding Compliance

Check whether the output respects the `apex-guardrails` source-grounding
rule:
- Are all factual claims traceable to `inputs/application_context.md`?
- Were any facts invented or embellished?
- Are placeholders used where evidence is missing?

### A4: Instruction Alignment

Compare the output structure and content against the skill's stated
rules:
- Does the output match the required format profile?
- Were all required steps executed?
- Were any rules violated?

### A5: Deviation Detection

Identify any differences between expected and actual behaviour:
- Missing sections in output that the spec requires.
- Extra sections not specified.
- Different formatting choices.
- Content that addresses a different requirement than specified.

Classify each deviation:
- **ACCEPTABLE**: skill adapted to context (e.g., skipped optional
  section because input was absent).
- **PROBLEMATIC**: skill violated a hard rule without justification.
- **UNCLEAR**: deviation detected but root cause uncertain.

---

## Steps

1. Read the audited skill's SKILL.md (Purpose, Rules, Steps, Output
   Format sections).
2. Read the generated output.
3. Perform audit checks A1–A5 in order.
4. For each check, record findings and classify deviations.
5. Determine overall instruction compliance:
   - **PASS**: no problematic deviations.
   - **PARTIAL**: 1+ acceptable or unclear deviations, no problematic.
   - **FAIL**: 1+ problematic deviations.
6. Write the checkpoint entry to the output artifact.

---

## Output Artifact

File: `output/tmp/test_suite/0x_agent_reasoning_trace.md`

When called multiple times, each invocation appends a new checkpoint
entry.

### Output Format

```
## Checkpoint <checkpoint_number>

Skill Audited:
<skill_name>

### A1: Instruction Interpretation
- Primary task understood as: <summary>
- Rules prioritized: <list>
- Optional features engaged: <list or "none">
- Optional features skipped: <list or "none">

### A2: Decision Logic
- Approach chosen: <summary>
- Trade-offs made: <summary or "none observed">
- Quality loop cycles: <n or "not applicable">

### A3: Source Grounding
- Claims checked: <n>
- Grounded: <n>
- Ungrounded: <n>
- Placeholders correctly used: yes/no
- Details: <specifics of any ungrounded claims>

### A4: Instruction Alignment
- Format profile match: <yes/no — expected: X, actual: Y>
- Required steps executed: <all / partial — list missing>
- Rules violated: <none / list>

### A5: Deviations

| # | Description | Severity | Classification | Root Cause |
|---|---|---|---|---|
| 1 | <what differed> | <low/med/high> | ACCEPTABLE / PROBLEMATIC / UNCLEAR | <explanation> |

### Overall Instruction Compliance
<PASS / PARTIAL / FAIL>

### Notes
<any additional observations relevant to debugging>
```
