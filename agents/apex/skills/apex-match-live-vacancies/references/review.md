# Evidence review and audit record

Use the scanner's family matches and warnings to organize review, not as a
closed list of eligible occupations. Review requirements for close matches,
plausible adjacent functions, all user-named IDs and materially incomplete rows.
Survey the remaining inventory in batches for misleading titles and missed
families. Record coverage honestly and keep any unreviewed plausible records
in a review queue rather than silently rejecting them.

## Decision ledger

Save private, run-specific JSON with one record per substantive decision:

```json
{
  "job_key": "source:external_id",
  "decision": "include | conditional | exclude | retain | pending",
  "reason": "Specific criterion and outcome",
  "required": [
    {"criterion": "Fluent language X", "status": "met | contradicted | unresolved", "source": "notice section or short excerpt", "evidence": "profile role/line, or not established"}
  ],
  "desirable": ["Clearly identified optional criteria"],
  "evidence_anchors": ["profile path:line or approved-update anchor"],
  "source_url": "Official notice URL",
  "observed_at": "Timezone-aware timestamp",
  "deadline_precision": "instant | date | unknown | conflicting",
  "remaining_checks": ["Specific unproven condition"]
}
```

Keep inventory triage separate from detailed decisions. Summarize eligible
source rows, duplicate records, expired rows, candidates reviewed in detail,
added entries and unresolved review rows without adding overlapping categories
as if they were mutually exclusive.

## Required versus desirable

Read words such as required, essential, minimum, desirable, asset, preferred
and accepted in lieu in their sentence/section context. Preserve alternatives
such as master's plus five years OR bachelor's plus seven. A credential listed
as desirable cannot independently disqualify a candidate. Neither years in a
role nor a senior title proves the same number of years doing a specialist task.
Qualifying experience must be anchored to actual work; union overlapping periods
when calculating a total and leave unsupported totals unresolved.

Do not mistake languages in country descriptions, organizational boilerplate or
an unrelated example for mandatory applicant requirements. Basic French is not
fluent French. Native Japanese is not proof of Japanese citizenship. Having
worked for UNICEF is not proof of eligibility for an UNOPS internal-only post.

An engineering-only qualification or substantial audit-career requirement must
be checked separately from general project or assurance experience. Conversely,
finance, procurement, accountability and safeguards functions must not be
discarded solely because the applicant's recent title emphasizes programmes.

## Source and time controls

Prefer current official notice facts to derived database classifications. Record
conflicts between structured deadlines and notice text; verify before calling
an uncertain vacancy closed or guaranteeing that it is open. Date-only deadlines
on the current date need a visible cutoff check. Past exact deadlines must not
be added to an open list merely to correct a historical omission.

If an employer page cannot be read, use the stored official text with its actual
observation date and describe the limitation. Do not call every database row
live-verified. Additional recommendation-feed jobs outside the database are
identified as supplementary sources; this does not authorize database writes.

An independent QA pass should challenge mandatory-versus-desirable parsing,
local eligibility assumptions, omissions in adjacent functions, duplicated IDs,
and source/evidence mismatches. For a new helper, test observable regressions
such as mixed-format dates and preserving an old record with no posting date.
