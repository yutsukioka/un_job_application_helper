# Change Log

Date: 2026-05-06

## Summary

Implemented the requested v2 ensemble hardening while preserving the previous
multi-model topology support for P0a/P0b/S/C/D/E/R servers.

Previously added live-server support for E1/E2/R1/R2 and the detached server
launcher were retained.

## Implemented Changes

### 1. Substantive Advisor Review Gate

- Added `.agents/scripts/validate_advisor_notes.py`.
- Updated advisor and QA prompt contracts so shutdown now requires more than
  a non-empty advisor-notes file.
- New rule: every advisor must contribute at least one specific observation,
  issue, or suggestion tied to a file, requirement, metric, keyword, CCOG
  register point, format profile, or character limit.
- Updated `apex-agent-sync-protocol`, `qa-auditor.agent.md`, `server_manifest.yaml`,
  and the v2 runbook.

### 2. CCOG Resolver Scoring Refinement

- Updated `.agents/skills/apex-ccog-resolver/scripts/resolve_ccog.py`.
- Added IDF-style weighting for CCOG verb/scope matches.
- Suppressed generic terms such as `apply`, `plan`, `provide`, `services`,
  `systems`, and `development` when they do not carry domain meaning.
- Added semantic title expansion for data/reporting/information families.
- Added title mismatch and domain-gate penalties.
- Changed sorting so title/domain-aligned entries outrank generic high scorers.
- Updated the `apex-ccog-resolver` skill contract to match the new scoring.

### 3. Independent Evaluation Isolation

- Added `.agents/scripts/prepare_independent_eval_input.py`.
- E1/E2 now read a sanitized benchmark file only:
  `<OUTDIR>/_discussion/independent_eval_input.md`.
- The sanitized file contains only `## JOB_DESCRIPTION_TEXT`, plus
  `## JOB_QUALIFICATION_QUESTIONS` when TARGET_SYSTEM is INSPIRA and questions
  exist.
- E1/E2 are explicitly forbidden from reading full `inputs/application_context.md`,
  candidate history, metric ledgers, strategy reports, advisor notes,
  panel-response files, or remediation files.

### 4. Draft Diversity Gate

- Added `.agents/scripts/check_draft_diversity.py`.
- Updated C2 to run a 95 percent character-level similarity check across D1/D2/D3
  `option1_admin_profile.md` drafts before merge.
- If any pair exceeds the threshold, C2 must stop with `DIVERSITY_FAILURE` and
  surface the issue before merging.

### 5. Independent D-Writer Scaffold

- Added `.agents/scripts/prepare_d_writer_scaffold.py`.
- D1/D2/D3 writer prompts now run a preflight before IMPLEMENT pass 1.
- The preflight creates empty draft targets and fails if a writer folder already
  contains non-empty option files, preventing copied or pre-populated sibling
  drafts from passing as independent drafts.

### 6. Run Manifest

- Added `.agents/scripts/write_run_manifest.py`.
- P0a initializes `<OUTDIR>/_discussion/run_manifest.json`.
- P0a/P0b/C1/C2/R2 prompt contracts now record key skill invocations.
- R2 finalizes the manifest.
- Manifest schema includes `job_slug`, `target_system`, `servers_launched`,
  `skills_invoked`, `skills_not_invoked`, `unexpected_invocations`, and
  `quality_gates`.

### 7. E2 Coverage Controls

- C2 remains responsible for E2a phrase coverage floor.
- R2 is now responsible for:
  - E2b requirement-by-requirement coverage matrix.
  - E2c unsupported-claim scan.
- Updated `spec/05_tier_e_integrity_guardians.md` and R2 prompt language.

## Plan-Only Items

### Mandatory Agent Execution Trace

No implementation change was made for this item. See `README_revised.md` for
the detailed implementation plan.

### Term Extractor Canonical Context Path

No implementation change was made for this item. See `README_revised.md` for
the detailed implementation plan.

## Validation Performed

- Parsed all `.agents/scripts/*.py` with Python AST parsing.
- Parsed the revised CCOG resolver script.
- Ran `validate_advisor_notes.py` against existing D1 advisor notes.
- Generated a sanitized independent-evaluation input bundle in `/private/tmp`.
- Ran `write_run_manifest.py` init/add/finalize cycle in `/private/tmp`.
- Ran `prepare_d_writer_scaffold.py` in `/private/tmp`.
- Ran `check_draft_diversity.py` on identical test drafts and confirmed it
  reports `DIVERSITY_FAILURE`.
- Ran the CCOG resolver against the current application context with
  `HUMANITARIAN_AGENCY`; top debug entries are now information/domain aligned.
