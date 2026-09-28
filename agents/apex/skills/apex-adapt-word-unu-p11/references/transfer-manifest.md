# Native P11 transfer manifest

Run with the bundled Python runtime (requires lxml). Keep candidate manifests and reports private, never in the skill folder.

```text
python scripts/p11_fields.py inspect INPUT.docx > private/working/p11_inventory.json
python scripts/p11_fields.py apply INPUT.docx private/working/manifest.json OUTPUT.docx --report private/working/report.json
```

The output must not exist. Inspection is read-only and may include personal data from a filled source. Use the source SHA-256 and exact XPath, type and expected value from the inventory. Do not select by legacy name alone or copy example paths from another file.

```json
{
  "source_sha256": "SHA-256 of the inspected DOCX bytes",
  "operations": [
    {
      "xpath": "Exact field XPath from inventory",
      "kind": "text",
      "expected": "Exact cached text from inventory, including blank en-spaces",
      "value": "Selected duties and accomplishments text.",
      "source_ref": "Selected Option 5 file, exact employer/title/dates and section",
      "word_limit": 300
    }
  ]
}
```

`kind` is `text`, `dropdown`, or `checkbox`. All require `source_ref` and `expected`. Text values support LF line breaks; no tabs, CRs, rich text, or Markdown conversion is performed. Text changes preserve field instructions, boundaries, bookmarks and run/paragraph properties and synchronize `w:textInput/w:default` with the cached result. Mixed-style result runs and nested fields are rejected. `word_limit` and `char_limit` are optional positive integers determined from the native field, not guessed from a generic ATS. The retained employment and motivation labels also activate hard ceilings of 300/600 words. Inspect revised templates before applying; the helper does not infer all possible limits, in particular the item 24/25 limits must be included explicitly.

A dropdown requires a string exactly matching an inspected display choice; it updates the visible result and native lastValue and clears only the placeholder-display flag. A checkbox requires JSON `true` or `false` and preserves the native control. Set all selected members of a Yes/No/N/A group explicitly to avoid multiple checked answers. Consent and declaration answers require explicit source evidence; this helper cannot establish consent or decide answers. Certification/signature-row edits are rejected.

Operations target only existing controls in word/document.xml. Unsupported fields are listed but not editable; cross-paragraph/nested fields, rich result structures, extensions to tables and new job blocks require a separately reviewed method. The helper validates source hashes, expected values, limits, unchanged unselected field values and byte-identical unrelated package parts. It does not check factual grounding, semantic row/role selection, completeness, layout or every formatting invariant. Inspect the XML diff for changes outside intended field content and allowed state/default attributes, then render and inspect every final page.
