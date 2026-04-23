---
name: apex-generate-admin-profile
description: >-
  Generate INSPIRA/UNICEF employment-record text: per role, a
  paste-ready Duties/Responsibilities field (character-controlled if
  limits exist), plus Direct Reports and Reason for Leaving. Option 1 of
  Phase 8.
---

# apex-generate-admin-profile

## Purpose

This skill produces the optimized Admin Profile required by many
international organizations’ online application systems. Each job entry
is condensed into a paste-ready duties/responsibilities field that
highlights responsibilities and achievements while maintaining ATS-safe
formatting and character control when numeric limits exist.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: the complete work history.
- `JOB_DESCRIPTION_TEXT`: to align tone and keyword selection.
- `LIMITS`: read `TARGET_SYSTEM` and any numeric field limits when
  present.

Optional:

- `USER_ADMIN_PROFILE_TEXT`: to preserve existing phrasing and
  chronology.
- `apex-keyword-insertion-map` output: to place specific phrases.
- `apex-bullet-enhancer` output: to transform key bullets into
  stronger, result‑oriented phrases for inclusion in paragraphs.
- `TERM_EXTRACTOR`: to guide keyword usage.

## Output format

For each role (newest first unless otherwise specified), output these
plain-text fields:

1. `ROLE: <Job Title> — <Organization> — <Dates>` (use placeholders if needed)
2. `DUTIES_RESPONSIBILITIES: <single paste-ready paragraph>`
3. `DIRECT_REPORTS: <short line>` only when the workflow/context expects it
4. `REASON_FOR_LEAVING: <short diplomatic line>` only when the
   workflow/context expects it

The `DUTIES_RESPONSIBILITIES` field must:
- remain a single clean paragraph suitable for INSPIRA/UNICEF paste
  fields;
- maintain duties vs. achievements discipline internally, even when
  those ideas appear in one paragraph;
- use ASCII punctuation, single spaces, and no bullets or tabs;
- use `capel-fit` only when a numeric limit is provided.

If the workflow/context only requires the paragraph field, keep the
paragraph clean and paste-ready. If Direct Reports and Reason for
Leaving are required, output them as separate clearly labeled fields per
role.

## Example (for pattern reference; do not copy verbatim)

ROLE: Programme Management Officer — [Organization] — [Dates]
DUTIES_RESPONSIBILITIES: Led the strategic design and operational
delivery of a USD [X]M multi-sector programme across [N] countries,
overseeing [N] implementing partners and coordinating with government
counterparts and UN agencies to ensure alignment with national
development priorities and donor requirements; developed and deployed
results-based monitoring frameworks using [Tool], achieving [X]% data
completeness and reducing reporting turnaround by [X]%; facilitated [N]
capacity-building workshops for [N] stakeholders, strengthening
programme delivery and contributing to a [X]% improvement in targeting
accuracy.
DIRECT_REPORTS: [Confirm number and types]
REASON_FOR_LEAVING: End of contract

## Rules

- **Coverage:** Include every job from USER_JOB_HISTORY_TEXT and/or
  USER_ADMIN_PROFILE_TEXT; do not omit, merge, or skip any roles or
  contracts. Preserve the source chronology (default: newest to oldest,
  unless specified otherwise).
- **TARGET_SYSTEM behavior:** For `INSPIRA` and `UNICEF`, produce
  paste-ready field text. If a numeric `CHAR_LIMIT` or field-specific
  limit is present, apply `capel-fit`. If `CHAR_LIMIT` is missing or
  `UNLIMITED`, prioritize useful content and completeness over forced
  compression.
- Use single spaces and ASCII punctuation only. Avoid bullet
  characters, tabs, fancy quotes or ellipsis. Use "..." instead of the
  ellipsis character.
- Use a professional, polished tone consistent with UN-style
  descriptions.
- Include supervisory scope/direct reports when supported by evidence;
  otherwise use placeholders if the field is expected.
- Provide Reason for Leaving as a separate short diplomatic line per role
  when expected by the workflow/context.
- Do not invent employers, dates, tools, budgets, metrics, or outcomes.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify character limits for
the duties/responsibilities paragraph when limits exist and verify the
required `ROLE` / `DUTIES_RESPONSIBILITIES` field structure.

## Steps

1. Determine the order of job entries (default: newest first).
2. For each role, gather duties, scope, achievements, supervisory
   context, and any existing reason-for-leaving detail from the source
   inputs.
3. Draft a single `DUTIES_RESPONSIBILITIES` paragraph that preserves
   duties vs. achievements discipline internally.
4. If a numeric limit exists for the paragraph field, apply `capel-fit`.
   If not, do not CAPEL-fit.
5. Add `DIRECT_REPORTS` and `REASON_FOR_LEAVING` as separate labeled
   fields only when the workflow/context expects them.
6. Output the role blocks in the required plain-text format.
