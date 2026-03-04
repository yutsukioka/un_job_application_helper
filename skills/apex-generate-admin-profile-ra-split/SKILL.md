---
name: apex-generate-admin-profile-ra-split
description: >-
  Generate IOM/Oracle Recruiting Cloud-style employment-record content where each role is split into Responsibilities and Achievements sections with bullets point aloowed, plus Direct Reports and Reason for Leaving. Option 5 of Phase 8.
---

# apex-generate-admin-profile-ra-split

## Purpose

This skill produces IOM/Oracle Recruiting Cloud-style role entries where Responsibilities and Achievements
are entered separately. The output may use headings and hyphen bullets for skimmability.

Core intent:
- Responsibilities = baseline mandate (“what was required”)
- Achievements = impact (“what changed because of your actions”), using condensed CAR/STAR bullets
- Integrate JD language and high-priority terms naturally without keyword stuffing
- Include Direct Reports and Reason for Leaving per role

## Shared definitions

Apply the multi-expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, Output Format Profiles, and error handling patterns defined in `apex-guardrails`.

Use format profile: `iom_ra_split` (headings allowed, hyphen bullets allowed).

## Inputs

Required:

- `USER_JOB_HISTORY_TEXT`: including duties and achievements for each
  role.
- `JOB_DESCRIPTION_TEXT`: for aligning keywords and tone.

Optional:

- `TERM_EXTRACTOR` and keyword insertion guidance.
- `JD_KEYWORD_BANK`
- `apex-keyword-insertion-map` output
- `apex-bullet-enhancer` output
- `apex-star-story-blueprints` output (to seed achievements)
- LIMITS (if present; may include numeric limits, but if not present or unlimited; no need to apply character limitation.)

## Output format

For each job, (newest to oldest unless user specifies), output exactly four lines:

1. **ROLE:** <Job Title> — <Organization> — <Dates> (use placeholders if missing)
2. **DIRECT_REPORTS:** <short line; number + type if known; otherwise placeholder>

3. **RESPONSIBILITIES:**
- <List all distinct responsibilities. Use as many bullets as necessary to be comprehensive, but strictly avoid redundancy. Combine overlapping tasks.>

4. **ACHIEVEMENTS:**
- <List all distinct achievements. Include as many as relevant, ensuring each highlights unique impact or value without repeating information.>

5. **REASON_FOR_LEAVING:** <short standard phrase OR safe options with "(Select one)">

Insert exactly one blank line between roles.

Formatting rules:
- Use hyphen bullets only: "- "
- Avoid fancy bullets, emojis, tabs, and smart quotes.
- Keep each bullet ideally 1 line; max 2 lines.
- Prioritize uniqueness: Every bullet must add new value. If two points say similar things, merge them into one strong bullet.
- Do not add filler points just to lengthen the list.

## Content rules

### Responsibilities bullets
- Describe ongoing functions, coordination, compliance, planning, reporting, and delivery responsibilities.
- Include scope (stakeholders, partners, geography, caseload/program scale) when supported by evidence.
- Do not write achievements here.

### Achievements bullets (condensed CAR/STAR)
Each achievement bullet must include:
- Action (what you did) + Result (what changed)
- Add Context only briefly if needed
- Quantify results where possible; otherwise placeholders (e.g., [X]%, [USD X], [N] partners, [N] reports)

Team-based achievements are allowed, but specify the individual’s workstream (e.g., “Led the data verification workstream…”).

### Direct Reports
- If known: state number and type (e.g., “Direct reports: 3 staff + 8 enumerators”).
- If unknown: “Direct reports: [Confirm number and types]”.

### Reason for leaving
Use standard, diplomatic phrases (do not criticize employer):
- End of contract / End of consultancy / Project concluded / Career progression / Relocation / Full-time study
If unknown, provide 2–3 safe options separated by " / " and append "(Select one)".
## Length & Character Limits

- IF NO LIMIT IS SPECIFIED (or user specifies "Unlimited"): Generate as many distinct, high-value bullets as necessary. Focus on comprehensive coverage without redundancy. 
- IF A NUMERIC LIMIT IS SPECIFIED (`CHAR_LIMIT`): Strictly adhere to the user's limit by using `capel-fit`. Prioritize the most impactful points, keep phrasing concise, and reduce the total bullet count to fit within the constraint.
- Formatting constraint: Even when under strict limits, do not abandon the bulleted format to save space (e.g., do not cram text into a dense paragraph) unless the user explicitly requests paragraph-only mode.

## Rules

- **No invention:** Do not fabricate organization names, dates or results, metrics, budgets, tools, or headcounts; use placeholders such as `[Org Name]`, `[Dates]`, `[User to Insert Metric]` as needed.
- **JD terminology:** Use JD terminology naturally; do not paste star symbols (★). Maintain professional tone and integrate high-priority keywords naturally.
- **Coverage:** Include every job from USER_JOB_HISTORY_TEXT and/or USER_ADMIN_PROFILE_TEXT; do not omit, merge, or skip any roles or contracts. Preserve the source chronology (default: newest to oldest, unless specified otherwise).

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

## Steps

1. Extract roles and dates from USER_JOB_HISTORY_TEXT; preserve chronology.
2. Build Responsibilities bullets per role aligned to JOB_DESCRIPTION_TEXT language.
3. Build Achievements bullets per role using CAR/STAR compression and metrics/placeholders.
4. Add Direct Reports and Reason for Leaving lines.
5. Output using the exact headings and bullet format.

