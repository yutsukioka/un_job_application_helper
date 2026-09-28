---
name: apex-generate-qualification-answers
description: Draft Option 4 qualification responses using observed portal controls and per-question limits, including exact choice labels or narrative answers. Preserve the legacy two-part, 1000-character default for long-form questions without portal schema. Use only when Option 4 or qualification answers are requested; do not generate other documents or submit an application.
---

# apex-generate-qualification-answers

## Purpose

This skill prepares evidence-grounded responses to application qualification
questions. The observed question, control type and field-specific instructions
determine the output. It prepares a reviewable artifact; it does not enter
responses into a portal or accept declarations on the applicant's behalf.

## Shared definitions

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, internal CAPEL generation technique, guiding principles, and
error handling patterns defined in `apex-guardrails`.

## Inputs

Required:

- `JOB_QUALIFICATION_QUESTIONS`: exact application questions, including any
  supplied instructions and answer choices.
- `USER_JOB_HISTORY_TEXT`: to source relevant experiences.

Optional:

- `apex-candidate-evidence-bank` output for quick reference to
  experience.
- `TERM_EXTRACTOR` or JD to align with keyword requirements.
- `PORTAL_FIELD_SCHEMA` or a supplied vacancy-specific private ATS page map:
  exact question labels, control types, options, required flags, conditional
  follow-ups and observed per-field limits with their units and provenance.
  Read the actual linked artifact when supplied; do not infer its contents
  from the filename or assume another vacancy's map applies.
- User-supplied answers to personal, legal, administrative or declaration
  questions, anchored to the exact question and current application.

## Control and limit precedence

1. Match each question by its full wording and vacancy, not just its position
   in a pasted list. Preserve question order and distinguish available options
   from an applicant's selected answer. An unanswered field is not a "No".
2. Use the observed control and question-specific instructions before legacy
   narrative conventions. Preserve conditional questions and their trigger;
   do not invent unseen follow-ups or assume a hidden field is absent.
3. For narrative fields, apply any verified per-question numeric limit and
   counting unit, together with a stricter user-requested limit where supplied.
   Do not impose the legacy 1000-character maximum over a known different
   portal limit. A verified maximum alone creates no minimum writing target.
4. If the observed narrative limit is `UNKNOWN` or `UNLIMITED`, do not invent
   a numeric cap or use CAPEL fitting unless the user supplies one. Absence of
   a visible counter does not prove that a field is unlimited.
5. Only when no portal schema or field-specific instructions are available
   and the supplied question is genuinely long-form, retain the legacy
   two-part single-paragraph format and 1000-character maximum below. Label
   this a drafting default, not a verified portal restriction. Do not apply
   that fallback to questions with explicit selectable options.

## Choice questions and declarations

For select, radio, checkbox or other controlled-choice questions, output only
the exact permitted option label as the proposed field value; preserve every
selected label separately if the control allows multiple selections. Verify
the permitted selection count from the actual control or instructions;
checkbox-shaped pills alone do not establish that multiple choices are allowed.
Never substitute an essay, rewrite the labels, infer a numeric scale, or convert
"extensive" and "moderate" into unrelated proficiency ratings. Keep evidence
and review notes outside the field value.

For job-experience, education or familiarity questions, recommend a choice
only when the applicant's evidence supports that choice. Where the portal
defines its levels, use those definitions. Without definitions, identify the
recommendation as an evidence-based judgment rather than an official rating;
do not automatically select the strongest option. If support is insufficient,
place the item in a separate confirmation list with a precise evidence gap.

Personal/legal declarations and administrative facts—including work rights,
nationality, prior service, disciplinary or criminal history, investigations,
relatives, referral source and current leave status—require the user's supplied
answer to that question. Do not infer an answer from silence or from general
job history. Record missing answers in the confirmation list. A completeness
certification, consent or final attestation remains for the applicant to
review and assert; do not mark it accepted or treat inspecting all pages as
proof that the applicant can certify completeness.

Present each question with its proposed field value when supported, then put
supporting rationale and unresolved items in separate review sections. Do not
present a confirmation placeholder as a selectable option or ready field value.

## Narrative output format

Return each narrative question followed by its answer in plain text. Unless
the observed question-specific instructions require another structure, use
the two-part single-paragraph format:

1. **Part 1:** Start with parentheses indicating the period and
   organization where the experience was gained (e.g., “(2018–2021,
   XYZ Company) – ”). If multiple experiences apply, mention up to two
   separated by semicolons.
2. **Part 2:** Immediately continue with a detailed description of
   how that experience meets the requirement, including what you did,
   how it demonstrates the skill or knowledge asked about, and the
   outcome. Use concise narrative and include metrics where available
   (insert placeholders for missing numbers or specifics).

For the legacy long-form fallback only, each answer must not exceed
**1000 characters (with spaces)**; aim for approximately 800–950 characters
when supported evidence warrants that detail. Do not pad an answer or invent
facts to reach that range. For observed fields, follow the control and limit
precedence above instead.

For every applicable numeric limit, validate the exact final field value
deterministically using [capel-fit](../capel-fit/SKILL.md). Use its exact-text
validation path for native portal text and the reported counting unit (UTF-16
for an observed HTML `maxlength`). Use normalized fitting only where the
field's format requirements call for it. Keep character counts and validation
notes outside the paste-ready answer; never fit or shorten controlled labels.

## Example (for pattern reference; do not copy verbatim)

**Question:** Do you have experience in results-based management
and programme monitoring?

**Answer:** (2019-2023, [Organization], [Country]) - Led the design
and implementation of a results-based monitoring framework for a
USD [X]M multi-year programme spanning [N] field locations;
developed programme logic models, indicator tracking matrices, and
quarterly reporting templates aligned with [Framework]; trained [N]
national staff on data collection protocols and quality assurance
procedures, achieving [X]% data completeness across all programme
indicators; introduced real-time dashboards using [Tool] that
reduced reporting cycle time by [X]% and enabled evidence-based
programme adjustments, contributing to a [X]% improvement in
beneficiary targeting accuracy.

## Narrative rules

Apply these rules subject to the observed question-specific instructions above.

- Do not start answers with “Yes, I have…” or restate the question.
- Do not refer the reader to other documents. Each answer must stand
  alone.
- Follow the two‑part format strictly—no line breaks or bullet points.
- Use semicolons or concise conjunctions to join sentences when needed.
- Use placeholders like `[User to Insert Specific Metric]` for missing
  data instead of inventing details.
- Never exceed the applicable numeric limit established above.
- Keep each answer to one paragraph only.
- **Required qualifications:** If the question asks for a required
  qualification (e.g., a certain number of years of experience or a
  specific degree), confirm that the candidate meets it only when supported
  and provide evidence; otherwise flag the gap. For example: "I have over
  5 years of experience in [field], as demonstrated by [specific role or project]."
- **Desirable qualifications:** If the question asks for desirable
  qualifications like publications or certifications, mention them
  specifically. Provide titles of publications with year and where
  published, or certification names with the granting institution and
  date. For example: "I hold the PMP certification (Project Management
  Professional, 2022, PMI)."
- **Multi-part questions:** If a question allows multiple examples
  (e.g., "describe your experience in A, B, and C"), structure the
  answer to address each part clearly using sentences that signpost
  each area ("In area A, I did XYZ...; in area B, my role was...;
  etc."). Maintain the single-paragraph rule.

## Recursive self-evaluation (internal only; do not print)

Apply the recursive self-evaluation loop protocol from `apex-guardrails`.

**Domain-specific checks for this skill:** verify every output matches the
observed control, permitted labels and applicable numeric limit. Check that
personal declarations are user-supplied, attestations remain unasserted,
unresolved items are separate, and narrative structure follows the actual
instructions or the documented legacy fallback.

## Steps

1. Read the questions and any supplied schema or private ATS page map;
   classify each as narrative, controlled choice, personal declaration or
   applicant attestation. Record unknown controls without guessing.
2. Match job-evidence questions to the relevant applicant evidence and
   personal questions to the user's supplied answers. Separate unresolved
   items and conditional follow-ups.
3. Prepare exact choice labels or narrative answers according to the
   control-specific rules. Apply the legacy narrative default only where
   its conditions are met.
4. Validate final narrative payloads with `capel-fit` when numeric limits
   apply; revise deliberately and revalidate after changes. Skip fitting
   for controlled labels and for unknown/unlimited fields with no user-supplied
   numeric limit.
5. Present each question with its supported response, separated by a blank
   line between pairs, followed by distinct evidence and confirmation notes.
