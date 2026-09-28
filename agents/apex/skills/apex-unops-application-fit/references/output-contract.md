# Output contract

Follow the checkout's private-data convention; do not place real applicant
history or completed applications in the public skill tree. Suggested paths are
private/output/<vacancy-key>/<run-id>/ and private/inputs/taxonomies/unops/;
resolve actual paths before writing and preserve prior runs.

## Mandatory planning artifacts

1. source_manifest.json: input paths/hashes, catalog hash/date, vacancy ID and
   source/date, active repository commit, field metadata and unknown constraints.
2. candidate_assertion_ledger.json: atomic claims anchored to role and source,
   using repository evidence-status classes and explicit dated skill intervals.
3. unops_fit_plan.json: all historical role IDs, immutable role facts, baseline
   facets, actual enum labels, proposed/selected areas with reasons, skills,
   requirement-to-evidence mappings, selected document options and blockers.
4. unops_selection.json: the small machine-checkable selection contract below.
5. unops_application_review.md: role decisions, selected skills with reasons,
   rejected alternatives, mandatory gaps, uncertainties and cross-profile risks.
6. role_fields.md and skills_for_portal.txt: paste-ready values only when their
   actual field semantics are known. Keep unverified proposals in review output.
7. validation_report.md and generation_diff.md: checks run, outcomes, unresolved
   items and changes from the earlier application snapshot.

Existing selected generators retain their output filenames and formatting
contracts. Do not add audit footnotes, evidence IDs or reasoning to upload-ready
CVs/letters or paste-ready fields; retain those in sidecar review files. Unknown
critical facts prevent submission-ready status even when useful drafts exist.

## Small selection JSON contract

```json
{
  "schema_version": 1,
  "mode": "vacancy",
  "vacancy_id": "actual-id",
  "catalog_sha256": "64-hex-character-sha256",
  "requested_selection_budget": 20,
  "platform_skill_limit": null,
  "platform_skill_limit_status": "UNVERIFIED",
  "platform_skill_limit_scope": "UNKNOWN",
  "selection_scope": "APPLICATION",
  "skills": [
    {
      "name": "Exact dictionary label",
      "catalog_no": "Observed No column value",
      "evidence": [
        {
          "role_id": "stable-role-id",
          "status": "SUPPORTED",
          "source": "private/inputs/application_context.md",
          "locator": "stable heading and line range or equivalent locator",
          "claim": "Specific capability supported by the cited record"
        }
      ],
      "requirement_ids": ["R1"],
      "selection_reason": "Distinct value for this vacancy"
    }
  ]
}
```

For reference portfolios use mode=reference_portfolio and vacancy_id=null;
requirement IDs may be empty. This must never be labelled a vacancy-final plan.
For vacancy mode every skill must reference at least one requirement. This
validator does not establish that the requirement IDs resolve or are satisfied;
the full fit-plan audit must do that.

The script rejects an unverified limit encoded as an official numeric limit.
Store the user's recollection separately from platform_skill_limit. When a
verified limit applies to a different scope, validate the appropriate additional
selection collection rather than incorrectly constraining this one. A limit of
zero is valid; an unknown limit is not zero and is not unlimited.

For a VERIFIED limit include platform_skill_limit_evidence with nonempty source,
locator and observed_at strings. The validator checks their presence, not the
source's authenticity. For selection_scope=PER_ROLE add selection_role_id; every
evidence record in that collection must have that role_id. Validate each role's
collection separately, and also the distinct global collection when a global
limit exists.

## Date calculation contract

Preserve uncertain date precision. Do not turn a year-only period into exact
January 1 / December 31 dates, or a whole role into a skill-use window, without
source support. For exact date windows use inclusive dates, union overlapping
intervals and store the convention. Do not round up to meet requirements.
A known calendar window is not automatically full-time equivalent experience.

## Integration acceptance cases

- A health/UHC/CBHI role can support an observed Health label without a clinical
  credential; no medical licence or clinical duty is invented.
- A target health vacancy cannot turn unrelated administration into Health.
- A primary-function constraint overrides preference for a secondary facet.
- An unknown enum leaves a proposal but not a fabricated selected portal value.
- A label containing commas is one skill in arrays/quoted CSV.
- Nonconsecutive catalog No values do not corrupt record counts or identity.
- Unverified twenty is a requested budget, not a platform fact.
- A skill used for one month in a three-year role earns no automatic three years.
- A missing required qualification remains a gap despite twenty matching tags.
- Every historical position appears once; no target-role duties migrate backwards.
- Non-UNOPS generation, existing menu numbers and format profiles remain unchanged.
- Existing selected outputs share the same facts and fit plan; no silent overwrite.
- Unknown constraints produce draft/blocked status, not invented validation success.
