---
name: apex-generate-admin-profile-dra-split
description: >-
  Generate Generic ATS Cloud-style employment-record content where each role is split into Duties, Responsibilities, and Achievements sections with bullet points allowed, plus Direct Reports and Reason for Leaving. Option 8 of Phase 8.
---

# apex-generate-admin-profile-dra-split

## Purpose

This skill produces ATS-style role entries where Duties, Responsibilities, and Achievements
are entered as three separate sections. The output uses headings and hyphen bullets for skimmability.

This skill **cooperates** with `apex-generate-admin-profile-ra-split` (Option 5):
- **This skill** generates the **Duties** and **Responsibilities** sections.
- `apex-generate-admin-profile-ra-split` generates the **Responsibilities** and **Achievements** sections, plus **Direct Reports** and **Reason for Leaving**.
- The final combined output uses:
  - **Duties** — from this skill
  - **Responsibilities** — from this skill
  - **Achievements** — from `apex-generate-admin-profile-ra-split`
  - **Direct Reports** — from `apex-generate-admin-profile-ra-split`
  - **Reason for Leaving** — from `apex-generate-admin-profile-ra-split`

Core definitions:
- **Duty** = a specific required act or function; an obligatory task or conduct that emerges from the employee's occupation or role. Duties denote what must be done — the recurring, mandated activities.
- **Responsibility** = a prospective sphere of ownership and answerability for outcomes; the obligation to achieve desired results and bear accountability for actions. Responsibilities denote what you are answerable for — the scope of ownership.
- **Achievement** = impact delivered ("what changed because of your actions"), using condensed CAR/STAR bullets — sourced from `apex-generate-admin-profile-ra-split`.

Integrate JD language and high-priority terms naturally without keyword stuffing.

## Shared definitions

Apply the multi-expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, Output Format Profiles, and error handling patterns defined in `apex-guardrails`.

Use format profile: `ats_dra_split` (headings allowed, hyphen bullets allowed).

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: including duties and responsibilities for each role.
- `JOB_DESCRIPTION_TEXT`: for aligning keywords and tone.

Optional:

- `TERM_EXTRACTOR` and keyword insertion guidance.
- `JD_KEYWORD_BANK`
- `apex-keyword-insertion-map` output
- `apex-bullet-enhancer` output
- `apex-star-story-blueprints` output (to seed achievements via RA-split)
- LIMITS (if present; may include numeric limits, but if not present or unlimited, no need to apply character limitation.)

## Output format

For each job (newest to oldest unless user specifies), output exactly:

1. **ROLE:** <Job Title> — <Organization> — <Dates> (use placeholders if missing)
2. **DIRECT_REPORTS:** <short line; number + type if known; otherwise placeholder> _(sourced from `apex-generate-admin-profile-ra-split`)_

3. **DUTIES:**
- <List all distinct duties — specific required acts, functions, and recurring mandated activities. Use as many bullets as necessary to be comprehensive, but strictly avoid redundancy. Combine overlapping items.>

4. **RESPONSIBILITIES:**
- <List all distinct responsibilities — spheres of ownership, answerability for outcomes, scope of accountability. Use as many bullets as necessary to be comprehensive, but strictly avoid redundancy. Combine overlapping items.>

5. **ACHIEVEMENTS:**
- <List all distinct achievements — sourced from `apex-generate-admin-profile-ra-split`. Each highlights unique impact or value using condensed CAR/STAR format.>

6. **REASON_FOR_LEAVING:** <short standard phrase OR safe options with "(Select one)"> _(sourced from `apex-generate-admin-profile-ra-split`)_

Insert exactly one blank line between roles.

Formatting rules:
- Use hyphen bullets only: "- "
- Avoid fancy bullets, emojis, tabs, and smart quotes.
- Keep each bullet ideally 1 line; max 2 lines.
- Prioritize uniqueness: Every bullet must add new value. If two points say similar things, merge them into one strong bullet.
- Do not add filler points just to lengthen the list.

## Content rules

### Duties bullets
- Describe specific required acts, functions, and recurring mandated tasks inherent to the role.
- Focus on the "what must be done" — obligatory activities, compliance tasks, operational functions.
- Include scope indicators (frequency, systems, frameworks) when supported by evidence.
- Do not write responsibilities or achievements here.
- Examples of duty language: "Process…", "Conduct…", "Prepare…", "Maintain…", "File…", "Submit…", "Execute…"

### Responsibilities bullets
- Describe spheres of ownership, areas of accountability, and answerability for outcomes.
- Focus on the "what you are answerable for" — overseeing, ensuring, managing, coordinating, leading.
- Include scope (stakeholders, partners, geography, caseload/program scale) when supported by evidence.
- Do not write duties (specific acts) or achievements here.
- Examples of responsibility language: "Oversee…", "Ensure…", "Lead…", "Manage…", "Coordinate…", "Advise…", "Accountable for…"

### Achievements bullets (from `apex-generate-admin-profile-ra-split`)
Each achievement bullet must include:
- Action (what you did) + Result (what changed)
- Add Context only briefly if needed
- Quantify results where possible; otherwise placeholders (e.g., [X]%, [USD X], [N] partners, [N] reports)

Team-based achievements are allowed, but specify the individual's workstream (e.g., "Led the data verification workstream…").

### Direct Reports (from `apex-generate-admin-profile-ra-split`)
- If known: state number and type (e.g., "Direct reports: 3 staff + 8 enumerators").
- If unknown: "Direct reports: [Confirm number and types]".

### Reason for leaving (from `apex-generate-admin-profile-ra-split`)
Use standard, diplomatic phrases (do not criticize employer):
- End of contract / End of consultancy / Project concluded / Career progression / Relocation / Full-time study
If unknown, provide 2–3 safe options separated by " / " and append "(Select one)".

## Length & Character Limits

- IF NO LIMIT IS SPECIFIED (or user specifies "Unlimited"): Generate as many distinct, high-value bullets as necessary. Focus on comprehensive coverage without redundancy.
- IF A NUMERIC LIMIT IS SPECIFIED (`CHAR_LIMIT`): Strictly adhere to the user's limit by using `capel-fit`. Prioritize the most impactful points, keep phrasing concise, and reduce the total bullet count to fit within the constraint.
- Apply `capel-fit` to all three sections (Duties, Responsibilities, Achievements) individually when character limits are specified.
- Formatting constraint: Even when under strict limits, do not abandon the bulleted format to save space (e.g., do not cram text into a dense paragraph) unless the user explicitly requests paragraph-only mode.

## Rules

- **No invention:** Do not fabricate organization names, dates or results, metrics, budgets, tools, or headcounts; use placeholders such as `[Org Name]`, `[Dates]`, `[User to Insert Metric]` as needed.
- **JD terminology:** Use JD terminology naturally; do not paste star symbols (★). Maintain professional tone and integrate high-priority keywords naturally.
- **Coverage:** Include every job from USER_JOB_HISTORY_TEXT and/or USER_ADMIN_PROFILE_TEXT; do not omit, merge, or skip any roles or contracts. Preserve the source chronology (default: newest to oldest, unless specified otherwise).
- **Duty vs. Responsibility distinction:** Maintain a clear separation. If a source bullet is ambiguous, classify it based on whether it describes a specific act/function (→ Duty) or a sphere of ownership/answerability (→ Responsibility). When in doubt, prefer Responsibility.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

Additional self-check for this skill:
- Verify every Duty bullet describes a specific act/function, not a sphere of ownership.
- Verify every Responsibility bullet describes ownership/accountability, not a specific task.
- Verify Achievements, Direct Reports, and Reason for Leaving are sourced from RA-split output.

## Steps

1. Invoke `apex-generate-admin-profile-ra-split` to produce Responsibilities, Achievements, Direct Reports, and Reason for Leaving per role. Retain the Achievements, Direct Reports, and Reason for Leaving from this output.
2. Extract roles and dates from USER_JOB_HISTORY_TEXT; preserve chronology.
3. Build **Duties** bullets per role — identify specific required acts, functions, and recurring mandated activities from the job history. Align to JOB_DESCRIPTION_TEXT language.
4. Build **Responsibilities** bullets per role — identify spheres of ownership, areas of accountability, and answerability for outcomes. Align to JOB_DESCRIPTION_TEXT language. Cross-check against the Responsibilities produced by RA-split for completeness.
5. Adopt **Achievements** bullets from the RA-split output (Step 1).
6. Adopt **Direct Reports** and **Reason for Leaving** from the RA-split output (Step 1).
7. If character limits are specified, apply `capel-fit` to Duties, Responsibilities, and Achievements sections individually.
8. Assemble the final output using the exact headings and bullet format defined above.
