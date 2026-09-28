# Shared expertise and employment evidence

This contract is shared by `apex-select-domain-of-expertise` and
`apex-generate-unesco-employment-history`. It governs their paired UNESCO work;
their own `SKILL.md` files remain authoritative for taxonomy/experience rules
and EHF fields/template preservation respectively.

## One run with two cooperating skills

The skill the user invokes is the entry skill. Load its companion once and
complete one sequence: source evidence → broad expertise mapping and qualifying
periods → role-specific EHF narrative → crosswalk and consistency verification.
If both skills are named, perform this sequence once. This is cooperation
between skill contracts, not a requirement to create agents or other tasks.
Do not invoke the companion back and forth or restart the ledger when moving
from mapping to writing. When a source correction requires revision, update
the affected records and both projections within the same run.

| Request | Paired behavior |
|---|---|
| Prepare/generate expertise entries | Create or update the broad map, evidence ledger, EHF content and crosswalk. |
| Prepare/fill/update the EHF | Create or update the EHF and its companion broad map, evidence ledger and crosswalk. |
| Review either artifact | Consult both contracts and available artifacts, then return inconsistencies and proposed changes; do not overwrite application outputs. |
| Explain/research taxonomy or edit the skills | Do that requested work; do not create applicant materials. |
| Explicitly restrict output or edits | Respect the restriction. Use the companion's evidence/consistency method internally, and state any affected companion update left outside scope. |

For expertise-led authoring, a companion EHF **content draft** is the default
when no native form is supplied/requested or already part of this application's
paired artifacts. If a native EHF is requested or is being maintained as part
of the active pair, update that form through its canonical skill and render it.
Do not create a DOCX merely because an unrelated CV was supplied as evidence.
Do not automatically modify the CV, cover letter, screening answers or
publication entries. No portal submission or profile write is included.

Read-only review is not apply intent. The normal paired-authoring request
authorizes its two related outputs; it does not approve every new assertion.
Apply the existing Candidate Assertion and controlled-update rules to new facts.
If the companion contract cannot be located, complete independent work and
identify the missing paired output; do not claim synchronized completion.

## Source and artifact scope

Use primary job history first, then approved factual updates. A CV may supply
facts when history is absent. Do not inherit a prior tailored Admin Profile's
register or promote existing generated prose to independent evidence. A
published work can evidence an attributable activity, not automatic tenure.

Store applicant evidence only in the current requested output location. Use
the active application's output directory; absent one, use
`private/output/unesco_<application-id>/`. Suggested companion files are:

- `expertise_evidence_ledger.json`: roles, exact pairs, evidence and periods.
- `expertise_mapping.md`: broad ranked inventory, per-role relevance and
  the distinct ready/review lists.
- `vacancy_skills.md`: full vacancy requirements and evidence/gaps, if a JD exists.
- `ehf_input.json`: clean field/narrative payload accepted by the EHF filler.
- `expertise_ehf_crosswalk.json` and `.md`: exact evidence-to-paragraph links.
- The requested EHF content or native form and its unresolved-field review.

Existing equivalent names are valid; do not duplicate the same application
under a second naming scheme. Chat-only output uses the same logical records
without saving them. These are user-requested task artifacts, not persistent
memory. Never place applicant evidence, counts, names, employers or vacancy
facts in the reusable skills, taxonomy, scripts or test fixtures.

Give the ledger an application ID, assessment cutoff, vacancy ID (or null for
a profile-wide request), taxonomy snapshot and source-file revisions. Use
source locators plus SHA-256 hashes for local files; preserve identifiable
version/source records for non-file inputs. Do not silently reuse another
applicant, vacancy, taxonomy or source revision. On change, refresh affected
evidence and both outputs; do not overwrite a different application. Keep a
recoverable prior version when replacing existing outputs.

## Evidence model

Use stable role IDs derived from the source chronology; keep successive
appointments separate even at one employer. Preserve title, employer, dates,
direct-report scope, assignment type and source locators. Narrative order can
change without changing role identity.

For every mapped Area/Subarea, retain all evidenced roles and their personal
actions/results. Give each evidence record a stable, unique ID, even when the
same source supports several distinct domains. Capture:

- Exact Area/Subarea labels and IDs, plus vacancy priority separately.
- Role ID, source locators, personal action, attributable result if established,
  and limits on authority or technical scope.
- Role relevance (3 central/repeated, 2 regular/important, 1 occasional).
- Assertion status and integration policy under `AGENTS.md`.
- Qualifying practice intervals and their basis: ongoing role responsibility,
  dated project/activity, or undated/episodic activity. Keep a whole-role period
  only when the source establishes ongoing practice. Retain unresolved periods
  and possible effects on the total band.
- The interval helper's union/bounds, displayed duration, any display convention,
  and separately reasoned ready/held portal choice. Scores never multiply years.

Use exact source precision. Missing practice dates do not make a clearly
attributable action ambiguous: hold its duration/band while retaining supported
role evidence. Conversely, an unallocated organization-sequence claim cannot
be assigned to an individual role simply to populate a field. Keep its role ID
null and explain the missing attribution. Never count an employer's whole
portfolio as personally exercised expertise without supporting responsibility.

Keep a complete taxonomy disposition inventory for a broad run, but do not
put unsupported catalog options into the applicant's mapped/ready lists.
Separate breadth of demonstrated experience from a vacancy-focused entry
recommendation. List core JD capabilities even when no exact dropdown label
exists, while never inventing a portal option to accommodate them.

## Narrative and crosswalk

For every supported role–domain link, the EHF must express the evidence in the
correct job's responsibilities or achievements. Use both only when both remit
and attributable output are established. Combine related evidence naturally;
literal repetition of every taxonomy label is unnecessary. No keyword lists,
relevance scores, cumulative domain years or internal IDs belong in the form.
An episodic course, investigation or systems rollout remains a discrete example.
Do not manufacture an achievement or extend an activity across a whole role to
make the crosswalk look complete.

Map each evidence ID to exact narrative paragraphs using the role ID, zero-based
job index, section (`responsibilities` or `achievements`), zero-based paragraph
index and verbatim text. Several evidence records may point to one paragraph
when it actually supports each. The readable crosswalk should also show the
expertise label, position, period, source and short explanation of the match.
Keep unsupported/unallocated records in a separate held list with reasons.

After every narrative edit, refresh paragraph indices/text and their source
links. If narrative review finds a missing or overstated claim, reconcile the
ledger against primary evidence; do not use the draft to validate itself.
Check the crosswalk in both directions: each eligible evidence record has a
correct-role narrative target, and every narrative claim has primary/approved
support. An uncertainty confined to experience bands must not block otherwise
supported EHF text or independent ready rows.

## Machine-readable linkage validation

The following is the minimum structural contract for the offline
[`validate_ehf_links.py`](../scripts/validate_ehf_links.py) helper. Other evidence,
duration, coverage and requirement fields above remain part of the ledger; the
helper does not infer or verify their factual meaning.

```json
{
  "schema_version": 1,
  "application_id": "example-application",
  "source_manifest": [
    {"source_id": "history", "path": "/task/history.md", "sha256": "<actual SHA-256>"}
  ],
  "roles": [
    {"role_id": "role-a", "job_title": "Example role", "employer": "Example employer",
     "from_date": "01/01/2020", "to_date": "31/12/2021"}
  ],
  "domains": [
    {"area_id": "<verified ID>", "subarea_id": "<verified ID>", "evidence": [
      {"evidence_id": "evidence-a", "role_id": "role-a", "role_relevance": 3,
       "assertion_status": "SUPPORTED", "integration_policy": "OK_TO_INTEGRATE",
       "source_refs": [{"source_id": "history", "locator": "role-a responsibilities"}],
       "action": "Maintained the programme's financial records.", "result": null,
       "practice_periods": [], "period_note": "Activity period needs clarification."}
    ]}
  ]
}
```

The dates, roles and action above are fictional schema examples. Do not copy
them into an application. For a non-file source, replace `path`/`sha256` with
`version` and `description`; the helper cannot verify its current content.

`ehf_input.json` follows the EHF filler contract exactly, with no extra IDs or
review fields inside jobs. A corresponding crosswalk has this structure:

```json
{
  "schema_version": 1,
  "application_id": "example-application",
  "ledger_sha256": "<SHA-256 of the exact ledger file>",
  "ehf_input_sha256": "<SHA-256 of the exact EHF payload file>",
  "links": [
    {"evidence_id": "evidence-a", "targets": [
      {"job_index": 0, "section": "responsibilities", "paragraph_index": 0,
       "text": "Maintained the programme's financial records."}
    ]}
  ],
  "held": []
}
```

Each held record is `{"evidence_id": "...", "reason": "..."}`. Supported,
role-anchored records with `OK_TO_INTEGRATE` must have links; records held by
assertion/role uncertainty must be in `held` and must not have clean targets.
A missing practice period alone is not a reason to hold narrative coverage.

For file-backed authoring, run with explicit paths (relative paths are resolved
from the current working directory; source paths in the ledger must be absolute).
For chat-only work, apply the same linkage checks to the logical records without
creating files solely to run this helper:

```sh
python3 scripts/validate_ehf_links.py --ledger /task/expertise_evidence_ledger.json \
  --ehf-input /task/ehf_input.json --crosswalk /task/expertise_ehf_crosswalk.json
```

The helper checks source/file revisions, role identity, exact paragraph targets,
complete coverage of eligible records and exclusion of held evidence. It reads
only and returns JSON; exit 1 identifies structural or stale-link errors. Its
PASS is **not** factual validation, an experience-band decision, whole-history
coverage proof or a DOCX layout check. Perform the semantic checks above, exact
portal-choice/interval checks and the EHF skill's final text/render checks too.
Review-only or explicitly restricted work may describe gaps without satisfying
the full-pair coverage gate; do not present it as a synchronized generation.
