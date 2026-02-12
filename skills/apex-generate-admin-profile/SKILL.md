---
name: apex-generate-admin-profile
description: >-
  Generate a unified Admin Profile for e‑recruitment systems: one headline
  followed by one paragraph per job in the format "Job Title: duties and
  achievements". Apply CAPEL character controls and integrate keyword guidance
  and bullet enhancements. Use this skill only when the user selects Option 1
  of Phase 8 or explicitly asks for an Admin Profile.
---

# apex-generate-admin-profile

## Purpose

This skill produces the optimized Admin Profile required by many
international organizations’ online application systems. Each job entry
is condensed into a single paragraph that highlights duties and
achievements, incorporates high‑priority keywords naturally and fits
within specified character limits.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: the complete work history.
- `JOB_DESCRIPTION_TEXT`: to align tone and keyword selection.
- Limits: `CHAR_LIMIT`, `TARGET_LOW`, `TARGET_HIGH`, `WORD_TARGET` for
  character control per entry.

Optional:

- `USER_ADMIN_PROFILE_TEXT`: to preserve existing phrasing and
  chronology.
- `apex-keyword-insertion-map` output: to place specific phrases.
- `apex-bullet-enhancer` output: to transform key bullets into
  stronger, result‑oriented phrases for inclusion in paragraphs.
- `TERM_EXTRACTOR`: to guide keyword usage.

## Output format

1. **Headline:** A one‑line role‑targeted summary derived from Phase 2.1.
2. **Job entries:** For each job (newest first unless otherwise
   specified), output one paragraph in the format `Job Title: duties and
   achievements`. Within each paragraph:
   - Start with the job title followed by a colon.
   - Summarize duties and achievements using action verbs and
     quantifiable outcomes; include keywords naturally.
   - Embed suggestions from the bullet enhancer as appropriate.
   - Use placeholders for missing metrics or details (e.g., “[User to
     Insert Specific Metric]”).
   - Do not include employer names or dates; those belong elsewhere.

Separate each job paragraph by exactly one newline. Do not include any
additional labels or headings in the final output.

## Example (for pattern reference; do not copy verbatim)

Programme Management Officer: Led the strategic design and
operational delivery of a USD [X]M multi-sector programme across
[N] countries, overseeing [N] implementing partners and coordinating
with government counterparts and UN agencies to ensure alignment
with national development priorities and donor requirements;
developed and deployed results-based monitoring frameworks using
[Tool], achieving [X]% data completeness and reducing reporting
turnaround by [X]%; facilitated [N] capacity-building workshops
for [N] stakeholders, strengthening programme delivery and
contributing to a [X]% improvement in targeting accuracy.

## Rules

- **Coverage:** Include every job from USER_JOB_HISTORY_TEXT and/or
  USER_ADMIN_PROFILE_TEXT; do not omit, merge, or skip any roles or
  contracts. Preserve the source chronology (default: newest to oldest,
  unless specified otherwise).
- Apply the CAPEL character control using the per-entry limits. Aim for
  the target band; adjust wording by expanding with micro-specifics or
  compressing by removing filler until each paragraph fits `TARGET_LOW`
  to `TARGET_HIGH` and never exceeds `CHAR_LIMIT` characters (with
  spaces).
- Use single spaces and ASCII punctuation only. Avoid bullet
  characters, tabs, fancy quotes or ellipsis. Use "..." instead of the
  ellipsis character.
- Use a professional, polished tone consistent with UN-style
  descriptions.
- Do not invent data or achievements; use placeholders for unknown
  specifics.
- **Initial acknowledgment:** Begin with "Understood. Generating your
  updated Admin Profile based on our strategy. This may take a
  moment..."
- **Conclusion:** After listing all optimized job entries, add:
  "Here is the draft of your updated Admin Profile. Please review it
  carefully, fill in any placeholders, and adjust any details to ensure
  it perfectly represents you."

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify character limits per
entry and the "Job Title: duties..." format.

## Steps

1. Draft the headline using insights from Phase 2 or the UVP.
2. Determine the order of job entries (default: newest first).
3. For each job:
   - Gather duties and achievements from the user’s history.
   - Incorporate enhanced bullet examples where relevant to strengthen
     phrasing and highlight outcomes.
   - Integrate high‑priority keywords following the insertion map.
   - Write a concise paragraph starting with `Job Title:`, then
     summarizing duties and achievements.
   - Use placeholders for missing specifics.
   - Apply `capel-fit` to the paragraph to ensure it meets
     character constraints.
   - Iterate wording and length adjustments as necessary.
4. After generating all paragraphs, insert one blank line between
   entries and output the final profile.
