---
name: skill-failure-analyzer
description: >-
  Detect failures in skill execution and perform root cause analysis.
  Compares expected output against actual output, classifies failure
  types, identifies probable causes (prompt ambiguity, instruction
  conflict, missing input, context truncation), and recommends specific
  fixes. Use this skill after a pipeline test reveals FAIL or PARTIAL
  results. This is a diagnostic skill — it does not produce application
  documents.
---

# skill-failure-analyzer

## Purpose

This skill investigates why a skill produced incorrect or incomplete
output. Given the expected behaviour (from the skill's SKILL.md) and
the actual output, it:

1. Classifies the failure type.
2. Performs root cause analysis.
3. Recommends specific fixes to the skill's SKILL.md or to the pipeline
   configuration.

It can run:
- **Standalone** — to analyze a single skill failure.
- **As a sub-module of `agent-test-suite`** — called during Step 6
  (Failure Classification) to produce detailed analysis for each
  FAIL/WARN item.

## Shared definitions

Reference the guardrails, format profiles, and error handling patterns
defined in `apex-guardrails`. This skill's own output uses format
profile: `strategy_markdown`.

---

## Inputs

Required:

- `skill_name`: the skill that failed.
- `generated_output`: the actual output (or "NO_OUTPUT" if the skill
  produced nothing).

Optional (but strongly recommended):

- `expected_output_spec`: the relevant `## Output Format` block from
  the skill's SKILL.md (auto-read from
  `.agents/skills/<skill_name>/SKILL.md` if not provided).
- `execution_trace_entry`: the corresponding entry from
  `agent-execution-tracer` output, if available.
- `reasoning_audit_entry`: the corresponding entry from
  `agent-reasoning-auditor` output, if available.
- `compliance_checkpoint`: the corresponding entry from
  `agent-functionality-tester` output, if available.
- `input_sections_available`: which `application_context.md` sections
  were present at invocation time.

---

## Failure Type Classification

| Type | Code | Description |
|---|---|---|
| Format Mismatch | `FMT` | Output structure doesn't match SKILL.md spec |
| Missing Section | `SEC` | One or more required output sections absent |
| Missing Output | `OUT` | Skill produced no output at all |
| Wrong Skill | `RTE` | A different skill was invoked than expected |
| Content Fabrication | `FAB` | Output contains claims not in inputs |
| Character Limit Breach | `CHR` | Output exceeds stated character limit |
| Placeholder Leak | `PLH` | Template placeholders leaked into final output |
| Dependency Break | `DEP` | Required upstream artifact was missing |
| Partial Completion | `PRT` | Output is truncated or incomplete |

---

## Root Cause Categories

| Category | Code | Description | Typical Fix |
|---|---|---|---|
| Prompt Ambiguity | `RC-AMB` | SKILL.md instructions are vague or multi-interpretable | Tighten wording in SKILL.md; add explicit constraints |
| Instruction Conflict | `RC-CON` | Two rules in SKILL.md contradict each other | Resolve conflict; add priority rule |
| Missing Input | `RC-INP` | Required input section was empty or absent | Ensure context pack is complete; add input validation |
| Context Truncation | `RC-CTX` | Input was too long and was truncated before the skill processed it | Reduce input size or split processing |
| Wrong Format Profile | `RC-FMT` | Skill used wrong `apex-guardrails` format profile | Correct the profile reference in SKILL.md |
| Reasoning Error | `RC-RSN` | Agent misinterpreted instructions despite clear wording | Add examples or rephrase; report as agent limitation |
| Skill Wiring | `RC-WIR` | Orchestrator invoked the wrong skill | Fix routing in `apex-orchestrator-report` or `deterministic-skill-router` |
| Upstream Failure | `RC-UPS` | A prior skill failed, depriving this skill of needed input | Fix the upstream skill first |

---

## Rules

1. **Evidence-based analysis.** Every root cause determination must cite
   specific text from the SKILL.md, the generated output, or the
   execution trace. Do not speculate without evidence.
2. **One failure, one entry.** If a skill has multiple failures, create
   separate analysis entries for each. Do not merge unrelated failures.
3. **Actionable fixes only.** Recommendations must be specific enough
   that a developer can implement them (e.g., "Add rule: 'If
   TERM_EXTRACTOR is empty, skip keyword weighting'" rather than
   "Improve error handling").
4. **Severity classification.** Rate each failure:
   - **CRITICAL**: blocks downstream skills or produces harmful output.
   - **MAJOR**: produces incorrect output but doesn't block pipeline.
   - **MINOR**: cosmetic or non-essential deviation.
5. **No cascading blame.** If the root cause is an upstream failure
   (`RC-UPS`), identify the upstream skill but do not analyze its
   failure here — that is a separate entry.

---

## Steps

1. Read the skill's SKILL.md to extract the expected output format,
   rules, and required inputs.
2. Read the generated output (or note `NO_OUTPUT`).
3. If available, read the execution trace, reasoning audit, and
   compliance checkpoint entries for this skill.
4. Identify all deviations between expected and actual output.
5. For each deviation:
   a. Classify the failure type (from the Failure Type table).
   b. Examine the SKILL.md for ambiguity, conflicts, or missing
      constraints that could explain the failure.
   c. Check whether required inputs were available.
   d. Cross-reference with the reasoning audit to see if the agent
      misinterpreted instructions.
   e. Assign a root cause category.
   f. Rate severity.
   g. Draft a specific fix recommendation.
6. Write all failure entries to the output artifact.

---

## Output Artifact

File: `output/tmp/test_suite/0x_skill_failure_analysis.md`

### Output Format

```
# Skill Failure Analysis

## Summary
- Skills analyzed: <n>
- Total failures: <n>
- By severity: CRITICAL: <n>, MAJOR: <n>, MINOR: <n>
- Most common root cause: <code>

## Failure Entries

### Failure #<n>

Skill:
<skill_name>

Failure Type:
<type name> (<code>)

Severity:
<CRITICAL / MAJOR / MINOR>

#### Expected Output
> <relevant excerpt from SKILL.md output format spec>

#### Actual Output
> <relevant excerpt from generated output, or "NO_OUTPUT">

#### Deviation
<precise description of what differs>

#### Root Cause Analysis

Category:
<root cause name> (<code>)

Evidence:
- SKILL.md says: "<quote from SKILL.md>"
- Output shows: "<quote from output>"
- Trace shows: "<quote from execution trace, if available>"

Explanation:
<1-2 sentence analysis linking evidence to root cause>

#### Recommended Fix

Target:
<file to modify — e.g., ".agents/skills/<skill>/SKILL.md" or "apex-orchestrator-report">

Change:
<specific edit or addition, with before/after if applicable>

Impact:
<what this fix resolves>

---

(Repeat for each failure)

## Fix Priority Queue

| # | Skill | Failure | Severity | Root Cause | Fix Target |
|---|---|---|---|---|---|
| 1 | ... | ... | CRITICAL | ... | ... |
| 2 | ... | ... | MAJOR | ... | ... |
```
