# Inspira browser reliability

Read this reference for the PeopleSoft application editor, especially repeated employment edits and attachment replacement. Use Codex Browser and its current browser-specific documentation for actual method signatures; this is not an API specification. The parent skill prohibits Computer Use and coordinate-based fallbacks.

## Current page and target identity

- Inspira commonly places the application inside a frame and opens employment, upload and save dialogs in separate modal frames. Discover the actual frame IDs from the current DOM. Modal identifiers and row suffixes change; never reuse a prior modal ID after closing it or assume a suffix always identifies the same role/file.
- After opening a dialog, wait for its visible form and the expected field to be available, then verify the role title or dialog purpose. A frame shell can appear before its content loads. Reading empty controls or an old frame is not evidence that the application lost data.
- Step navigation may contain a graphical link and a text link with the same ID. Select the link with the observed label rather than blindly using a duplicated ID or a first match.
- Map employment entries by employer, title and dates. Map attachments by actual description and filename. Uploads, deletions and reopening a saved application can reorder attachment rows.

## When a control appears unresponsive

- Inspect the current page for processing indicators, modal dialogs, validation messages and changed row contents. Do not immediately repeat a mutation because the first snapshot still shows the old page.
- Accessibility overlays may label controls as generic documents or hide them from the accessibility tree. A DOM snapshot and read-only inspection of visible form controls can supply the correct labels and values.
- A semantic click can fail to reach an offscreen control on a horizontally or vertically scrolled page. Inspect its visible location, scroll the page to expose it, and retry the observed semantic control. If supported browser locators and scrolling cannot reach the control, report that step as blocked; do not switch to Computer Use or screenshot coordinates.
- A supported Codex Browser locator keyboard activation such as Enter can operate a specifically identified link or Save/Cancel button. Focus only that exact control; do not send an unscoped Enter that could activate a different default button.
- Treat saving and navigation as asynchronous. Wait for the relevant dialog to close or the expected destination field to appear. Then inspect current values. If a wait times out, re-observe before retrying: the operation may have finished after the timeout.
- Reload only when a page is stuck and the relevant work is known to be saved. Reopen the draft by its vacancy number and check the restored step. If unsaved user changes may be present, resolve their disposition first.

## Safe repeated form updates

- Process employment records sequentially. A batch may automate the repeated sequence only when it reads the newly opened form, verifies the expected role and stops on an unexpected state.
- Before each edit, snapshot editable values. After filling, require that only the duties field changed and that its text exactly matches the approved payload within the character limit.
- After saving the draft, reopen each edited record and check the persisted duties and unchanged protected values. Close these read-only verification views with Cancel.
- For repeated uploads, use the browser's documented file-chooser capability and inspect the selected filename before Upload. Do not attempt arbitrary file-input mutation through page evaluation.
- Keep browser reads DOM-backed. Do not inspect application internals, hidden session state, cookies or private network endpoints to accelerate the task.

## Approval versus technical failure

A delayed page or selector error is a technical problem. An automatic approval rejection is an authorization boundary. Do not use the recovery techniques above to bypass an approval rejection. Obtain the required confirmation for the same exact action, then resume through the supported Codex Browser interface.
