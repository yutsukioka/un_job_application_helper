---
name: apex-adapt-word-cover-letter
description: Replace explicitly approved date and body text in an existing Word cover letter, rename it as requested, and preserve its original formatting, salutation, contact information and sign-off. Use for faithful DOCX adaptation from approved content, not new cover-letter drafting.
---

# Adapt an existing Word cover letter

Transfer the user's approved content into their existing `.docx`. The existing file is the formatting authority. This is a narrowly scoped adaptation, not permission to regenerate, polish, restructure, or add application content.

Apply [apex-guardrails](../apex-guardrails/SKILL.md) for source grounding and
controlled updates. The selected document and approved transfer scope govern
formatting and wording.

## Inputs and scope

- Identify the exact source DOCX, requested output filename, date, and approved replacement paragraphs. Resolve paths against the current workspace; do not reuse another computer's absolute paths.
- A supplied replacement body or explicitly selected generated cover-letter artifact is the content authority. Preserve its wording and paragraph boundaries. Generated recipient blocks, subject lines, headings, or sign-offs do not expand the authorized replacement region.
- Preserve names, contacts, salutation, signature, closing, and all other content unless separately authorized. Keep `Dear Hiring Manager,` unchanged as the next content line after the date. Add no recipient/selection-panel block, organization address block, `Re:` line, or title.
- Replace the requested date in its existing paragraph. Preserve its right alignment and existing run styling. Do not infer a date from the old filename; use the user's supplied date, or resolve “today” in the user's timezone.
- Rename exactly as requested. Treat dates, vacancy identifiers and filenames as task inputs, never reusable constants. Do not retain application facts or personal information in this skill, its helper, or examples.

## Inspect and make the smallest edit

1. Announce this skill and the actual DOCX/content sources it consumes. Read the relevant existing document and approved source before editing. Inventory paragraphs, run properties, paragraph properties, page/section setup, headers/footers, and affected text. Keep a temporary original for comparison and recovery.
2. Use the available `documents` skill for document mechanics and visual verification, with this user's preservation instructions overriding generic design or writing defaults. Resolve its current package location from the available-skill catalog. Call `load_workspace_dependencies`; use the returned bundled Python/Node runtimes and package paths. Follow the document skill's artifact-operation marker requirement before authoring.
3. Build an explicit edit manifest mapping each original date/body paragraph to its exact replacement. Require an unambiguous match and verify the expected original text before modifying it. If the source differs materially from the requested old text, inspect it and report the mismatch; do not use approximate matches that can alter protected content.
4. Prefer surgical OOXML changes through [scripts/adapt_docx.py](scripts/adapt_docx.py). Read [the edit manifest contract](references/edit_manifest.md) before running it with the bundled Python runtime: `adapt_docx.py INPUT.docx MANIFEST.json OUTPUT.docx --report REPORT.json`. The manifest requires the source SHA-256 and explicit original-XML paragraph selectors with expected text. It supports exact text replacements and cloned paragraph groups. Restrict this task's manifest to the authorized date/body text; do not invoke unrelated edit capabilities.
5. Change text nodes while retaining the original paragraph and run formatting. Preserve `w:pPr`, `w:rPr`, tabs, line spacing, paragraph spacing, indentation, pagination settings, margins, sections, styles, numbering, relationships and embedded objects. Do not reconstruct the whole document or use a save path that silently normalizes its formatting. Preserve every ZIP part except the necessary `word/document.xml` edits; do not modify metadata merely to reflect the rename.
6. Preserve the existing body paragraph count when the approved replacement has the same count. If a different approved count requires insertion/removal, clone the matching original body paragraph's properties and remove only the replaced region's surplus paragraphs. Do not add blank lines or alter paragraph settings to fit text. Never insert a heading or add omitted factual material.

## Verify before replacing the old file

- Compare final extracted text against the exact approved replacement. Confirm the date, body paragraph order and boundaries, unchanged salutation/closing/contacts, and the absence of added recipient/subject/title lines.
- Compare protected paragraphs and OOXML properties with the original. Check that unrelated ZIP parts are byte-identical. A render alone does not establish formatting preservation.
- Render the original and final document with the current documents skill's `render_docx.py`; inspect every final page image at full readable resolution. Use only the absolute path to bundled LibreOffice resolved through `load_workspace_dependencies`, never the user's installed desktop LibreOffice, even if the bundled renderer fails. Carry this path and restriction into any delegated rendering work.
- Confirm the renderer can resolve the original fonts. If it substitutes fonts, expose existing font directories through a task-local renderer configuration; do not change DOCX font settings to match the fallback. If the user requires permission before using Microsoft Word, ask before native-app verification. An original-file rendering defect is not permission to change protected layout.
- Confirm the date remains right aligned, salutation is the next content line, and there is no clipping, overlap, unexpected font change, blank page, or stranded sign-off. Re-render after any correction. Do not shrink text, change paragraph settings, or rewrite the approved body to repair layout. If fidelity and the requested content cannot both be satisfied within the authorized changes, finish an inspectable draft and identify the precise unresolved constraint.
- Inspect the full final text for placeholders, including bracketed confirmation requests, missing-detail tokens and incomplete drafting instructions. Do not silently resolve or delete them. Check approved replacements against available role/date/metric evidence and protected content for contradictions; source approval does not make a conflicting assertion factual. Do not integrate a newly discovered unresolved factual conflict; preserve the affected source text, complete independent edits, and identify the exact conflict.
- Save under the exact requested new name. Recheck the original file's hash before final replacement to detect concurrent edits. For an explicit rename/replacement request, remove the obsolete name only after validating the new DOCX and retaining a recoverable original in temporary storage. Do not overwrite an unrelated existing destination, delete a Word lockfile, or discard unsaved Word content.

## Deliver

Return the final DOCX and a concise description of the changes. If any placeholder remains, state **PLACEHOLDER WARNING** prominently and quote each unresolved item with its location. If content contradicts the source or protected material, state **CONTENT CONFLICT** prominently and identify both versions; distinguish affected text left unchanged from completed edits. Do not bury these alerts in a QA file or imply the application is ready when a conflict remains. Do not deliver render intermediates unless requested.
