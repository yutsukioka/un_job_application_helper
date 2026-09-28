# ATS input skill consistency review

Reviewed 27 September 2026 while adding `apex-unops-ats-input`. Scope is reusable
skill behavior and discovery, not a new applicant-generation or portal-entry run.

## Shared behavior

The five ATS skills now require the same
[Codex Browser contract](../agents/apex/skills/apex-guardrails/references/ats-browser.md).
The documented browser surface of the unified `cua_repl` runtime is permitted;
native-app/desktop automation is excluded. Discovery checks directly exposed and
deferred tools. A browser-panel opener alone is not an interaction capability.
Unsupported uploads remain pending for a specific manual handoff and verification.

Previously, UNICEF accepted the browser surface, while Inspira, IOM and WMO
prohibited the entire tool name. IOM repeated that prohibition in its page guide.
Those contradictions are removed. Organization-specific entry workflows and
permissions remain in their canonical SKILL.md files; UI metadata adds no rules.

All five preserve unselected data, use current controls, match employment by
identity rather than row order, transfer selected approved content, verify saved
state and leave final submission/certification to the user. Existing authorization
is reused. An uncertain save/upload requires observation before retrying.

## Organization-specific contracts

| Skill | Scope and sources | Fields and constraints | Preserved distinction |
|---|---|---|---|
| [UNICEF](../agents/apex/skills/apex-unicef-ats-input/SKILL.md) | Existing PageUp draft; selected Option 1, final files and supplied personal answers | Responsibilities plus specifically selected administrative/referee fields; live caps and uploads | Returns to Home when available. Its existing placeholder rule stops entry for unresolved responsibilities/upload-file placeholders; untouched metadata is treated separately. |
| [Inspira](../agents/apex/skills/apex-inspira-ats-input/SKILL.md) | Existing draft or expressly requested start using the most recent eligible prior application; Options 1/4/7 and requested PDFs | Duties, qualification answers, motivation and attachments in the PeopleSoft workflow | Retains its 1,000/2,000-character field guidance, checked against the actual portal, and prior-application copy behavior. These are not UNOPS defaults. |
| [IOM](../agents/apex/skills/apex-iom-ats-input/SKILL.md) | Oracle WAVE inspection/extraction/comparison, selected preparation or approved draft entry | Separate responsibilities/achievements and observed skill/language dimensions | Context updates require an explicit request; another active context is kept separate. |
| [WMO](../agents/apex/skills/apex-wmo-ats-input/SKILL.md) | Oracle inspection/extraction, selected preparation or draft entry | Actual controls rather than inferred IOM equivalents | Keeps application_context.md unchanged and leaves its integration to the user. |
| [UNOPS](../agents/apex/skills/apex-unops-ats-input/SKILL.md) | Careers Marketplace inspection/comparison or selected draft/profile revision; existing approved artifacts and Option 10 where relevant | Description of Duties, per-role Position Area, live skill labels, actual screening controls and requested attachments | Distinguishes shared-profile scope; checks live labels against dated catalogs; does not presume a 20-skill platform rule, narrative cap or proficiency scale. |

These differences are retained deliberately. Cross-system consistency means a
shared browser and evidence/scope discipline, not identical field schemas, page
counts, initialization behavior or placeholder policies.

## UNOPS contract review cases

The following are local scenario reviews of the written workflow, not executed
browser tests or evidence of a successful upload. No live application was edited.

| Scenario | Required behavior under the new contract |
|---|---|
| User requests a duties-only revision; current shared context targets another organization | Use the selected UNOPS artifact and map each role; preserve other fields and the shared context. No mandatory rerun of Option 10. |
| Only the unified runtime exposes an in-app browser | Use its documented `iab` browser surface after reading current documentation; no desktop actions. |
| A native file chooser appears and the browser upload times out | Inspect upload state before another attempt; if unsupported, report the exact pending upload and hand it to the user without desktop fallback. |
| A newer live selection differs from an older dictionary plan | Verify identity/provenance, reuse only the later verified choices, and assess unavailable labels through the fit workflow within selection authorization. Preserve the older snapshot. |
| User asks to add one skill; the profile already reaches its observed limit | Do not silently remove a different skill. Prepare the concrete replacement question or leave the addition pending. |
| User restricts changes to the application but the control edits a shared profile | Prepare the exact change set and clarify only that scope before writing. Existing profile-edit authorization needs no repeat approval. |
| Position Area has duplicate labels with different values | Do not assume equivalence or use a guessed ID. Resolve from current guidance or hold that affected selection. |
| A clean final CV is selected while an older text draft has placeholders | Inspect the actual final upload artifact; irrelevant old placeholders do not block it. |
| A selected payload has an unresolved fact; other fields are clean | Hold the affected field/file and continue independent approved edits. Do not remove the gap or invent a value. |
| User edits a field while the agent prepares changes | Refresh values; preserve unrelated edits and reconcile changed targets before saving. |
| A route is named ApplicationConfirmation | Inspect rendered status; the route alone proves neither submission nor an editable draft. |
| Skill installation/setup is requested | Create and validate local skill artifacts and discovery links; do not enter or submit application data. |

## Readiness and limits

The new folder, frontmatter name and `$apex-unops-ats-input` prompt use the same
name. Repository catalogs and the AGENTS template route to it. The fit skill links
to ATS entry only for a separate authorized handoff. The new skill references the
shared browser rules and a dated, applicant-free UNOPS field guide.

Run the skill-creator validator and check local reference links before installation.
Expose the canonical folder through the repository's existing reversible
`~/.agents/skills/` symlink convention; preserve conflicting installations.
If Codex's picker is stale, restart it. A direct canonical SKILL.md invocation
can be used in the current task. Actual portal access, authentication and upload
support must still be established at invocation; installation does not prove them.

Completed checks: the skill-creator validator passed for UNOPS ATS input, UNOPS
application fit, UNICEF, Inspira, IOM, WMO and guardrails. The six scoped UI
invocation names and local skill-reference links passed checks. Whitespace checks
passed for the edited tracked catalogs. The UNOPS discovery symlink was created
and verified to resolve to the canonical folder, with identical SKILL.md contents.
No live browser entry, upload or submission test was performed.
