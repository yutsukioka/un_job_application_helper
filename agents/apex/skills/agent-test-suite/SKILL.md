---
name: agent-test-suite
description: >-
  Run a controlled end-to-end diagnostic of the ApexStrategist pipeline.
  Executes the orchestrator workflow against a test context pack, records
  which skills were invoked and in what order, validates every output
  artifact for format compliance and content grounding, and produces a
  consolidated diagnostic report. Use this skill when debugging agent
  behaviour, validating skill wiring, or regression-testing after skill
  edits. This is a testing/diagnostic skill — it does not produce
  application documents.
---

# agent-test-suite

## Purpose

This skill performs a structured end-to-end test of the ApexStrategist
pipeline. It:

1. Verifies that `private/inputs/application_context.md` (or a designated test
   context pack) contains the required sections.
2. Simulates the `apex-orchestrator-report` Phase 1-7 workflow and
   records which skills are invoked, in what order, and what artifacts
   each produces.
3. Validates every generated artifact against the output format profiles
   defined in `apex-guardrails` (Profile A–F) and checks for source
   grounding, placeholder correctness, and keyword integrity.
4. Identifies failures: missing outputs, format violations, incorrect
   skill routing, and ungrounded claims.
5. Produces a single consolidated diagnostic report.

The goal is to give developers a concrete, actionable report explaining
what the agent did correctly and where it deviated.

## Diagnostic sources

The suite consolidates these diagnostic artifacts when available:

- `0x_agent_execution_graph.md`
- `0x_agent_reasoning_trace.md`
- `0x_agent_trace_log.md`
- `0x_skill_failure_analysis.md`

## Shared definitions

Reference the guardrails, format profiles, recursive quality loop, and
error handling patterns defined in `apex-guardrails`. This skill uses
format profile: `strategy_markdown`.

---

## Inputs

Required:

- `private/inputs/application_context.md` (or a test-specific context file) with
  at minimum these sections populated:
  - `USER_JOB_HISTORY_TEXT`
  - `JOB_DESCRIPTION_TEXT`
  - `JOB_REQUIREMENT_TEXT`
  - `TERM_EXTRACTOR`
  - `LIMITS` (including `TARGET_SYSTEM`)

Optional:

- `test_prompt`: a specific user request to route through the pipeline
  (default: "Run the full orchestrator report").
- `test_scope`: which phases to test — `full` (Phases 0-7, default),
  `phase8_only` (requires a prior strategy report), or a comma-separated
  list (e.g., `1,2,3`).

## Status codes

Use these final status codes exactly:

- `PASS`
- `PARTIAL`
- `FAIL`

---

## Rules

1. **Read-only on production outputs.** Do not overwrite files in
   `private/output/generated_documents/`. Write all diagnostic artifacts to
   `private/output/tmp/test_suite/`.
2. **No fabrication.** The test suite itself must not invent content. It
   only observes and validates what the pipeline produces.
3. **Use actual skills.** Do not reference skills that do not exist in
   `agents/apex/skills/`. The test validates the real skill registry.
4. **Delegate to sub-skills.** Use the four diagnostic sub-skills for
   their respective steps:
   - `agent-execution-tracer` for Step 3 (execution graph).
   - `agent-functionality-tester` for Step 4 (format compliance).
   - `agent-reasoning-auditor` for Step 5 (content grounding / reasoning).
   - `skill-failure-analyzer` for Step 6 (failure classification).
5. **Deterministic checks.** Format compliance and character-limit
   checks must use the same `capel-fit` scripts the production pipeline
   uses, not approximations.
6. **Fail fast on missing inputs.** If required context sections are
   absent, report the gap immediately and halt — do not attempt partial
   execution.

---

## Steps

### Step 1 — Context Validation

1. Read `private/inputs/application_context.md`.
2. Verify that each required section exists and is non-empty.
3. Record the section inventory in the diagnostic log:
   - Section name | Present (Y/N) | Approx length (chars)
4. If any required section is missing, record `FAIL: missing input` and
   stop.

### Step 2 — Skill Registry Snapshot

1. List all directories under `agents/apex/skills/` that contain a
   `SKILL.md`.
2. Record the skill name and first-line description for each.
3. Confirm that every skill referenced in `apex-orchestrator-report`
   Steps (Phases 0-7) exists in the registry.
4. Flag any missing skills as `FAIL: skill not found`.

### Step 3 — Pipeline Execution Trace

For each phase defined in `apex-orchestrator-report`:

1. Record the phase number and the skill(s) it should invoke.
2. Execute (or simulate execution of) the skill.
3. Capture:
   - Skill name invoked
   - Input sections consumed
   - Output artifact(s) produced (file name + location)
   - Execution status: `OK`, `WARN` (non-critical deviation), or
     `FAIL` (critical error)
4. If a phase depends on a prior phase's output, verify the dependency
   artifact exists before proceeding.

### Step 4 — Output Format Compliance

For every artifact produced in Step 3:

1. Determine the expected format profile from `apex-guardrails`
   (strategy_markdown for Phase 1-7 outputs).
2. Check:
   - Correct Markdown heading hierarchy (## for phase, ### for sub).
   - Bullet-point structure (no orphan bullets, consistent markers).
   - No chain-of-thought leakage (no scoring rubrics, no "Cycle N"
     text).
   - Placeholder syntax: all placeholders use `[bracketed text]`.
   - Character limits (if applicable): run `capel-fit` dry-run.
3. Record compliance per artifact:
   - Artifact | Profile | Checks Passed | Checks Failed | Details

### Step 5 — Content Grounding Audit

For each output artifact:

1. Sample up to 10 factual claims (dates, titles, metrics, tool names).
2. Verify each claim appears in `private/inputs/application_context.md`.
3. Flag ungrounded claims as `WARN: ungrounded claim — "<text>"`.
4. Check that no placeholders were silently filled with fabricated data.

### Step 6 — Failure Classification

Consolidate all `FAIL` and `WARN` items from Steps 1-5. Classify each:

| Category | Description |
|---|---|
| `INPUT_MISSING` | Required context section absent |
| `SKILL_NOT_FOUND` | Referenced skill does not exist |
| `ROUTING_ERROR` | Wrong skill invoked for a phase |
| `FORMAT_VIOLATION` | Output does not match expected profile |
| `GROUNDING_VIOLATION` | Claim not supported by inputs |
| `DEPENDENCY_BREAK` | Phase output missing for downstream phase |
| `CHAR_LIMIT_BREACH` | Output exceeds stated character limit |

### Step 7 — Diagnostic Report Generation

Write `private/output/tmp/test_suite/0x_agent_diagnostic_report.md` with these
sections:

---

## Output Artifact

File: `private/output/tmp/test_suite/0x_agent_diagnostic_report.md`

### Report Structure

```
# Agent Diagnostic Report
## Test Parameters
- Context file: <path>
- Test scope: <full | phase8_only | specific phases>
- Date: <ISO date>
- Target system: <from LIMITS>

## 1. Context Validation Summary
| Section | Present | Length (chars) |
|---|---|---|

## 2. Skill Registry Check
| Skill | Exists | Referenced By |
|---|---|---|

## 3. Execution Trace
Source: `0x_agent_execution_graph.md` (produced by `agent-execution-tracer`)
| Phase | Skill(s) Invoked | Artifacts Produced | Status |
|---|---|---|---|

## 4. Format Compliance
Source: `0x_agent_trace_log.md` (produced by `agent-functionality-tester`)
| Artifact | Profile | Passed | Failed | Details |
|---|---|---|---|---|

## 5. Content Grounding & Reasoning
Source: `0x_agent_reasoning_trace.md` (produced by `agent-reasoning-auditor`)
| Artifact | Claims Sampled | Grounded | Ungrounded | Details |
|---|---|---|---|---|

## 6. Failure Summary
Source: `0x_skill_failure_analysis.md` (produced by `skill-failure-analyzer`)
| # | Category | Phase | Description | Severity |
|---|---|---|---|---|

## 7. Overall Verdict
<PASS | PARTIAL | FAIL>

## 8. Recommended Actions
- <numbered list of specific fixes>
```

---

## Verdict Criteria

- **PASS**: All steps complete with zero `FAIL` items and at most 2
  `WARN` items.
- **PARTIAL**: Pipeline completes but with 1+ `FAIL` or 3+ `WARN` items
  that do not block downstream phases.
- **FAIL**: Any `INPUT_MISSING`, `SKILL_NOT_FOUND`, or
  `DEPENDENCY_BREAK` that prevents pipeline completion.
