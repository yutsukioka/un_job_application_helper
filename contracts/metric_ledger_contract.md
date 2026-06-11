# metric_ledger.md Contract

## Purpose

`metric_ledger.md` is the team's early shared source of truth for
role-scoped metrics, approved aggregates, key requirement weights, and
critical decisions that later outputs must reuse consistently.

This artifact exists to prevent:
- cross-document metric drift
- accidental reuse of a number in the wrong role or period
- silent aggregation of unrelated figures
- downstream contradiction between strategy and final documents

---

## Creation timing

Create `metric_ledger.md` in **Round 1** immediately after core requirement
extraction and before downstream strategy or Phase 8 document generation.

Default owner: `screening-lead`
Default validator: `qa-auditor`

---

## Consumers

All later stages must use the ledger:

- `technical-lead` for CCOG and register framing
- `screening-lead` for Phase 1-7 strategy
- `ats-format-lead` for Option 1 / 2 / 3 / 4 generation
- `qa-auditor` for cross-document validation
- independent evaluators for evidence and contradiction checks

---

## Update rules

1. The ledger is **canonical** once validated.
2. Corrections are allowed, but only with an explicit change-log entry.
3. Downstream documents must not silently override the ledger.
4. If a downstream document and the ledger disagree, the document is wrong
   unless the ledger is explicitly corrected.
5. Do not aggregate role-specific metrics unless the source explicitly
   supports the aggregate.

---

## Required structure

## 1. Metadata

Include:
- `JOB_SLUG`
- `TARGET_SYSTEM`
- `VACANCY_TYPE`
- `FUNCTIONAL_REGISTER`
- `CLASSIFICATION_SOURCE`
- `CREATED_BY`
- `VALIDATED_BY`
- `LAST_UPDATED_AT`

## 2. Canonical decisions

A short table recording confirmed decisions.

Suggested columns:

| FIELD | VALUE | SOURCE | STATUS | NOTES |
|---|---|---|---|---|
| VACANCY_TYPE | DEVELOPMENT_AGENCY | human override | confirmed | Use in CCOG and strategy |
| FUNCTIONAL_REGISTER | Cash-Based Programming / CVA | screening analysis | working | Distinct from vacancy type |

This section separates:
- organization / agency-type classification
- functional / domain register
- any numeric limits later confirmed by the human

## 3. Requirement register

A table that separates three often-confused dimensions:

| REQUIREMENT_ID | JD_REQUIREMENT | JD_IMPORTANCE | EVIDENCE_STRENGTH | GAP_RISK | NOTES |
|---|---|---|---|---|---|

### Definitions
- `JD_IMPORTANCE` = how central the requirement is in the JD
- `EVIDENCE_STRENGTH` = how strongly the candidate documents support it
- `GAP_RISK` = how damaging the gap would be during screening

Do not use one star field to mean all three.

## 4. Metric registry

This is the core table.

Suggested columns:

| METRIC_ID | CLAIM_TEXT | VALUE | UNIT | SCOPE | PERIOD | SOURCE_LOCATION | STATUS | ALLOWED_OUTPUT_FORMS | AGGREGATION_RULE | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|

### Required rules
- `METRIC_ID` must be unique.
- `SCOPE` must name the exact role or approved aggregate.
- `PERIOD` must identify the correct timeframe.
- `STATUS` should be one of:
  - `SUPPORTED`
  - `SUPPORTED_APPROX`
  - `CONFIRM_NEEDED`
  - `DO_NOT_USE`
- `ALLOWED_OUTPUT_FORMS` should show safe wording variants.
- `AGGREGATION_RULE` must explicitly say one of:
  - `ROLE_ONLY`
  - `AGGREGATE_ALLOWED`
  - `DO_NOT_AGGREGATE`

### Example of why this matters

Bad:
- Role 1 grievance tickets = 188
- Role 2 grievance tickets = >300
- downstream CV says Role 2 = 188+

Good:
- `GRV_R1_188` -> scope = Role 1 only -> `ROLE_ONLY`
- `GRV_R2_300PLUS` -> scope = Role 2 only -> `ROLE_ONLY`
- no combined grievance total unless explicitly supported

## 5. Non-numeric canonical claims

Not every critical claim is numeric. Include a table for role-scoped or
source-sensitive non-numeric facts:

| CLAIM_ID | CLAIM_TEXT | SCOPE | SOURCE_LOCATION | STATUS | SAFE_WORDING | NOTES |
|---|---|---|---|---|---|---|

Examples:
- direct reports
- governance body membership
- specific systems used
- policy / strategy roles
- reason-for-leaving wording

## 6. Approved aggregates

Some aggregates are safe; many are not.

Create a table:

| AGGREGATE_ID | COMPONENT_METRICS | APPROVED_WORDING | SOURCE_BASIS | STATUS | NOTES |
|---|---|---|---|---|---|

If an aggregate is not listed here, do not create it downstream.

## 7. Prohibited or high-risk claims

Create a do-not-use table:

| CLAIM_OR_METRIC | REASON | STATUS | SAFE_ALTERNATIVE |
|---|---|---|---|

Use this for:
- unsupported blockchain / stablecoin past-experience claims
- ambiguous leadership scope
- metrics that cannot be tied confidently to a role or period

## 8. Placeholder / confirmation queue

Track unresolved but potentially useful claims:

| ITEM | WHY_NEEDED | TARGET_DOC | PLACEHOLDER_TEXT | OWNER |
|---|---|---|---|---|

This prevents agents from inventing details while still surfacing missing
high-value evidence.

## 9. Change log

Every correction must be logged.

Suggested columns:

| DATE | CHANGED_BY | ITEM | OLD_VALUE | NEW_VALUE | REASON |
|---|---|---|---|---|---|

---

## Minimum operational rules

1. Every downstream generator must consult the ledger before using numbers.
2. QA must compare final documents against the ledger, not just against
   each other.
3. Independent evaluators may use the ledger for contradiction checks, but
   they must still benchmark the application against the JD.
4. If a metric is not in the ledger and not clearly present in source
   inputs, do not use it.
5. If a metric is approximate, mark it approximate consistently.

---

## Recommended file header template

```md
---
artifact: metric_ledger
job_slug: <JOB_SLUG>
target_system: <TARGET_SYSTEM>
vacancy_type: <VACANCY_TYPE>
functional_register: <FUNCTIONAL_REGISTER>
classification_source: <human|screening proposal|other>
created_by: <agent>
validated_by: <agent>
last_updated_at: <ISO timestamp>
status: draft|validated
---

# metric_ledger
```

---

## Recommended acceptance criteria

The ledger is considered ready when:

- vacancy type and functional register are explicit
- every critical JD requirement has `JD_IMPORTANCE`, `EVIDENCE_STRENGTH`,
  and `GAP_RISK`
- every reused metric in later outputs exists in the ledger
- role-specific metrics are clearly scoped
- unsafe aggregates are explicitly blocked
- unresolved items are placed in the confirmation queue, not guessed
- qa-auditor has validated the ledger
