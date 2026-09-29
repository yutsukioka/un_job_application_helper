---
name: apex-adapt-word-cv
description: Adapt an existing Word CV using approved UVP, Summary, Skills and experience bullets while preserving contact details, role headers, education, references, native dash bullets and original formatting. Use for faithful DOCX content transfer, not CV regeneration or redesign.
---

# Adapt an existing Word CV

Transfer approved application content into the user's established CV. Preserve the template's formatting and all protected information exactly. Do not invoke CV-generation rules that change headings, role lines, section order, wording, or document design.

Apply [apex-guardrails](../apex-guardrails/SKILL.md) for source grounding and
controlled updates. The selected document and approved transfer scope govern
formatting and wording.

## Inputs and permitted replacements

Identify the original DOCX, exact requested output filename, approved UVP, generated `Summary`, generated `Skills`, and approved experience bullets for each role. Resolve file paths for the current workspace. Dates and vacancy identifiers in output names are task inputs, never constants stored in this skill. Do not copy personal information into the skill or helper.

| Existing region | Authorized content | Preserve |
| --- | --- | --- |
| Short profile statement near the contact block | Approved UVP statement | Contact details, name and all surrounding content; original paragraph/run styling |
| `QUALIFICATION SUMMARY` body | Approved generated `Summary` | Existing heading text and formatting |
| `CORE COMPETENCIES` body | Approved generated `Skills` | Existing heading and list formatting |
| Each role's experience bullets | Corresponding approved generated role bullets | Entire role header and organization/date/duration lines |

Everything else is protected unless the user explicitly selects it for replacement. In particular:

- Preserve each role's original two-line header, including title, period, date spelling, employer, organization type, duration, full-time label, tabs, alignment, and line structure. A generated single-line title/employer/date heading is a mapping aid only; do not paste it into the CV.
- The short UVP statement may be in a `word/header*.xml` part rather than the document body. Locate it by inspecting the DOCX package. Target only its approved text in that part and preserve every other header item, including contacts, tabs and styling.
- Preserve education, references, certificates and other unselected sections. Preserve their exact wording; any user-requested warning exemptions apply only to the explicitly named content.
- Retain the existing role order and match each bullet group by organization, title and period. Do not match by organization alone when it has multiple roles. If a supplied role cannot be mapped unambiguously, hold only that group and identify the ambiguity.
- Use approved text verbatim, retaining meaningful categories and qualifications. Strip only source-format bullet markers or Markdown delimiters that the Word formatting already supplies. Do not shorten, combine, polish, add claims, or derive a new UVP without authorization.

## Preserve native Word formatting

1. Announce this skill and the DOCX, UVP, Summary, Skills and experience sources it consumes. Inventory the original's paragraphs, text runs, native numbering definitions, paragraph properties, page/section settings and protected regions. Keep a temporary original for comparison and recovery.
2. Use the available `documents` skill for document mechanics and verification. This user's exact-preservation rules override generic document design and writing defaults. Resolve the current skill package from the available-skill catalog. Call `load_workspace_dependencies`; use its returned bundled runtimes and dependencies, and follow the document skill's artifact-operation marker requirement before authoring.
3. Build an explicit edit manifest with the source SHA-256, exact expected old text, replacement text and role-bullet group boundaries. Use [the shared OOXML helper](../apex-adapt-word-cover-letter/scripts/adapt_docx.py), reading [the edit manifest contract](../apex-adapt-word-cover-letter/references/edit_manifest.md) before running it with the bundled Python runtime: `adapt_docx.py INPUT.docx MANIFEST.json OUTPUT.docx --report REPORT.json`. Use paragraph text replacements for the UVP/Summary/Skills and group replacement/cloning where approved bullet counts differ. Resolve selectors against the original XML; require unambiguous anchors instead of guessing after insertions shift paragraphs.
4. Preserve `w:pPr`, `w:rPr`, `w:numPr`, tabs, line/paragraph spacing, indentation, styles, margins, page/section setup and pagination settings. Preserve all unrelated DOCX ZIP parts byte-for-byte. Patch only the explicitly selected document/header XML parts; do not reconstruct or broadly re-save the document. Keep protected paragraphs and their formatting unchanged.
5. Experience bullets must use the source's Word-native dash numbering. Keep the existing numbering ID/level and dash glyph; do not insert a literal hyphen, manual bullet, or tab at the start of replacement text. Keep original fonts and the template's paragraph settings. When the user describes 0.25-inch indentation and 0.25-inch hanging indentation, inspect both paragraph and numbering-level properties to distinguish the bullet position from the text position. Preserve the actual source settings; if they differ from the user's explicit numeric expectation, alert the user instead of silently normalizing them. Preserve inherited style/numbering settings rather than rewriting them as direct formatting.
6. If a role has more approved bullets than before, clone a corresponding original bullet paragraph with its complete paragraph, run and numbering properties, then replace only its text. If fewer, remove only surplus old bullet paragraphs. Preserve per-paragraph formatting where it differs intentionally. Never reduce the approved content just to retain the old bullet count.
7. Do not insert blank paragraphs within one role or between roles at the same organization. Keep exactly one blank paragraph between different organizations. Remove accidental duplicate spacers only within this selected experience region. Retain or add extra spacers solely when needed for the user's appearance exception: keeping a position's title, dates and organization lines with its opening experience text instead of stranded at a page bottom. Clone an existing spacer; do not alter paragraph spacing, fonts, keep-with-next settings, manual page breaks or section formats to achieve this. Do not delete existing appearance spacers blindly before rendering.

## Verification and delivery

- Compare final content with the exact approved UVP/Summary/Skills/role bullets. Check complete role coverage and make sure no old bullet text remains unintentionally. Compare every protected role header, contact block, education, references and certificates with the original, including formatting and tabs.
- Verify unchanged paragraph/run/numbering properties and unrelated ZIP-part hashes. Inspect native bullet definitions and effective indentation; visible dash characters in a PDF do not prove native Word bullets.
- Render the original and final DOCX using the current documents skill's `render_docx.py`, and inspect every final page image at full readable resolution. Use only bundled LibreOffice at the absolute path resolved through `load_workspace_dependencies`, never the user's installed desktop LibreOffice, even if bundled rendering fails. Include the path and restriction in any delegated rendering work.
- Confirm the renderer uses the source fonts, including inherited fonts. Make existing font directories available to the renderer if needed; never edit the CV's fonts to compensate for renderer substitution. Compare suspicious repeated-header or tab-wrapping behavior with the original. When native Word verification is needed, respect any user instruction to ask before controlling the app; do not assume a renderer discrepancy authorizes template changes.
- Check for clipped or overlapping text, changed fonts, blank pages, excessive gaps, and role titles or dates stranded at a page bottom. Apply only the authorized spacer exception to address pagination; preserve paragraph/section formats. Re-render after edits. If exact content and protected formatting cannot both be satisfied, report the specific unresolved layout constraint rather than silently changing either.
- Sweep the final document for placeholders, including bracketed confirmations, missing details and incomplete drafting instructions. Do not guess values or silently delete unresolved markers. Check approved replacements against available evidence and protected titles/dates/metrics for contradictions. Do not integrate a newly discovered unresolved factual conflict; leave the affected source wording intact and complete independent changes.
- Save under the exact requested new filename. Recheck the original file's hash before final replacement to detect concurrent edits. On an explicit rename/replacement request, remove the obsolete name only after the new file is verified and a recoverable original is held in temporary storage. Do not overwrite an unrelated destination, delete a Word lockfile, or discard unsaved Word content.
- Return the final DOCX and a concise completion statement. For every remaining placeholder that the user has not explicitly exempted, prominently state **PLACEHOLDER WARNING** with its exact text and location. For contradictions, state **CONTENT CONFLICT**, identify both versions and the affected content left unchanged. Keep these alerts in the user-facing response, not only an internal report. Do not deliver render intermediates unless requested.
