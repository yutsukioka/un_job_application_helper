# Experience interval calculation

First decide which practice periods actually support the proposed subarea from
applicant evidence. The helper cannot make that decision. Run one ledger per
subarea; sharing a period across genuinely distinct domains is allowed, while
adding overlapping periods twice within one domain is not.

Invoke `python3 scripts/calculate_experience.py --ledger <path>` from this skill
folder. It reads JSON and prints a report; it does not write applicant files.

```json
{
  "as_of_date": "2026-09-13",
  "intervals": [
    {"start": "2020-01-01", "end": "2022-12-31", "source": "CV, role A: dated accounting work"},
    {"start": "2022-06", "end": "2024-05", "source": "Project B dates and personal responsibilities"},
    {"start": "2025-02-01", "end": null, "source": "Current role and relevant duties", "current_confirmed": true, "current_status_source": "Applicant confirms ongoing work in the current assessment"}
  ]
}
```

These are fictional structural examples. Do not turn them into applicant facts.

Use ISO day (`YYYY-MM-DD`), month (`YYYY-MM`) or year (`YYYY`) precision as actually
supplied. Exact endpoints count inclusively, and known end dates after the
assessment cutoff are capped at that date. Month/year dates produce lower and
upper coverage bounds: the helper never treats the first day as an established
employment date. The displayed bound dates are calculation bounds, not revised
source facts. When ordering is uncertain, the guaranteed contribution can be
zero; the possible bound is still shown.

An open-ended interval needs `current_confirmed:true` and a source locator for
the supporting current-status evidence. Set that flag only when the source
actually supports it; the helper cannot verify a self-declared boolean. A stale
“present” without fresh supporting evidence remains an issue. Missing or invalid
intervals are held separately; known resolved intervals still receive totals.

The helper unions intervals before counting, including adjacent dates, and
returns minimum/maximum known covered calendar days and merged periods. It does
not divide days by a chosen year length, derive FTEs, infer qualifying employment,
or assign a UNESCO band. An unresolved interval means the known totals are not a
complete total. Do not use them to imply an upper-bound experience claim.

Assess the actual calendar periods and source precision when selecting a band.
Clearly supported totals comfortably within one observed band can proceed
when no identified unresolved relevant interval could change that choice.
Do not label a bounded band ready merely because a verified subset fits it.
Hold only the affected row's band; keep the verified pair and known-period
evidence in review while independent rows proceed.
Hold a range that crosses bands or needs an unsupported rounding convention.
The exact-ten-year overlap, under-one-year absence and fractional gaps between
displayed ranges remain interpretation questions. Never replace uncertainty
with the minimum band or No Selection.
