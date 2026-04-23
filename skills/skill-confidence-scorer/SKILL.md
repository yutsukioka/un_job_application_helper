---
name: skill-confidence-scorer
description: >-
  Evaluate how confidently a skill selection matches a user request by
  scoring relevance, instruction alignment, and context compatibility.
  Produces a ranked confidence report for all candidate skills. Use this
  skill to diagnose ambiguous routing, compare skill candidates, or
  validate that deterministic-skill-router selected the best match. This
  is a diagnostic/testing skill — it does not produce application documents.
---

# skill-confidence-scorer

## Purpose

This skill evaluates every registered ApexStrategist skill against a
given user request and produces a confidence score for each. Unlike
`deterministic-skill-router` (which applies hard rules), this scorer
uses multi-factor analysis to surface the best candidate even for
ambiguous or novel requests.

Use cases:
- Validate the `deterministic-skill-router` output: does the scored
  top candidate match the routed skill?
- Diagnose ambiguous requests where multiple skills could apply.
- Identify skill-registry gaps when no skill scores above the
  confidence threshold.

## Shared definitions

Reference the guardrails and error handling patterns defined in
`apex-guardrails`. Output format profile: `strategy_markdown`.

---

## Inputs

Required:

- `user_request`: the user's natural-language request or prompt text.

Optional:

- `skill_registry_override`: a subset of skills to score (default: all
  skills under `.agents/skills/*/SKILL.md`).
- `context_snapshot`: summary of currently available input files (to
  evaluate context compatibility). Default: scan
  `inputs/application_context.md` for populated sections.
- `router_output`: the `deterministic-skill-router` log entry for
  cross-validation.

---

## Scoring Methodology

Each candidate skill receives three sub-scores on a 0-100 scale.

### A. Relevance Score (0-100)

Measures semantic alignment between the user request and the skill's
stated purpose and description.

| Signal | Score Contribution |
|---|---|
| Exact keyword match (skill name appears in request) | +30 |
| JD-phase keyword match (e.g., "Phase 3" and skill is for Phase 3) | +20 |
| Verb-intent alignment (request verb matches skill's primary action: "generate", "analyze", "extract", "rank") | +20 |
| Domain-noun overlap (e.g., "cover letter", "evidence", "admin profile") | +20 |
| Partial or tangential overlap | +5-10 |
| No detectable overlap | 0 |

Cap at 100.

### B. Instruction Alignment Score (0-100)

Measures whether the skill's defined output and steps match what the
user is asking for.

| Signal | Score Contribution |
|---|---|
| Skill output type matches request (e.g., user wants a document and skill generates a document) | +40 |
| Skill output format matches implied need (e.g., user mentions "paste-ready" and skill uses `inspira_field_strict`) | +20 |
| Skill steps address the user's specific ask (e.g., user says "rank evidence" and skill steps include ranking) | +25 |
| Skill has constraints that conflict with the request (e.g., skill says "do not generate documents" but user wants a document) | -30 |
| Skill description explicitly says "use when <user's scenario>" | +15 |

Floor at 0, cap at 100.

### C. Context Compatibility Score (0-100)

Evaluates whether the required inputs for the skill are currently
available.

| Signal | Score Contribution |
|---|---|
| All required inputs present in context snapshot | 100 |
| Most required inputs present (1 missing) | 60-80 |
| Multiple required inputs missing | 20-40 |
| Critical input missing (e.g., no JOB_DESCRIPTION_TEXT for a JD-analysis skill) | 0-20 |
| Skill has no external input requirements | 90 (always compatible) |

### Composite Confidence Score

```
Confidence = (A × 0.45) + (B × 0.35) + (C × 0.20)
```

Relevance is weighted highest because correct skill identification
matters most. Context compatibility is weighted lowest because missing
inputs can be resolved.

---

## Confidence Thresholds

| Level | Score Range | Interpretation |
|---|---|---|
| HIGH | 75-100 | Strong match — safe to invoke |
| MEDIUM | 50-74 | Plausible match — review before invoking |
| LOW | 25-49 | Weak match — likely not the right skill |
| NO_MATCH | 0-24 | No meaningful alignment |

---

## Rules

1. **Score all candidates.** Do not short-circuit — always score every
   skill in the registry (or override set) so the full ranking is
   visible.
2. **Source-grounded scoring.** Base relevance scores on the skill's
   actual `SKILL.md` frontmatter and `## Purpose` section, not on
   assumptions about what the skill might do.
3. **Conflict penalty.** If a skill explicitly states "do not use for
   <scenario>" and the user's request matches that scenario, apply a
   -30 penalty to Instruction Alignment.
4. **Tie-breaking.** When two skills have the same composite score,
   prefer the one whose description is more specific (shorter, more
   targeted) over a generic/orchestrator skill.
5. **Cross-validation flag.** If `router_output` is provided and the
   router's selected skill differs from the scorer's top candidate,
   flag this as `ROUTING_MISMATCH` in the output.

---

## Steps

1. Read the user request.
2. Build the skill registry: for each `.agents/skills/*/SKILL.md`, read
   the YAML frontmatter (`name`, `description`) and the `## Purpose`
   section.
3. Build the context snapshot: scan `inputs/application_context.md` for
   populated sections (or use the provided `context_snapshot`).
4. For each candidate skill:
   a. Compute Relevance Score (A) — keyword/verb/noun matching.
   b. Compute Instruction Alignment Score (B) — output type, format,
      and step matching.
   c. Compute Context Compatibility Score (C) — required input
      availability.
   d. Compute Composite Confidence.
   e. Assign confidence level (HIGH/MEDIUM/LOW/NO_MATCH).
5. Sort candidates by Composite Confidence descending.
6. If `router_output` is provided, compare the router's selection
   against the top-scored candidate and flag mismatches.
7. Write the output artifact.

---

## Output Artifact

File: `output/tmp/0x_skill_confidence_scores.md`

### Output Format

```
# Skill Confidence Report

## Request
<original user request>

## Context Availability
| Input Section | Present |
|---|---|
| USER_JOB_HISTORY_TEXT | yes/no |
| JOB_DESCRIPTION_TEXT | yes/no |
| JOB_REQUIREMENT_TEXT | yes/no |
| TERM_EXTRACTOR | yes/no |
| LIMITS | yes/no |
| ... | ... |

## Top 5 Candidates

| Rank | Skill | Relevance (A) | Alignment (B) | Context (C) | Composite | Level |
|---|---|---|---|---|---|---|
| 1 | <skill> | <0-100> | <0-100> | <0-100> | <0-100> | HIGH |
| 2 | ... | ... | ... | ... | ... | ... |

## Recommended Skill
<skill name> — Confidence: <composite> (<level>)

## Router Cross-Validation (if applicable)
- Router selected: <skill>
- Scorer top candidate: <skill>
- Match: YES / ROUTING_MISMATCH
- Analysis: <one-sentence explanation if mismatch>

## Full Rankings

| Rank | Skill | Relevance | Alignment | Context | Composite | Level |
|---|---|---|---|---|---|---|
| 1 | ... | ... | ... | ... | ... | ... |
| ... | ... | ... | ... | ... | ... | ... |
| <n> | ... | ... | ... | ... | ... | NO_MATCH |

## Observations
- <any noteworthy patterns: skill gaps, ambiguous requests, near-ties>
```
