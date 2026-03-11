---
name: apex-progression-metric-ledger
description: >-
  Identify same-organization promotion chains, extract quantitative and scope
  evidence across related roles, classify additivity and semantic verb safety,
  and output a reusable Promotion-Group Metric Ledger for downstream evidence
  ranking, qualification answers, CVs, and consistency checks.
---

# apex-progression-metric-ledger

## Purpose

This skill normalizes multiple roles within the same organization into
**promotion chains** when the evidence suggests career progression or
scope expansion. It is an analytical skill, not a final document
generator.

Its goals are to:

1. Detect same-organization role sequences that should be treated as a
   single progression narrative for evidence selection.
2. Extract explicit quantitative and scope signals across the chain
   (budgets, funding, projects, beneficiaries, partners, team size,
   geographies, etc.).
3. Classify whether those metrics are safely additive, partially
   additive, non-additive, or uncertain.
4. Preserve action-verb semantics so downstream outputs do not inflate
   `oversaw`, `governed`, or `audited` into `managed` without support.
5. Produce reusable fragments for qualification answers and other
   outputs.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.
Output format profile: `strategy_markdown`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`

Optional (recommended for better ranking and scope interpretation):

- `JOB_DESCRIPTION_TEXT`
- `JOB_REQUIREMENT_TEXT`
- `JOB_QUALIFICATION_QUESTIONS`
- `apex-jd-core-requirements` output
- `apex-candidate-evidence-bank` output

## Output format

Return exactly these sections:

## Promotion Groups Identified

For each same-organization chain found, output:
- **Promotion group:** `<Org short label + dates>`
- **Organization:** `<organization name>`
- **Role progression:** `<oldest role -> newest role>`
- **Highest relevant role:** `<role title>`
- **Why grouped:** `<contiguous dates / title progression / scope expansion / overlapping evidence>`
- **Use downstream:** `<qualification answers / evidence bank / CV / all>`

If no qualifying promotion chain exists, write:
- `No same-organization promotion chains identified from the provided inputs.`

## Promotion-Group Metric Ledger

For each promotion group, output:

- **Promotion group:** `<same value as above>`
- **Narrative anchor role:** `<highest-seniority relevant role>`
- **Progression summary:** `<one concise sentence>`
- **Metric objects:**
  - `<dimension> | <verb> | <value> | <unit> | <period> | <scope label> | <role source> | <additivity class> | <aggregation confidence>`
- **Safe roll-ups:**
  - `<dimension label>: <rolled-up value OR DO NOT SUM> | <reason>`
- **Reusable qualification-answer fragment:** `<1 sentence>`
- **Reusable admin/CV fragment:** `<1 sentence>`

## Unresolved Aggregation Questions

- List any places where:
  - scope overlap is unclear,
  - time overlap may cause double counting,
  - units differ,
  - the same portfolio may have been restated at a higher level,
  - or verb semantics prevent a clean roll-up.
- Use bracketed placeholders for what the user should confirm.

## Additivity classes (use exactly these labels)

- `EXACT_ADD` = distinct metrics covering separate items or clearly non-overlapping periods/scopes.
- `PERIOD_ADD` = separate annual / fiscal-year figures from the same stream that can be added.
- `DISTINCT_SCOPE_ADD` = different portfolios/programmes within the same organization and same dimension.
- `NONADDITIVE_RESTATE` = later role appears to restate or subsume earlier scope.
- `OVERLAP_UNCERTAIN` = possible overlap; do not sum.
- `SEMANTIC_MIXED` = metrics may be aggregated only under a generalized label
  such as `programme/funding accountability`, not under the strongest action verb.

## Rules

- Extract only metrics and claims explicitly present in the source text.
  Do not infer values, currencies, project counts, or time spans.
- Group roles into a promotion chain only when the organization is the
  same and at least one of the following is true:
  - dates are contiguous or nearly contiguous,
  - titles show seniority progression,
  - later role clearly expands scope over earlier role,
  - or narrative text explicitly indicates promotion / role change.
- The **narrative anchor role** must be the highest-seniority role that
  is directly relevant to the target requirement or qualification
  question.
- Preserve verb semantics in both metric objects and reusable fragments.
- Do not sum metrics across overlapping periods or restated scope.
- If the evidence is insufficient to decide additivity, output `DO NOT SUM`
  and explain why.
- If action verbs differ across metrics but the scope is still safely
  combinable, use a generalized label such as `programme/funding accountability`
  in the roll-up rather than overstating direct management.

## Steps

1. Parse `USER_JOB_HISTORY_TEXT` into roles with organization, title,
   dates, and narrative evidence.
2. Detect same-organization promotion chains using organization match,
   chronology, title signals, and scope expansion cues.
3. Extract explicit quantitative and scope evidence from each role.
4. Normalize each metric into a `metric object` with:
   - dimension,
   - verb,
   - value,
   - unit,
   - period,
   - scope label,
   - role source.
5. Classify each metric object's additivity using the six required labels.
6. Determine the narrative anchor role for each promotion group.
7. Compute safe roll-ups only where the classification supports it.
8. Draft one reusable qualification-answer fragment and one reusable
   admin/CV fragment per promotion group.
9. Output unresolved questions as placeholders instead of guessing.
