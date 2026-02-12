---
name: term_extractor
description: >-
  Extract exactly five high-priority terms from a job description with star
  ratings, ATS synonyms, JD-grounded rationale, and resume-ready examples in
  a strict four-line format. Use this skill as a prerequisite step before the
  orchestrator to populate the TERM_EXTRACTOR section, or standalone when the
  user asks for keyword extraction from a JD.
---

# term_extractor

## Purpose

This skill analyzes a job description to extract the five most important
screening terms for ATS optimization and competency-based shortlisting.
The output populates the `TERM_EXTRACTOR` section of
`inputs/application_context.md` for use by downstream skills.

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_DESCRIPTION_TEXT`: the full job description including
  Responsibilities, Requirements/Qualifications, and Desirable sections.

Optional:

- `USER_JOB_HISTORY_TEXT` or candidate CV/LinkedIn bullets: to tailor
  resume examples. If missing, use placeholders like `[country]`,
  `[programme]`, `[tool]`, `[metric]` and do not invent facts.
- Optimization preference: ATS-first / Human-first / Balanced
  (default: Balanced).

## Success Metrics (optimize for all)

- **M1: Evidence-Backed Relevance (9/10+):** Each term is explicitly
  present in the JD or strongly implied by repeated duties. No guessing.
- **M2: Coverage (8/10+):** Across five terms, cover as many domains as
  the posting allows: core domain(s), technical skills/tools, UN
  frameworks/programmes/policies, high-impact deliverables, essential
  qualifications.
- **M3: ATS Matching Strength (8/10+):** Each term includes 6-12
  synonyms with common variations and acronyms (if in the posting).
- **M4: Non-Redundancy (9/10+):** No near-duplicate terms unless the
  posting clearly treats them as separate requirements.
- **M5: Format Compliance (10/10 required):** Exactly five terms, each
  exactly four lines with required labels, separated by one blank line.

## Star Rating Rubric (use ★ symbols)

- ★★★★★ = explicitly required / central to the role (title + required
  quals + repeated deliverables)
- ★★★★ = strongly emphasized; likely a screening keyword
- ★★★ = important but secondary
- ★★ = supportive / nice-to-have
- ★ = minor mention

Only use ★★★★★ when the posting clearly supports it.

## Output format

For each of the five terms, output exactly four lines (no bullets, no
numbering, no extra sections). Separate terms with one blank line.

- Line 1: `<term> <stars>`
- Line 2: `Synonyms: syn1; syn2; syn3; ...`
- Line 3: `You should add this term because: <brief reason tied to JD>`
- Line 4: `Example for your resume: "one resume-style sentence"`

No extra text before or after the five terms.

## Steps

1. **Extract (broad net):** Identify 10-15 candidate terms/skill areas
   directly from the posting (title, duties, requirements). Keep them
   specific; avoid vague soft skills unless emphasized.
2. **Score for screening likelihood (internal only; do not print):**
   Assess each term for "Required/Essential/Must" language, repetition
   across sections, centrality to title + top duties, typical shortlist
   keywords, and qualification gatekeeping.
3. **Select the final five:** Choose the best 5 ensuring coverage (M2)
   and no redundancy (M4). If the posting includes a required
   degree/language/certification, ensure one term captures it.
4. **Build synonyms:** For each term, generate 6-12 semicolon-separated
   synonyms/variants, including acronyms and full names only if
   supported by the posting.
5. **Justify with JD evidence:** For each term, write a brief
   justification tied to where it appears (Responsibilities /
   Requirements / Desirable). May include a short JD phrase (max 8
   words).
6. **Write a resume-style example:** Create one bullet-like sentence in
   quotes with action + scope + outcome. Use metrics if candidate info
   provides them; otherwise use placeholders.
7. **Self-evaluation (internal only; do not print):** Apply the
   recursive self-evaluation loop from `apex-guardrails`, adding
   domain-specific checks: verify M1-M5 thresholds, check for missed
   gatekeepers (degree/language/tools/frameworks), and eliminate
   redundancy or overly generic terms. Output only once all metrics
   pass.
