---
name: apex-cross-doc-consistency
description: >-
  Validate consistency across multiple generated application documents
  (Admin Profile, CV, Cover Letter, Qualification Answers, etc.). Flag
  mismatches in job titles, dates, achievements, keyword usage, and
  narrative claims. Use this skill after generating two or more Phase 8
  documents to ensure they tell a cohesive story.
---

# apex-cross-doc-consistency

## Purpose

This skill compares generated application documents against each other
and against the source inputs to detect inconsistencies that could
undermine the candidate's credibility during screening. It produces a
structured consistency report with actionable flags.

Apply the expert lens, collaboration rules, guardrails, quality loop
protocol, and guiding principles defined in `apex-guardrails`.

## Inputs

Required:

- At least two generated documents from Phase 8 (e.g., Admin Profile
  and CV, or CV and Cover Letter).

Optional:

- `USER_JOB_HISTORY_TEXT`: to verify that documents match the source
  data.
- `TERM_EXTRACTOR`: to check keyword usage consistency.
- `inputs/application_context.md`: for full cross-reference.

## Checks

1. **Job titles:** Verify that the same role is titled identically
   across all documents. Flag any discrepancies (e.g., "Programme
   Manager" in the CV vs. "Project Manager" in the Cover Letter).
2. **Dates and timelines:** Verify that employment dates, project
   durations, and chronological order are consistent across documents.
3. **Achievements and metrics:** Verify that quantified claims (e.g.,
   "USD 5M budget", "12 countries") match across documents and against
   source inputs. Flag any conflicting numbers.
4. **Keyword usage:** Verify that high-priority keywords (★★★ and
   above) appear consistently across documents. Flag any document that
   omits a critical keyword present in others.
5. **Narrative coherence:** Verify that the UVP, STAR stories, and key
   selling points referenced in the strategy report are reflected in
   the generated documents. Flag missing narrative threads.
6. **Employer and organization names:** Verify spelling and naming
   consistency across documents.

## Output format

Return a structured report:

```
## Cross-Document Consistency Report

### PASS items
- <list of checks that passed>

### FLAGS
- [FLAG-001] <Category>: <description of mismatch, with document names and locations>
- [FLAG-002] ...

### Summary
PASS / FAIL (with count of flags)
```

## Rules

- Do not modify the documents; only report findings.
- Reference specific locations in each document when flagging issues.
- Distinguish between critical flags (factual mismatches) and minor
  flags (stylistic differences).
- If no inconsistencies are found, return a PASS report confirming
  the documents are consistent.

## Steps

1. Parse all provided documents and extract job titles, dates,
   metrics, keywords, and key claims.
2. Cross-reference each extracted element across all documents.
3. Cross-reference against source inputs if provided.
4. Compile flags for any mismatches.
5. Output the structured consistency report.
