---
name: apex-match-live-vacancies
description: Match a candidate's evidenced experience against the current live vacancy database, reassess earlier omissions, and create or update a vacancy shortlist with explicit eligibility and source checks. Use for vacancy discovery and shortlist refreshes, not application-document generation or portal submission.
---

# apex-match-live-vacancies

Apply the work mode, expert lens, guardrails, truth hierarchy, quality loop,
and guiding principles in [apex-guardrails](../apex-guardrails/SKILL.md).
Use AUTHORING for the shortlist and INTERNAL_QA for validation. This skill
does not activate Phase 8 generation or alter the active application context.

## Inputs and scope

Use the user's specified database, candidate evidence, workbook and preferences.
Repository defaults are `private/jobagg/output/all_jobs.sqlite3` and the
`USER_JOB_HISTORY_TEXT` section of `private/inputs/application_context.md`.
Use only `APPROVED_UPDATES` from the optional
`private/inputs/user_feedback_updates.md` as additional factual evidence.
Do not learn applicant skills from the target JD, prior generated answers,
unresolved feedback or a spouse's experience. Citizenship, work authorization,
internal appointment status and proficiency require explicit applicant evidence.

For an existing shortlist, retain its scope and user-maintained information.
Use the supplied geographic, contract and remote-work preferences. Without
explicit preferences, retain broad discovery coverage and mark unresolved local
eligibility for review. Do not convert preferences into citizenship assertions
or automatically reject all local contracts.
Plausible local or home-based roles with unresolved eligibility may be retained
as clearly conditional review entries. A user-supplied recommendation list is
an additional discovery source, not approval of eligibility or fit.

Read [review protocol](references/review.md) before evaluating candidates.
Read [workbook workflow](references/workbook.md) when a workbook change is
requested, and apply the available Spreadsheets skill. Review-only requests
produce analysis without editing the workbook.

## Run a current, complete scan

Use [scan_live_jobs.py](scripts/scan_live_jobs.py) for consistent read-only
extraction and triage. It is a discovery aid, not the final matcher.

```bash
python3 agents/apex/skills/apex-match-live-vacancies/scripts/scan_live_jobs.py \
  --database private/jobagg/output/all_jobs.sqlite3 \
  --profile private/inputs/application_context.md \
  --output-dir private/outputs/job_shortlist_refresh/RUN_ID \
  --force-id <source:vacancy-id>
```

Add `--feedback` only when that file exists. Pass a timezone-aware `--as-of`
for reproducible evaluation; omission uses execution time. Runtime paths come
from the available dependency loader. Resolve symlinks rather than assuming a
mounted volume or a historic database copy. Record the resolved database,
publication generation, snapshot time, profile hashes and coverage counts.

Scan the full open inventory on every run, including older postings and rows
with missing posting dates. Force previously shortlisted IDs and specifically
requested/recommended IDs into the review even if their stored status changed.
Never restrict refreshes to jobs first seen since the previous run. Inspect
unprioritized and incomplete-title records in manageable batches to catch
adjacent functions or multilingual titles; a title keyword is not a knockout.
Broad searches must include the candidate's actual finance, procurement,
operations, assurance, grants, policy, data, learning and other evidenced work.

The helper records publication readiness. Do not read a knowingly incomplete
database transaction. A consistent database snapshot can be used while exports
are pending only when database transactions are marked complete and the
generation is stable; disclose that boundary. Missing publication metadata or
uncertified source completeness is uncertainty, not proof of full coverage.
Do not invoke ingestion jobs or modify the database as an implicit side effect.

## Decide from requirements and evidence

For each considered vacancy, distinguish:

- **Eligibility:** deadline/status, nationality or residence/work rights,
  internal-candidate restrictions, mandatory languages, degree and professional
  qualifications. Record met, contradicted or unresolved with source evidence.
- **Functional fit:** actual tasks, qualifying experience, sector depth,
  seniority and work modality, supported by specific applicant roles.
- **Desirable criteria:** useful differentiators, not mandatory exclusions.

Read the substantive requirements and relevant duties, not just labels or
repeated organization descriptions. A LICA, GS, NPSA or home-based label alone
does not prove a nationality restriction or global accessibility. Prefer the
official vacancy body when classification fields conflict. Resolve material
conflicts or hold the entry for review. Never treat employer recommendation
labels, retrieval scores or broad transferable experience as hiring probability.

Validate dates as dates. Retain timezone and precision. A date without a time
does not become a verified midnight deadline, and a missing/ambiguous deadline
does not become expired. A clearly expired vacancy is excluded from new open
entries even if it was a missed match yesterday. Preserve existing historic
rows and statuses unless the user requests removal. Verify current official
notices for proposed additions when accessible; otherwise accurately label
database-observed status and the remaining verification need.

Use the workbook's existing Strong / Good / Conditional labels consistently:
Strong needs direct evidence for core work and no known mandatory conflict;
Good needs credible transferable evidence with named checks; Conditional has
a material but plausible unresolved criterion. Clear mandatory conflicts are
excluded rather than disguised as Conditional. Do not equate unknown local
eligibility with a confirmed conflict. Separate roster calls from actual posts.

Maintain a decision ledger keyed by source + vacancy ID (canonical URL as
fallback). Record disposition, mandatory/desirable requirements, source
snippets, candidate evidence anchors, gaps and reasons for inclusion/exclusion.
Deduplicate across notices and languages. Do not claim all vacancies received
detailed assessment when some only received inventory triage; quantify both.

## Update and verify

When the user authorizes updating the shortlist, complete the review and update
it in the same run. Do not add a second approval gate for ordinary saved-file
edits. Back up the original, preserve existing statuses, notes, owner values,
styles and links, and append only reviewed additions. Apply explicit existing
row corrections by stable ID, never title alone. Surface unresolved eligibility
in the affected row's match label, gaps and preparation guidance.

Use [update_shortlist.mjs](scripts/update_shortlist.mjs) and its linked
[preservation helper](scripts/preserve_workbook.py) for the supported workbook
layout, following the workbook reference. Unsupported structures require an
adapted, verified edit—not silent reconstruction or dropping workbook parts.

Before replacing the original, recheck its hash, verify unique IDs and links,
deadline precision, all changed formulas/ranges, and preservation of user data.
Render changed views and inspect them at readable scale. If the source changed
concurrently, reread and merge the intended edits into the latest version.
Report additions, conditional entries, expired missed roles, significant
coverage limitations and tests performed. Link the updated workbook. Never
submit applications or modify shared recruitment profiles through this skill.
