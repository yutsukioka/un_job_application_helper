---
name: agent-functionality-tester
description: >-
  Audit AI agent output compliance by comparing generated artifacts
  against the format specifications in their source SKILL.md files.
  Creates checkpoint entries recording compliance status (PASS / PARTIAL
  / FAIL) for each validated artifact. Use this skill during pipeline
  testing or when debugging format deviations. This is a diagnostic skill
  — it does not produce application documents.
---

# agent-functionality-tester

## Purpose

This skill validates that an artifact produced by any ApexStrategist
skill conforms to the output format defined in that skill's SKILL.md. It
operates as a checkpoint auditor: given a skill name and its generated
output, it extracts the expected format specification, performs structural
comparison, and records a compliance verdict.

It can run:
- **Standalone** — to validate a single skill's output.
- **As a sub-module of `agent_test_suite`** — called once per artifact
  during Step 4 (Output Format Compliance).

## Shared definitions

Reference the output format profiles (A–F) and guardrails defined in
`apex-guardrails`. This skill's own output uses format profile:
`strategy_markdown`.

---

## Inputs

Required:

- `skill_name`: the name of the skill whose output is being tested
  (must match a directory under `.agents/skills/`).
- `generated_output`: the actual text output produced by the skill
  (inline text or file path under `output/`).

Optional:

- `skill_md_override`: path to a specific SKILL.md if different from
  `.agents/skills/<skill_name>/SKILL.md`.
- `format_profile_override`: force a specific format profile (A–F)
  instead of inferring from the SKILL.md.
- `checkpoint_number`: sequential checkpoint ID when called in a batch
  by `agent_test_suite` (default: 1).

---

## Rules

1. **Read-only.** This skill reads SKILL.md files and generated output;
   it never modifies them.
2. **Specification-driven.** Every check must trace back to a concrete
   rule in the target skill's SKILL.md or in `apex-guardrails` format
   profiles. Do not invent format expectations.
3. **No content judgement.** This skill checks structure and format
   only — not factual accuracy or quality. Content grounding is handled
   by `agent-reasoning-auditor`.
4. **Deterministic verdicts.** Use PASS / PARTIAL / FAIL consistently:
   - **PASS**: all required sections present, format fully matches spec.
   - **PARTIAL**: output is recognizable but has minor deviations (e.g.,
     missing optional section, slightly different heading level).
   - **FAIL**: required sections missing, wrong format profile used, or
     structural errors that would break downstream consumption.

---

## Validation Checks

### V1: Required Sections

Extract the list of required output sections from the skill's SKILL.md
`## Output Format` or `## Output Artifact` block. Verify each section
heading appears in the generated output.

| Check | PASS | PARTIAL | FAIL |
|---|---|---|---|
| All required sections present | All found | 1 missing, rest found | 2+ missing |

### V2: Heading Hierarchy

Verify Markdown heading levels match the specification (e.g., `##` for
top-level sections, `###` for sub-sections).

| Check | PASS | PARTIAL | FAIL |
|---|---|---|---|
| Heading levels correct | Exact match | Off by 1 level | Flat text / no headings |

### V3: Table Structure

If the format spec includes tables:
- Column count matches.
- Header row present.
- Separator row present (`|---|`).
- Data rows non-empty.

### V4: Bullet Formatting

If the format spec uses bullets:
- Consistent bullet marker (`-` or `*`, not mixed).
- No orphan bullets (bullet without preceding heading or context).
- Nested bullets indented correctly.

### V5: Placeholder Syntax

All placeholders use the `[bracketed text]` convention defined in
`apex-guardrails`. Flag:
- Angle-bracket placeholders (`<text>`) in final output (acceptable in
  templates, not in generated content).
- Unresolved `TODO` or `FIXME` markers.

### V6: Format Profile Compliance

Determine the applicable format profile (A–F from `apex-guardrails`)
and verify:
- Profile A/B (field strict): single paragraph, no internal line breaks,
  ASCII punctuation, no bullets.
- Profile C (IOM RA): headings and hyphen bullets allowed.
- Profile D/E (document): line breaks and plain-text headings allowed.
- Profile F (strategy markdown): Markdown headings and bullets allowed.

### V7: Character Limit (if applicable)

If the SKILL.md specifies a character limit or references `capel-fit`:
- Measure the output length (characters including spaces).
- Compare against the stated target band.
- Record over/under and delta.

---

## Steps

1. Read `.agents/skills/<skill_name>/SKILL.md` to extract:
   a. The `## Output Format` or `## Output Artifact` section.
   b. The stated or implied format profile.
   c. Any character limit references.
2. Read the `generated_output` (from file or inline).
3. Run validation checks V1–V7 in order.
4. For each check, record: check ID, expected value, actual value,
   verdict (PASS/PARTIAL/FAIL), and details.
5. Compute overall compliance status:
   - All checks PASS → overall PASS.
   - Any FAIL → overall FAIL.
   - No FAIL but 1+ PARTIAL → overall PARTIAL.
6. Write the checkpoint entry to the output artifact.

---

## Output Artifact

File: `output/tmp/test_suite/0x_agent_trace_log.md`

When called multiple times (e.g., by `agent_test_suite`), each
invocation appends a new checkpoint entry.

### Output Format

```
## CHECKPOINT <checkpoint_number>

Skill Tested:
<skill_name>

Format Profile:
<profile letter and name>

### Validation Results

| Check | Expected | Actual | Verdict | Details |
|---|---|---|---|---|
| V1: Required Sections | <list> | <found/missing> | PASS/PARTIAL/FAIL | <specifics> |
| V2: Heading Hierarchy | <spec> | <actual> | PASS/PARTIAL/FAIL | <specifics> |
| V3: Table Structure | <spec> | <actual> | PASS/PARTIAL/FAIL | <specifics> |
| V4: Bullet Formatting | <spec> | <actual> | PASS/PARTIAL/FAIL | <specifics> |
| V5: Placeholder Syntax | [bracketed] | <actual> | PASS/PARTIAL/FAIL | <specifics> |
| V6: Profile Compliance | <profile rules> | <actual> | PASS/PARTIAL/FAIL | <specifics> |
| V7: Character Limit | <limit or N/A> | <actual length> | PASS/PARTIAL/FAIL | <delta> |

### Overall Compliance
<PASS / PARTIAL / FAIL>

### Recommended Fixes (if PARTIAL or FAIL)
- <specific fix per failed check>
```
