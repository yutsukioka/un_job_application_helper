# Explicit DOCX edit manifest

Use the bundled document Python interpreter:

```text
python scripts/adapt_docx.py INPUT.docx MANIFEST.json OUTPUT.docx --report REPORT.json
```

The output and optional report must be new files. The report must differ from
the input, output and manifest. The helper does not rename,
delete or replace the source. Keep manifests and reports in task-local temporary
storage, not in the reusable skill: they contain application content.

The JSON object contains `source_sha256` (the inspected input's SHA-256) and
`operations` (an ordered list). Select paragraphs using XPath with the supplied
`w` and `w14` namespaces. All locators resolve against the original document before
edits; insertion therefore does not shift later selectors. Body paragraph 1 is
`/w:document/w:body/w:p[1]`. Inspect all editable parts, including headers and
text boxes, rather than assuming that the body contains the complete document.

Supported operations:

- `replace_text`: `part`, `xpath`, `expected`, `replacement`. The expected string
  must occur once within the selected paragraph. A substring can target the date
  or UVP while keeping surrounding text intact. Replacement text must stay within
  one paragraph. Existing runs and properties remain; differently formatted runs
  require narrower replacements rather than guessed formatting.
- `replace_paragraph_group`: `part`, `xpaths`, `expected`, `replacements`, `kind`.
  `xpaths` selects consecutive original paragraphs and `expected` supplies their
  complete texts. `replacements` supplies the approved new paragraph texts. Existing
  slots retain their own properties; additional paragraphs clone the last original
  paragraph's properties. Unneeded original slots are removed only inside this
  explicitly mapped group. `kind` is `bullet` or `text`; `bullet` requires native
  Word dash numbering and rejects hand-typed leading bullet markers.
- `insert_blank_before`: `part`, `anchor_xpath`, `template_xpath`, `count`, `reason`.
  The template must be an existing empty, unnumbered paragraph. Reasons are
  `organization_boundary` or `pagination`. Use the latter only when a rendered
  page demonstrates a stranded heading/role block and the user permits spacers.

All operations require `type`. Package parts not named in an edit remain
byte-for-byte identical, including styles, numbering, relationships and images.
The helper verifies protected paragraphs and source hashes, and refuses overlapping
edits, unsafe rich-content cloning, ambiguous text and differently styled ranges.
It is intentionally not a general DOCX editor. For unsupported structures, inspect
and adapt the method narrowly; do not flatten rich text or bypass fidelity checks.
Scalar replacements reject paragraphs containing fields (including cached field
results), revisions, controls or drawings, and paragraphs nested inside such
structures. Paragraph groups and cloned spacers additionally reject hyperlinks,
range anchors and explicit line/page/section breaks, including in original slots
that a shorter replacement group would remove. These guards apply before output
creation; a rejected manifest leaves no output DOCX.

Run the prescribed document-operation marker before the first DOCX edit, and
render every final page using the document skill. Structural PASS does not certify
pagination, font availability or layout. Verify actual numbering indents, repeated
header size, orphaned titles, and unchanged protected regions against the original.
