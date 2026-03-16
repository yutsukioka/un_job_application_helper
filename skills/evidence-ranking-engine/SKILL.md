---
name: evidence-ranking-engine
description: >-
  Rank candidate evidence items by strength before downstream generation.
  Scores each evidence snippet from the candidate-evidence-bank on three
  axes — responsibility level, requirement relevance, and quantified
  impact — and produces a prioritized evidence selection log. Use this
  skill between Phase 1.3 (evidence bank) and Phase 8 (document
  generation) to ensure the strongest evidence is surfaced first. This is
  an analytical/testing skill — it does not produce application documents.
---

# evidence-ranking-engine

## Purpose

This skill takes the evidence inventory produced by
`apex-candidate-evidence-bank` and assigns a composite strength score to
each snippet. The ranked output enables downstream generation skills
(`apex-generate-admin-profile`, `apex-generate-cv`,
`apex-generate-qualification-answers`, etc.) to prioritize the most
compelling evidence when space is limited.

## Shared definitions

Apply the guardrails (source-grounded only, placeholders over guessing)
and error handling patterns defined in `apex-guardrails`. Output format
profile: `strategy_markdown`.

---

## Inputs

Required:

- Output from `apex-candidate-evidence-bank` — specifically the
  `## Evidence Bank by Requirement` section containing per-requirement
  evidence snippets tagged by role.
- `USER_JOB_HISTORY_TEXT` (from `inputs/application_context.md`) — used
  to verify role hierarchy and dates.

Optional:

- `TERM_EXTRACTOR` — starred terms for relevance weighting.
- `JD_KEYWORD_BANK` — expanded keyword bank for relevance matching.
- Output from `apex-jd-core-requirements` — requirement star weights
  (★ to ★★★) for requirement-importance multiplier.

---

## Scoring Methodology

Each evidence snippet receives three sub-scores on a 0-100 scale.

### A. Responsibility Level Score (0-100)

Measures the seniority and decision-making scope of the role from which
the evidence originates.

| Indicator | Score Range |
|---|---|
| Strategic/executive role (P-5+, Director, Head of Unit) | 80-100 |
| Mid-level management (P-3/P-4, Programme Officer, Team Lead) | 50-79 |
| Technical/specialist without supervisory scope | 30-49 |
| Support/entry-level role (G-level, Intern, Junior Consultant) | 0-29 |

Adjustments:
- +10 if the evidence describes supervision of staff or budget authority.
- +5 if the evidence describes cross-functional or multi-agency coordination.

### B. Requirement Relevance Score (0-100)

Measures how directly the evidence addresses the specific core
requirement it is mapped to.

| Indicator | Score Range |
|---|---|
| Direct match: same competency, same domain, same context | 80-100 |
| Strong transferable: same competency in a related domain | 50-79 |
| Partial match: related competency or partial domain overlap | 20-49 |
| Tangential: loosely related or requires significant framing | 0-19 |

Adjustments:
- +10 if the evidence uses exact JD language or ★★★ TERM_EXTRACTOR terms.
- +5 if the evidence aligns with JD_KEYWORD_BANK phrases.

### C. Quantified Impact Score (0-100)

Measures whether the evidence includes concrete, verifiable outcomes.

| Indicator | Score Range |
|---|---|
| Specific numeric metric (%, $, #, time saved, caseload) | 80-100 |
| Quantified scope (team size, budget range, geographic reach) | 50-79 |
| Qualitative outcome with tangible result description | 20-49 |
| No measurable outcome stated | 0-19 |

Adjustments:
- +10 if the metric directly maps to a JD-stated deliverable or KPI.
- Snippets containing `[User to Insert Specific Metric]` placeholders
  score 10 in this category (potential but unconfirmed).

### Composite Score

```
Composite = (A × 0.25) + (B × 0.50) + (C × 0.25)
```

Relevance is weighted double because requirement alignment is the
primary selection criterion for screening panels.

### Requirement-Importance Multiplier (optional)

If requirement star weights are available from `apex-jd-core-requirements`:

- ★★★ requirement: Composite × 1.0 (no change).
- ★★ requirement: Composite × 0.9.
- ★ requirement: Composite × 0.8.

This ensures evidence for critical requirements surfaces higher in the
overall ranking.

---

## Rules

1. **Source-grounded only.** Score only evidence snippets that exist in
   the candidate-evidence-bank output. Do not invent or infer evidence.
2. **No score inflation.** If a snippet lacks quantified impact, score
   it honestly — do not round up to make it look stronger.
3. **Placeholders count as gaps.** Evidence containing unresolved
   placeholders receives a maximum Quantified Impact score of 10.
4. **Tie-breaking.** When two snippets have the same composite score,
   prefer the one from the more recent role (by end date).
5. **Selection threshold.** Mark evidence as `Selected: yes` if its
   composite score >= 50, or if it is the highest-scoring snippet for a
   requirement that has no snippet >= 50.

---

## Steps

1. Read the `## Evidence Bank by Requirement` from
   `apex-candidate-evidence-bank` output.
2. Parse each requirement block and extract individual evidence snippets
   with their role tags.
3. For each snippet:
   a. Determine the role's level from `USER_JOB_HISTORY_TEXT` → score A.
   b. Compare the snippet text against the requirement, TERM_EXTRACTOR
      starred terms, and JD_KEYWORD_BANK phrases → score B.
   c. Check for numeric metrics, scope indicators, or qualitative
      outcomes → score C.
   d. Compute composite score.
4. Apply the requirement-importance multiplier if star weights are
   available.
5. Sort all snippets by composite score descending.
6. Apply the selection threshold rule.
7. Write the output artifact.

---

## Output Artifact

File: `output/tmp/0x_evidence_selection_log.md`

### Output Format

```
# Evidence Ranking Log

## Summary
- Total evidence snippets scored: <n>
- Selected (score >= 50 or best-for-requirement): <n>
- Unselected: <n>
- Requirements with no strong evidence: <list>

## Rankings by Requirement

### Requirement: <requirement text> [★★★]

| # | Role Tag | Evidence Snippet | Resp (A) | Relev (B) | Impact (C) | Composite | Selected |
|---|---|---|---|---|---|---|---|
| 1 | <role> | <snippet text> | <0-100> | <0-100> | <0-100> | <0-100> | yes/no |
| 2 | ... | ... | ... | ... | ... | ... | ... |

(Repeat for each requirement)

## Global Top 10 (across all requirements)

| # | Requirement | Role Tag | Snippet | Composite | Selected |
|---|---|---|---|---|---|

## Evidence Gaps
- <requirement>: no snippet scored above 50. Best available: <snippet> (score: <n>).
```
