---
name: apex-generate-motivation-statement
description: >-
  Generate a structured UN Inspira Motivation Statement (max 2000
  characters with spaces) using the VACC four-paragraph framework,
  condensed CAR/Micro-STAR evidence, and diplomatic tone. Enforced
  via capel-fit with a 1950–2000 character band. Option 7 of Phase 8.
  Do not generate other documents in this skill.
---

# apex-generate-motivation-statement

## Purpose

This skill drafts a highly effective Motivation Statement for the
UN Inspira e-recruitment platform. The Motivation Statement is the
primary qualitative differentiator in an Inspira application — it is
the candidate's opportunity to present a coherent narrative that
bridges experience, values, and forward-looking contribution in a
single, tightly constrained text field.

It must:

- fit within the **2000-character (with spaces)** Inspira field limit,
- follow the mandatory **VACC four-paragraph architecture**,
- use condensed **CAR (Context-Action-Result) / Micro-STAR** evidence
  formulations to maximize density,
- project the tone of a seasoned diplomat or rigorous policy analyst,
- integrate UN Core Values and the target agency's mandate naturally.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

Format profile: `inspira_field_strict` (single text block, ASCII
punctuation, no bullets or line breaks in final output).

## Inputs

Required:

- `JOB_DESCRIPTION_TEXT`: the full Job Opening (JO) including title,
  reference number, department, and duty station.
- `USER_JOB_HISTORY_TEXT`: to source specific achievements and evidence.

Optional:

- `apex-uvp-statement` output: for the strategic hook / opening pitch.
- `apex-star-story-blueprints` output: to select CAR/Micro-STAR
  examples for the competency paragraph.
- `apex-candidate-evidence-bank` output: for requirement-level evidence
  anchors and gap identification.
- `apex-progression-metric-ledger` output: for safe metric roll-ups
  when same-organization promotion chains exist.
- `TERM_EXTRACTOR` / `JD_KEYWORD_BANK`: for keyword integration.
- `JOB_REQUIREMENT_TEXT`: for competency and eligibility details.

## Output format

Return the Motivation Statement as a **single plain-text block suitable
for Inspira** (no Markdown headings, no bullets, ASCII punctuation only).
Paragraphs are separated by a single blank line within the text field.

The statement follows the **VACC four-paragraph architecture**:

### Paragraph 1 — Values: Strategic Hook and Institutional Alignment (~400 chars)

- Open by naming the exact Job Opening title and reference number.
- Articulate the candidate's fundamental professional or personal driver,
  linking it directly to the specific mandate of the hiring UN entity
  (Agency, Fund, or Programme).
- Convey genuine motivation — not a generic statement of interest but a
  precise connection between the candidate's trajectory and the
  organization's mission.

### Paragraph 2 — Alignment: Competency Synthesis and Empirical Evidence (~700 chars)

- **Do NOT** chronologically restate the CV or PHP duties.
- Distil technical capacity by highlighting **one or two major,
  quantifiable achievements** that mirror the primary requirements of
  the JO.
- Use the **condensed CAR / Micro-STAR method**: fuse Context and Action
  into a single high-density clause, followed immediately by a
  quantified Result. Example pattern:

  `To address [challenge] across [scope] (C), I [action verb]
  [deliverable] and [second action] (A), which [quantified outcome]
  (R).`

- If a safe same-organization roll-up is available from the progression
  ledger, use the consolidated metric rather than a single-role figure.

### Paragraph 3 — Contribution: Contextual Value and the Multilateral Agenda (~600 chars)

- Demonstrate awareness of the geopolitical, socioeconomic, or
  institutional context in which the role operates.
- Articulate how the candidate's specific skill set will advance
  targeted **Sustainable Development Goals (SDGs)** or address urgent
  challenges faced by the hiring agency or duty station.
- If the post is a non-family or hardship duty station, address
  resilience and adaptability concisely.

### Paragraph 4 — Context: Conclusion and Professional Readiness (~300 chars)

- Be exceedingly brief, confident, and forward-looking.
- Explicitly reaffirm readiness and commitment to the **UN Core Values**:
  Integrity, Professionalism, and Respect for Diversity.
- Close with a clean, diplomatic statement. Avoid aggressive
  "call-to-action" or private-sector sales-pitch phrasing.

## Character-limit enforcement

The final statement must be **1950–2000 characters (with spaces)**.
Draft using an internal CAPEL word budget targeting ~1950 characters
(approximately 300 words at 6.5 chars/word), then validate and adjust
deterministically with `capel-fit`:

```
python3 skills/capel-fit/scripts/fit_entry.py \
  --char-limit 2000 --target-low 1950 --target-high 2000 \
  --mode auto --print-report
```

- If STATUS is TOO_LONG after auto mode, manually compress while
  preserving the VACC paragraph structure, the CAR evidence, and
  Core Values reference, then re-run `capel-fit`.
- If STATUS is TOO_SHORT, add a concrete detail, a second SDG
  reference, or expand the CAR example, then re-run `capel-fit`.
- Repeat until STATUS: OK. No statement may be finalized without
  passing `capel-fit` validation.

## Lexical and tonal rules

- **Action verbs:** Embed verbs that map to the **UN Values and
  Behaviours Framework** — e.g., *facilitated, mediated, assessed,
  formulated, spearheaded, streamlined, pioneered, engineered,
  coordinated, governed*.
- **Tone:** Seasoned diplomat / rigorous policy analyst. Precise,
  active, measured vocabulary. Convey quiet confidence — not
  self-aggrandisement.
- **Avoid:**
  - Generic corporate platitudes (`passionate team player`,
    `results-driven professional`).
  - Private-sector jargon (`ROI`, `synergy`, `leverage` as a verb).
  - Informal or colloquial language.
  - Unexplained acronyms (spell out on first use; common UN acronyms
    like SDGs or OCHA are acceptable).
  - Absolute terminology (`always`, `guaranteed`, `the best`).
  - AI-generated text hallmarks (formulaic transitions, excessive
    hedging, repetitive sentence structures).
- **Punctuation:** ASCII only — straight quotes, hyphens (not em
  dashes), `...` (not ellipsis character). Single spaces only.

## Rules

- Do not fabricate names, dates, employer details, metrics, or outcomes
  not present in user inputs; use bracketed placeholders instead.
- Do not repeat Admin Profile or CV bullets verbatim; synthesize into
  narrative form.
- Each paragraph has a distinct function per the VACC framework — do
  not merge or reorder them.
- Integrate at least one condensed CAR / Micro-STAR formulation in
  Paragraph 2. Use placeholders for missing metrics.
- The JO title and reference number must appear in Paragraph 1
  (use `[JO Title]` and `[JO Ref#]` if not available in inputs).
- Mention at least one specific SDG or agency priority in Paragraph 3.
- Paragraph 4 must reference the three UN Core Values by name.
- Follow the same promotion-chain and metric-accumulation safety rules
  defined in `apex-guardrails` and used by
  `apex-generate-qualification-answers`.
- Every final statement must pass `capel-fit` validation (STATUS: OK)
  before being presented to the user.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:**

- VACC four-paragraph structure intact.
- JO title/reference present in Paragraph 1.
- At least one CAR / Micro-STAR formulation in Paragraph 2.
- SDG or agency-priority reference in Paragraph 3.
- UN Core Values named in Paragraph 4.
- Diplomatic tone; no private-sector jargon or platitudes.
- Character count within 1950–2000 band (via `capel-fit`).
- ASCII punctuation only; no line breaks within paragraphs.

## Steps

1. Extract the JO title, reference number, hiring entity, duty station,
   and key requirements from `JOB_DESCRIPTION_TEXT`.
2. Identify the candidate's strongest 1–2 achievements relevant to the
   JO from the evidence bank or job history.
3. If `apex-progression-metric-ledger` is available, pull safe roll-ups
   for any same-organization promotion chains.
4. Select one or two SDGs or agency priorities that connect the
   candidate's expertise to the role's context.
5. Draft the four VACC paragraphs using an internal word budget of ~290
   words. Allocate roughly:
   - P1: ~60 words
   - P2: ~105 words
   - P3: ~90 words
   - P4: ~45 words
6. Run `capel-fit` on the full statement with `--char-limit 2000
   --target-low 1950 --target-high 2000 --mode auto --print-report`.
   Iterate until STATUS: OK.
7. Run the recursive self-evaluation loop (domain-specific checks
   above). Revise if needed; re-run `capel-fit` after any revision.
8. Output the final plain-text statement.
