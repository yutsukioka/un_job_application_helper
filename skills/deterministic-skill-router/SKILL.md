---
name: deterministic-skill-router
description: >-
  Route user requests to the correct ApexStrategist skill using an explicit
  rule table rather than relying solely on semantic similarity. Logs the
  routing decision with matched rule, confidence, and fallback path. Use
  this skill to diagnose routing errors, validate skill selection, or
  pre-route a batch of test prompts. This is a diagnostic/validation skill.
---

# deterministic-skill-router

## Purpose

This skill resolves which ApexStrategist skill should handle a given user
request by applying a deterministic rule table first, then falling back to
semantic matching only when no rule fires. It produces a routing log that
records the decision path, enabling developers to verify correct skill
selection and diagnose mis-routing.

## Shared definitions

Reference the guardrails and error handling patterns defined in
`apex-guardrails`. Output format profile: `strategy_markdown`.

---

## Inputs

Required:

- `user_request`: the user's natural-language request or prompt text.

Optional:

- `skill_registry`: override list of available skills (default: scan
  `.agents/skills/*/SKILL.md`).
- `batch_mode`: if `true`, accept a list of requests and route each.

---

## Routing Rule Table

Rules are evaluated top-down; first match wins. Patterns are
case-insensitive substring or keyword matches against the user request.

| # | Pattern (any of) | Routed Skill | Notes |
|---|---|---|---|
| 1 | "build context", "context pack", "assemble inputs" | `apex-build-context-pack` | Must run before analysis |
| 2 | "term extract", "keyword extract", "five terms" | `term-extractor` | Phase 0 prerequisite |
| 3 | "keyword bank", "20-40 phrases", "expanded keywords" | `apex-jd-keyword-bank` | Optional complement to term-extractor |
| 4 | "ccog", "occupational group", "ICSC" | `apex-ccog-resolver` | Phase 1 CCOG resolution |
| 5 | "core requirement", "top 5", "knockout criteria" | `apex-jd-core-requirements` | Phase 1.2 |
| 6 | "evidence bank", "evidence map", "gap mitigation" | `apex-candidate-evidence-bank` | Phase 1.3 |
| 7 | "progression", "promotion chain", "metric ledger" | `apex-progression-metric-ledger` | Pre-generation utility |
| 8 | "strategy report", "full report", "phase 1-7", "orchestrator" | `apex-orchestrator-report` | Full pipeline orchestrator |
| 9 | "headline", "summary line" | `apex-headline-summary` | Phase 2.1 |
| 10 | "keyword insertion", "keyword placement", "must-use phrases" | `apex-keyword-insertion-map` | Phase 2.2 |
| 11 | "bullet enhance", "rewrite bullet", "action verb" | `apex-bullet-enhancer` | Phase 2.3 |
| 12 | "star stor", "star blueprint", "situation task action" | `apex-star-story-blueprints` | Phase 3 |
| 13 | "uvp", "unique value", "value proposition", "branding" | `apex-uvp-statement` | Phase 4 |
| 14 | "cover letter pointer", "cover letter tip", "cover letter guid" | `apex-cover-letter-pointers` | Phase 5 strategy only |
| 15 | "impression", "tone", "polish" | `apex-impression-tips` | Phase 6 |
| 16 | "coaching", "reflection question" | `apex-coaching-reflection` | Phase 7 |
| 17 | "feedback", "user revision", "phase 7.5" | `apex-user-feedback-revision` | Phase 7.5 |
| 18 | "admin profile" + NOT "iom" + NOT "ra split" | `apex-generate-admin-profile` | Phase 8 Option 1 |
| 19 | "cv", "résumé", "resume" + NOT "cover letter" | `apex-generate-cv` | Phase 8 Option 2 |
| 20 | "cover letter" + NOT "pointer" + NOT "tip" | `apex-generate-cover-letter` | Phase 8 Option 3 |
| 21 | "qualification answer", "screening question" | `apex-generate-qualification-answers` | Phase 8 Option 4 |
| 22 | "iom", "ra split", "responsibilities and achievements" | `apex-generate-admin-profile-ra-split` | Phase 8 Option 5 |
| 23 | "competency map", "skills per job", "relevance score" | `apex-generate-competency-mapping` | Phase 8 Option 6 |
| 24 | "motivation statement", "inspira motivation" | `apex-generate-motivation-statement` | Phase 8 Option 7 |
| 25 | "dra split", "duties responsibilities achievements", "duties, responsibilities, and achievements", "ats dra" | `apex-generate-admin-profile-dra-split` | Phase 8 Option 8 |
| 26 | "lint", "format check", "paste-ready" | `apex-output-lint` | Post-generation utility |
| 27 | "capel", "character fit", "char limit" | `capel-fit` | Post-generation utility |
| 28 | "placeholder", "missing info", "unresolved" | `place-holder-checker` | Post-generation utility |
| 29 | "audit", "application review" | `apex-application-audit` | Post-generation review |
| 30 | "cross-doc", "consistency check" | `apex-cross-doc-consistency` | Post-generation review |
| 31 | "test suite", "diagnostic", "pipeline test" | `agent-test-suite` | Testing skill |
| 32 | "trace log", "output compliance", "checkpoint audit" | `agent-functionality-tester` | Testing sub-skill |
| 33 | "execution graph", "execution trace", "skill invocation log" | `agent-execution-tracer` | Testing sub-skill |
| 34 | "reasoning audit", "reasoning trace", "instruction interpretation" | `agent-reasoning-auditor` | Testing sub-skill |
| 35 | "failure analysis", "root cause", "skill failure" | `skill-failure-analyzer` | Testing sub-skill |

### Compound-pattern rules (rows 18-25)

When the request matches a primary keyword but also contains a
disqualifying keyword, the rule does not fire and evaluation continues
to the next row.

### Fallback

If no rule fires:
1. Compare the request text against each skill's `description:` field
   in its YAML frontmatter using keyword overlap.
2. Select the skill with the highest overlap score.
3. If the highest score is below 2 keyword matches, return
   `NO_MATCH` and recommend the user clarify their request.

---

## Rules

1. **One skill per request.** Return exactly one routed skill (or
   `NO_MATCH`). Do not chain skills — that is the orchestrator's job.
2. **Log every decision.** Even when routing is obvious, write the full
   log entry so the trace is auditable.
3. **No side effects.** This skill only routes — it does not execute the
   target skill.
4. **Batch mode.** When `batch_mode` is true, produce one log entry per
   request in sequence.

---

## Steps

1. Read the user request (or list of requests in batch mode).
2. Normalize the request to lowercase for pattern matching.
3. Evaluate the routing rule table top-down (rows 1-35).
4. If a rule fires, record the match and proceed to Step 6.
5. If no rule fires, run the fallback keyword-overlap comparison against
   skill descriptions. Record the top 3 candidates with scores.
6. Write the routing log entry.
7. If batch mode, repeat Steps 2-6 for each request.

---

## Output Artifact

File: `output/tmp/0x_skill_routing_log.md`

### Output Format (per request)

```
## Routing Entry #<n>

User Request:
<original text>

Normalized Request:
<lowercased text>

Matched Rule:
<rule # and pattern, or "FALLBACK" or "NO_MATCH">

Skill Selected:
<skill name>

Routing Method:
deterministic-rule | semantic-fallback | no-match

Confidence:
high (deterministic match) | medium (fallback top score >= 3) | low (fallback top score < 3)

Fallback Candidates (if applicable):
1. <skill> — score: <n>
2. <skill> — score: <n>
3. <skill> — score: <n>
```
