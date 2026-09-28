# Phase 8 to UNU P11 mapping

| P11 destination | Preferred source | Transfer rule |
| --- | --- | --- |
| 21 Employment record and duties/related accomplishments | Option 1 admin profile, Option 5 responsibilities/achievements, or Option 8 duties/responsibilities/achievements | Select one source per role. Combine complementary sections once in the 300-word narrative. Context/admin profile supplies selected exact factual metadata. Do not add a Reason for Leaving field; none exists in this version. |
| 23 Motivational Statement | Selected Option 7 motivation; otherwise selected Option 3 cover-letter body | Must answer the P11 career-goals and character/experience prompt. Remove letter furniture only; do not assume an unrelated vacancy's statement applies. Retain shorter approved text; maximum 600 words. Hold missing prompt coverage for revision if rewriting was not requested. |
| 1–11 personal/contact information; 17 education | Option 2 CV plus supported admin profile | Transfer only selected known facts. Exact degree titles in original language; no invented birth, citizenship, enrolment or date components. |
| 16 Languages | Option 9 language evidence plus explicit applicant proficiency | Native choices are Basic, Confident, Fluent, separately for Speaking, Reading, Writing and Understanding. Do not equate High/Medium/Low or Option 6 relevance/tenure with these choices. Fluency requires Fluent in all four; knowledge requires Confident/Fluent in at least two. Missing modality evidence stays unresolved. |
| 17B Training; 18 Licenses and Certificates | Option 9 supported credentials and Option 2 factual entries | Preserve credential status and dates; planned training is not completed training. No inferred equivalence. |
| 19 Professional Memberships; 20 Publications | Selected supported CV/admin profile entries or separately approved publication list | Preserve attribution, original titles, publication status, dates and outlets. Not every written work is a published work. |
| 22 References other than supervisors | Explicit referee details / supported selected admin profile | Form asks for three references if fewer than three supervisors; no personal/family references. No fabricated contact data or consent. |
| 12–15 personal declarations; 21 contact objections; 24–25 misconduct | Exact question-matched user answers; Option 4 only if it answers this same native question | A portal citizenship question is not a P11 misconduct answer. Respect branching and native Yes/No/N/A controls. Do not infer negative answers. |
| 26 Certification, date, signature | Applicant | Preserve wording and leave applicant action pending; no synthesized signature or completion-date assertion. |
| Option 6 competency mapping | Mapping aid only | Do not paste scores or total skill tenure into the form. |

The public portal's three screening questions remain separate; there is no arbitrary P11 slot for them. Skills without a native field may appear in already selected role/motivation wording, but do not create a skills section or add unsupported claims.

## Retained blank template

`assets/UNU-P11_Personal-History-Form.docx` is a byte-for-byte copy of the user-supplied blank file, not a candidate record. Never write applicant data into it. Its fingerprint is in `template-inventory.json`. All row references below are zero-based physical `w:tr` indexes; Word libraries may repeat merged cells, so select actual XML cells.

- Language rows 15–20; education rows 23–26.
- Training answer row 28; certificates 30; memberships 32; publications 34.
- First employment narrative row 44; subsequent narrative rows 53, 61, 69, 77, 85, 93, 101, 109, 117.
- Reference rows 120–122; motivation answer row 124.
- Misconduct rows 125–126; certification row 127.

Inspect actual label text and native controls before resolving these hints. Legacy field names are not assumed unique. Use exact XML paths and hashes; treat `Text129` or `Text172` as hints, never sole global selectors. For a supplied filled or revised DOCX, re-inventory it rather than transplanting locators from this blank file.
