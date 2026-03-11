---
name: apex-application-audit
description: >-
  Post-generation review of completed application documents. Evaluates
  requirement alignment, framing register, and structural vulnerabilities
  from a panel-reviewer perspective. Does not produce numeric scores or
  shortlist predictions. Use after generating one or more Phase 8 documents,
  or when the user asks for an application audit.
---

# apex-application-audit

## Purpose

This skill performs a post-generation review of application materials from the
perspective of a critical hiring panel.

It is not a scoring system. It does not predict shortlisting. Its job is to
answer:

- Are the core requirements actually covered across the documents?
- Is the framing register appropriate for the target vacancy?
- Which structural weaknesses still remain after generation?
- Which claims still require user verification before submission?

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop protocol,
and guiding principles defined in `apex-guardrails`.

Output format profile: `strategy_markdown`.

## Inputs

Required:

- one or more generated Phase 8 documents
  - Admin Profile and/or
  - CV and/or
  - Cover Letter and/or
  - Qualification Answers and/or
  - Motivation Statement and/or
  - IOM RA split output

Strongly recommended:

- `apex-jd-core-requirements` output
- `apex-candidate-evidence-bank` output
- `ccog_reference_resolved.md`
- `apex-orchestrator-report` match assessment
- `apex-cross-doc-consistency` output when 2+ documents exist

## Output format

Return exactly these sections:

### `## Alignment Matrix`

For each core requirement from Phase 1.2, output a table:

| Requirement | Admin Profile | CV | Cover Letter | Qual. Answers | Strength |
|---|---|---|---|---|---|
| <requirement> | <where addressed or Not provided> | <where addressed or Not provided> | <where addressed or Not provided> | <where addressed or Not provided> | Strong / Partial / Weak / Absent |

Rules:
- If a document type was not provided, mark it `Not provided`.
- Strength is about actual evidentiary coverage, not style.

### `## Framing Diagnosis`

Include:

- **Target CCOG register:** <code — family name OR JD-derived register>
- **Actual register used:** <assessment by document>
- **Register alignment:** `ALIGNED | PARTIAL_MISMATCH | FULL_MISMATCH`
- **Specific mismatches:** list verb- or phrase-level mismatches with corrected, ethical alternatives

### `## Structural Vulnerabilities`

For each remaining `CRITICAL` or `SIGNIFICANT` gap that is still weakly
addressed after generation, include:

- **Gap:** <description>
- **How it manifests in documents:** <specific observation>
- **Competing candidate advantage:** <what a stronger candidate likely has>
- **Remediation option:** <if any>

### `## Verification Checklist`

Include every item that still requires candidate confirmation:

- [ ] <claim requiring `[Confirm: ...]` verification>
- [ ] <metric that should be double-checked>
- [ ] <scope, title, timeframe, or stakeholder detail that needs confirmation>

### `## Consistency Flags`

- If `apex-cross-doc-consistency` is available, summarize its relevant flags.
- If not, provide direct observations on:
  - title mismatches
  - date discrepancies
  - metric conflicts
  - promotion-anchor inconsistency
  - register drift across documents

## Rules

- Do not assign numeric scores.
- Do not predict shortlisting likelihood.
- Do not invent stronger competitor evidence than the context supports.
- Be direct about structural weaknesses without becoming fatalistic.
- Keep the audit evidence-based and document-specific.
- When a weakness is primarily a register issue rather than an experience gap,
  say so explicitly.

## Steps

1. Read the core requirements and evidence-bank output.
2. Map each requirement across the generated documents.
3. Assess whether the wording matches the target register.
4. Surface remaining structural vulnerabilities, focusing on `CRITICAL` and
   `SIGNIFICANT` issues.
5. Compile a verification checklist from placeholders and confirm-tags.
6. If multiple documents exist, integrate or replicate the most relevant
   consistency flags.
7. Output the five required sections above.
