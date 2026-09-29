# Updating the existing shortlist

Use the installed Spreadsheet skill and its bundled runtime. These helpers extend
the existing Project Plan, vacancy and roster layouts; they do not redesign a
workbook or create a new tab. They never overwrite the original workbook.

## Input

`curated.json` is an array (or `{ "records": [...] }`) of reviewed records. The
legacy 26 September input format is supported. New rows require:

`org`, `id`, `title`, `duty`, `grade`, `contract`, `fit`, `why`, `gap`,
`requirements`, `prep`, `date`, `deadline`, `evidence`, `apply_url`, `notice_url`.

Keep `job_key` and review evidence in the curated payload for the audit trail.
The existing workbook's stored identity is organization plus vacancy ID, checked
against apply/notice hyperlinks. Ambiguous identity joins fail closed. Titles are
not used to deduplicate vacancy records.

Optional fields:

- `kind`: `vacancy` (default) or `roster`.
- `action`: `append` (default), `review`, or `refresh`. Append skips records already
  present. Review changes only explicitly supplied fit/reason/gap/requirements/
  preparation/deadline-text/evidence and the resulting priority. Refresh additionally
  permits explicitly supplied official title, duty, grade, contract, deadline and
  URLs; title/link changes synchronize to the matching Project Plan row.
- `priority`: P0/P1/P2. Strong match defaults to P0, Good match to P1, other labels
  to P2. Conditional, eligibility and review labels force P2 and require a gap.
- `owner`: defaults to the workbook's candidate. New application status is blank.
- `date_precision`: `date` or `time`. ISO date-only input defaults to `date`;
  ISO datetime defaults to `time`. Use `date: null` for an unknown deadline.

`date` stores the deadline, while `deadline` explains the source and timezone.
Offset-aware ISO datetimes are converted to Africa/Nairobi; offset-free legacy
datetimes are already Nairobi wall time. Known midnight is a precise time when
supplied as a datetime, not silently treated as unknown. Dates remain typed Excel
values, and new plan deadlines retain full precision. Countdown formulas use the
computer clock, following the workbook's Nairobi-clock convention.
When a review payload includes `deadline_detail.utc_instant` with a resolved
future-instant state, do not reduce it to a source-calendar date. The helper
rejects that loss of precision. Vacancy IDs are stored and formatted as text,
including numeric-looking IDs longer than Excel's 15-digit numeric precision.

## Commands

Discover bundled Node, Python and node_modules with `load_workspace_dependencies`.
Create a `node_modules` symlink to the returned modules directory **inside the
conversation's run directory**, never inside this skill or the dependency folder.
Pass the same source/input/run/date options in all three commands:

```text
<bundled-node> scripts/update_shortlist.mjs --inspect --source <existing.xlsx> --curated <curated.json> --run-dir <run-dir> --as-of YYYY-MM-DD --python <bundled-python>
```

Inspect `before_plan.png`, `before_vacancy.png`, and `before_roster.png`. Run the
Spreadsheet skill's artifact-operation marker exactly once immediately before
the first authoring command, then:

```text
<bundled-node> scripts/update_shortlist.mjs --edit --source <existing.xlsx> --curated <curated.json> --run-dir <run-dir> --as-of YYYY-MM-DD --python <bundled-python>
<bundled-node> scripts/update_shortlist.mjs --verify --source <existing.xlsx> --curated <curated.json> --run-dir <run-dir> --as-of YYYY-MM-DD --python <bundled-python>
```

`--edit` authors changed/new cells with Artifact Tool, recalculates, then invokes
the targeted OOXML preservation fallback. The fallback transfers only those
authored cells and native range/link extensions into `before.xlsx`'s package.
It preserves all untouched package parts, existing cell formulas/content/styles,
user statuses, notes, panes, column widths, merges and other native controls.
It extends existing tables, summary count ranges, conditional formats and relevant
validation tails. New Project Plan countdowns link directly to the detail row
resolved by identity, so repeated titles do not create an ambiguous lookup.
Existing unrelated formulas are left intact.

Read `preservation_checks.json` and the formula scan, and view all changed-row
PNGs from `--verify`. Do not claim native Excel recalculation was tested merely
because Artifact Tool recalculated, cached formula values exist, or exports pass.
The helper requests recalculation when Excel opens the file. Inspect any pre-existing
formula defects separately; do not silently repair them.

## Installation after verification

The output is `<run-dir>/verified.xlsx`; the original remains unchanged. A caller
may install it into the authorized original path only after visual QA and checking
the source SHA-256 still equals `manifest.json.source_sha256`. Keep `before.xlsx`
as the backup. Prefer a same-directory temporary copy plus atomic replacement.
If the source or curated payload changes, use a new run directory and repeat the
inspection. Never bypass the concurrency guard.
