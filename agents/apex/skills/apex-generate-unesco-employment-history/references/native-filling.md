# Native Word filling

Use `scripts/fill_ehf.py` for the bundled standard EHF. The helper needs Python
3 and `lxml`; the Codex workspace dependency loader supplies both. Resolve the
runtime through that loader. Do not install packages into this repository.
For other environments, use an already available Python environment with
`lxml`. Document rendering is a separate required QA step.

## Prepare input

Write a task-local JSON file using the exact keys below. These are synthetic
examples, not reusable applicant facts. Every key must be present. Use `null`
for an unresolved field, not a invented date, zero, reason or salary. Plain
strings preserve Unicode and punctuation. Narrative strings become one
paragraph; arrays create separate paragraphs. Use paragraph arrays instead
of embedded newlines or tabs. Do not place internal evidence references in
this JSON: keep the field/source mapping in the separate review.

```json
{
  "first_name": "Élodie",
  "last_name": "Example",
  "jobs": [
    {
      "from_date": "01/01/2025",
      "to_date": "Present",
      "job_title": "Synthetic Programme Officer",
      "employer": "Example Organization",
      "location": "Nairobi, Kenya",
      "annual_salary": "USD 60,000 gross annually (synthetic)",
      "direct_reports": "2",
      "un_grade": "Not applicable",
      "responsibilities": ["Coordinated the synthetic programme's research briefs."],
      "achievements": ["Produced six briefing notes for a synthetic review."]
    }
  ]
}
```

The helper accepts the jobs in the supplied present/recent-first order. It
checks calendar dates, start/end consistency and obvious ordering issues;
it does not infer dates, sort concurrent roles, calculate tenure or verify
factual support. Confirm the chronology before calling it. `Present` is a
practical ongoing designation supported by the helper, not a choice list or
a formally verified UNESCO rule. Other supplied conventions require the
applicable document workflow rather than silent conversion.

The two name controls have a native Word maximum of 20. Check the exact name
text through the sibling CAPEL native validator with no normalization:

```sh
"$PYTHON_BIN" "$APEX_SKILLS/capel-fit/scripts/validate_text.py" \
  --file "$TMP_DIR/first-name.txt" --char-limit 20 --unit utf16 --json
```

Repeat for the last name. Files must contain only the intended field text,
without an incidental trailing newline. UTF-16 is a conservative local check;
the source Word field does not establish Unicode counting semantics. Neither
the helper nor CAPEL should abbreviate or truncate a name. Resolve a conflict
before presenting a completed form. The remaining 40 legacy short-text form
fields have no explicit maximum; responsibility and achievement cells are
ordinary Word paragraphs with no numeric cap in this asset.

## Fill a new file

Set `$SKILL_DIR` to this skill's directory; `$TMP_DIR` to a task-local writable
directory; `$PYTHON_BIN` to the resolved interpreter. `$OUTPUT_DOCX` must be a
new path. The helper is read-only toward its template and input:

```sh
"$PYTHON_BIN" "$SKILL_DIR/scripts/fill_ehf.py" \
  --input "$TMP_DIR/ehf-input.json" --output "$OUTPUT_DOCX" \
  > "$TMP_DIR/ehf-fill-review.json"
```

For an explicitly identified incomplete review draft, add `--allow-incomplete`.
Unknown slots stay blank and the report lists their paths. That option also
permits unresolved chronology warnings; the draft remains incomplete. It
does not bypass name limits, invalid dates or malformed/unknown keys.

Exit 0 means the file was generated, **not** that content or layout has passed
QA. Exit 2 means an invalid input, unsupported template or output conflict.
The JSON report contains field paths and counts, not applicant field values.
The script refuses to overwrite an existing output. An optional `--template`
accepts only a byte-identical copy of the verified bundled file; a different
template must be inspected and handled through document tools.

## Preserve and check

The filler changes `word/document.xml` plus `word/settings.xml` to request
field refresh on opening. Every other package part remains byte-identical,
including the logo, headers/footer, styles, relationships and opaque custom
XML. It preserves legacy `FORMTEXT` controls and updates cached result text;
it does not flatten controls. It preserves the two native continuous A4
sections, row/cell layouts, labels and source typography.

Unused job blocks and surplus blank answer paragraphs are removed. More than
five jobs clone the last blank job pattern, with new bookmark/field identifiers.
No field label is removed. Each narrative paragraph takes the relevant source
paragraph's formatting; the helper does not shrink text. The template itself
uses small form text, so inspect legibility and do not make it smaller to fit.

Use the documents skill's retained-template workflow: inspect the source,
record task-local `artifact.md`, render the reference and final, and compare
package/section structure. The package manifest is
[template-manifest.json](template-manifest.json). A fresh run can reuse its
source provenance and slot map in [form reference](form-reference.md), but
must inspect the actual final pages. Use `render_docx.py` and, for fidelity
review, `scripts/render_and_diff.py` from the available documents skill.

Check all populated values in extracted output text and every page image.
Pay particular attention to achievements rows, long employer/title text,
jobs beyond five, page-boundary splits, Unicode glyphs and footer numbers.
If a field continues onto another page, ensure its label and content remain
understandable. Revise the answer length or source-derived paragraph flow
when necessary, retaining all factual content that is required by the form.

The helper sets Word's update-fields flag because the source footer contains
a `PAGE` field. Word refresh has not occurred just because rendering succeeded.
The render can verify visible page numbers; do not save the delivered DOCX
through headless LibreOffice merely to refresh fields, since that may rewrite
unrelated package parts. If PDF is requested, validate the exported PDF and
check the applicable upload instructions separately.
