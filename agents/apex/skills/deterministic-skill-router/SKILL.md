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
  `agents/apex/skills/*/SKILL.md`).
- `batch_mode`: if `true`, accept a list of requests and route each.

---

## Routing Rule Table

Evaluate the IOM ATS operation gate first, then the named UNESCO/UNOPS gate, then the Option 9 requested-deliverable
gate below before the general table. If Option 9 passes, select rule 18 immediately. Otherwise skip rule 18 and
evaluate the remaining rules top-down; first match wins. General-table
patterns are case-insensitive substring or keyword matches.

### IOM ATS requested-operation gate (rule I1)

Route to `apex-iom-ats-input` when the requested operation is to inspect/read
an IOM WAVE/Oracle application, extract its vacancy/questions, compare a live
extraction with the context pack, map its form fields, or enter approved content
into its draft. An explicit invocation also matches. Determine the requested
operation before source clauses; mentioning a CV or Option 5 as the input does
not change an ATS-entry request into document generation. Do not trigger on
the word IOM alone, on document-generation requests such as "Generate an IOM
RA profile", or on requests to create/install/explain the skill itself.
If this gate fails, exclude `apex-iom-ats-input` from fallback keyword matching.

### Named UNESCO/UNOPS requested-output gate (rules U1–U3, N10)

Use [scripts/route_named_output.py](scripts/route_named_output.py) as the
deterministic precheck. It returns MATCH, DEFER, or NO_MATCH with a reason.
MATCH selects the named skill immediately; DEFER continues to Option 9 and
the existing table. NO_MATCH is final for this content-generation router:
do not let keyword fallback turn research, explanation, installation or status
questions about the skills into applicant-content generation.

| Rule | Requested output | Skill |
|---|---|---|
| U1 | Prepare/fill UNESCO Employment History Form or EHF | `apex-generate-unesco-employment-history` |
| U2 | Select/recommend/review Domain of Expertise entries | `apex-select-domain-of-expertise` |
| U3 | Review/curate/prepare publications or bibliography | `apex-curate-publications` |
| N10 | Prepare/review UNOPS application fit, Position Areas, exact skills, role/skills companion, or Phase 8 Option 10 | `apex-unops-application-fit` |

UNOPS CV/letter/context requests retain their requested-output routes; the shared
UNOPS guardrails hook prepares their fit plan after selection. Do not route a
request to install/adapt the skill or its package into applicant generation.

Explicit requested invocation of a named skill also matches. Determine the
requested target before source clauses such as "from", "using", "based on"
or "against". "Review my publications using my CV" is U3; "Update my CV
publication section" continues to the CV route. Context-pack and strategy
requests retain their own routes. If this gate returns DEFER, exclude the
named skills from fallback keyword matching, as with a failed Option 9
gate. The target may remain ambiguous rather than inventing a requested output.

### Option 9 requested-deliverable gate (rule 18)

This gate distinguishes the requested output from material supplied as input:

1. Normalize case and whitespace. Ignore an initial polite request prefix
   such as "please", "can you", "could you", or "would you".
2. Accept an explicit invocation or selection of `apex-generate-skills-proficiency`,
   `Option 9`, `Option9`, or `Phase 8 Option 9`. These must be the requested
   operation, either a standalone selection or the target immediately after
   "generate", "create", "make", "prepare", "draft", "produce", "regenerate", "run",
   "invoke", "select", or "use". Allow an optional "$" before the skill name.
3. Also accept "generate", "create", "make", "prepare", "draft", "produce",
   "regenerate", "update", or "recommend" when the requested target begins
   with one of the following (ignore an initial "a", "the", or "my", and
   an optional "World Bank" or "WB" qualifier):
   - "Skill / Certification / Language" (spaces around slashes optional);
   - "skills and proficiency" or "skills/proficiency";
   - "skill proficiency entries", "skills proficiency entries",
     "language proficiency entries", or "certification proficiency entries";
   - "skill entries", "skills entries", "skill list", or "skills list",
     with "proficiency" explicitly requested in that target.
4. Determine the target before source clauses introduced by "from",
   "based on", "using", "with evidence from", or "against". A CV, context
   pack, strategy report, or competency map mentioned only in a source clause
   does not override the Option 9 target.
5. Do not pass this gate merely because "proficiency" or Option 9 is
   mentioned. Research/explanation requests, and requests whose target is a
   CV, cover letter, context pack, strategy report, or competency map, do not
   become Option 9 generation requests. If the gate fails, exclude
   `apex-generate-skills-proficiency` from fallback keyword matching too.

Examples:

| Request | Gate result / routing |
|---|---|
| "Generate Option9 from my context pack" | Pass: Option 9, before context-builder matching |
| "Create Phase 8 Option 9 using my strategy report" | Pass: Option 9, before orchestrator matching |
| "Prepare Skill / Certification / Language entries from my CV" | Pass: Option 9 |
| "Recommend my skills list with proficiency based on my competency map" | Pass: Option 9 |
| "Update my CV language proficiency" | Fail: continue to the CV rule |
| "Research World Bank proficiency definitions" | Fail: no Option 9 match, including fallback |
| "Explain Option 9" | Fail: no Option 9 generation match |
| "Build a context pack for Option 9" | Fail: continue to the context-builder rule |
| "Create a competency map with proficiency notes" | Fail: continue to the competency-map rule |

### General table

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
| 18 | Option 9 requested-deliverable gate above passes | `apex-generate-skills-proficiency` | Evaluate before row 1; skip entirely when the gate fails |
| 19 | "admin profile" + NOT "iom" + NOT "ra split" | `apex-generate-admin-profile` | Phase 8 Option 1 |
| 20 | "cv", "résumé", "resume" + NOT "cover letter" | `apex-generate-cv` | Phase 8 Option 2 |
| 21 | "cover letter" + NOT "pointer" + NOT "tip" | `apex-generate-cover-letter` | Phase 8 Option 3 |
| 22 | "qualification answer", "screening question" | `apex-generate-qualification-answers` | Phase 8 Option 4 |
| 23 | "iom", "ra split", "responsibilities and achievements" | `apex-generate-admin-profile-ra-split` | Phase 8 Option 5 |
| 24 | "competency map", "skills per job", "relevance score" | `apex-generate-competency-mapping` | Phase 8 Option 6 |
| 25 | "motivation statement", "inspira motivation" | `apex-generate-motivation-statement` | Phase 8 Option 7 |
| 26 | "dra split", "duties responsibilities achievements", "duties, responsibilities, and achievements", "ats dra" | `apex-generate-admin-profile-dra-split` | Phase 8 Option 8 |
| 27 | "lint", "format check", "paste-ready" | `apex-output-lint` | Post-generation utility |
| 28 | "capel", "character fit", "char limit" | `capel-fit` | Post-generation utility |
| 29 | "placeholder", "missing info", "unresolved" | `place-holder-checker` | Post-generation utility |
| 30 | "audit", "application review" | `apex-application-audit` | Post-generation review |
| 31 | "cross-doc", "consistency check" | `apex-cross-doc-consistency` | Post-generation review |
| 32 | "test suite", "diagnostic", "pipeline test" | `agent-test-suite` | Testing skill |
| 33 | "trace log", "output compliance", "checkpoint audit" | `agent-functionality-tester` | Testing sub-skill |
| 34 | "execution graph", "execution trace", "skill invocation log" | `agent-execution-tracer` | Testing sub-skill |
| 35 | "reasoning audit", "reasoning trace", "instruction interpretation" | `agent-reasoning-auditor` | Testing sub-skill |
| 36 | "failure analysis", "root cause", "skill failure" | `skill-failure-analyzer` | Testing sub-skill |

### Compound-pattern rules (rows 19-26)

When the request matches a primary keyword but also contains a
disqualifying keyword, the rule does not fire and evaluation continues
to the next row.

### Fallback

If no rule fires:
1. Exclude `apex-generate-skills-proficiency` when its requested-deliverable
   gate failed, and the named UNESCO/UNOPS skills when their gate deferred.
   A NO_MATCH from the named gate never reaches this fallback.
   Compare the request text against the remaining skills'
   `description:` fields in YAML frontmatter using keyword overlap.
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
3. Evaluate the IOM ATS operation gate first; on match select I1. Otherwise
   evaluate the named UNESCO/UNOPS gate. On MATCH select U1/U2/U3/N10; on NO_MATCH
   record no-match and stop. On DEFER evaluate Option 9. If it passes,
   select rule 18; otherwise skip rule 18 and evaluate the general table
   top-down (rows 1-17, then 19-36).
4. If a rule fires, record the match and proceed to Step 6.
5. If no rule fires, run the fallback keyword-overlap comparison against
   skill descriptions. Record the top 3 candidates with scores.
6. Write the routing log entry.
7. If batch mode, repeat Steps 2-6 for each request.

---

## Output Artifact

File: `private/output/tmp/0x_skill_routing_log.md`

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
