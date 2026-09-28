# Deterministic job shortlist

`agents/apex/scripts/shortlist_jobs.py` reads the live SQLite database and writes
an Excel shortlist using Python only. It has no LLM, API-key or network dependency
and does not modify the database or application context.

```sh
python3 -m pip install -r agents/apex/job_shortlist_requirements.txt
python3 agents/apex/scripts/shortlist_jobs.py \
  --feedback private/inputs/user_feedback_updates.md
```

Defaults resolve relative to the repository, regardless of working directory:

- Database: `private/jobagg/output/all_jobs.sqlite3` (the configured live symlink).
- Profile: `private/inputs/application_context.md`.
- Output: timestamped XLSX and a compact JSON run report under
  `private/outputs/job_shortlists/`.

Override with `--database`, `--profile`, `--output` and a timezone-aware `--as-of`.
Use `--as-of` only for reproducibility; it does not reconstruct a historical
database. A new run uses the database available at execution time.

The workbook contains potential matches, a separate eligibility-review list,
open roster calls, and method/coverage notes. Every vacancy sheet is sorted by
closing date. Rows include title, station (`Remote` for remote jobs), published
grade or contract level, application link, matching profile evidence, required
criteria, remaining checks, deadline precision and database timestamps.

## Rules and boundaries

The profile parser reads only `USER_JOB_HISTORY_TEXT` and optionally
`APPROVED_UPDATES`. It does not learn from the target vacancy or turn unresolved
feedback into applicant evidence. Matching uses explicit patterns in `FAMILIES`:
each result needs a title signal and a corresponding applicant evidence signal.
It produces reproducible functional overlap, not an LLM assessment or a hiring
probability. Review the required years, degree and specialist experience yourself.

The script excludes closed, expired, stale, duplicate and no-longer-listed rows,
unknown/invalid deadlines, uncertain same-day date-only cutoffs, nationally
recruited roles outside Japan, clearly unsupported mandatory languages, unpaid
volunteering and specified career-stage/specialist mismatches. A remote national
consultancy outside Japan is still excluded. A hybrid office job is not labelled
remote merely because its description mentions flexible working.

Open means **open in the live database with a future stored deadline**, not a
fresh employer-portal verification. Date-only deadlines remain visibly unverified.
Incomplete descriptions, ambiguous restrictions and language alternatives go to
review. Pattern rules can miss unusual wording and can flag otherwise suitable
roles; this is a screening aid, not an automatic application decision.

Optional `--profile-overrides private/inputs/shortlist_profile.json` accepts:

```json
{
  "languages": {"english": "full professional", "japanese": "native", "french": "basic"},
  "citizenships": [],
  "national_countries": ["JP"],
  "work_authorized_countries": [],
  "disabled_families": []
}
```

Fill citizenship only from your confirmed facts. Japan in `national_countries`
is a search preference, not a citizenship assertion. Work authorization is
retained as profile metadata; the initial rules conservatively flag restrictions
instead of attempting to interpret immigration eligibility. Change `FAMILIES`
or disable a family to adjust matching. No personal profile is embedded in code.

Run tests in an environment with the Excel dependency installed:

```sh
python3 -m unittest discover -s tests -p test_job_shortlist.py -v
```
