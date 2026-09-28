# Application Context Pack

This file provides the context for the **ApexStrategist** agent to analyze your profile and the target job.
Paste your content under each header. All sections are required unless marked optional.

## USER_JOB_HISTORY_TEXT
[Paste your consolidated job history or CV text here. Include dates, locations, and key achievements for each role.]

## USER_ADMIN_PROFILE_TEXT
[Paste your existing e-recruitment profile text here (Personal History Form content). Include duties and achievements for each entry.]

## JOB_DESCRIPTION_TEXT
[Paste the full text of the Job Description (JD) for the target role.]

## JOB_REQUIREMENT_TEXT
[Paste the specific "Requirements" or "Competencies" section from the JD. Include Education, Work Experience, Languages, and Competencies.]

## JOB_QUALIFICATION_QUESTIONS
[Paste the screening/qualification questions from the application portal, if available. If none, leave blank.]

## TERM_EXTRACTOR
[Paste key terms and phrases relevant to the role. You can optionally assign weights, e.g., "Result-Based Management ***" or "Stakeholder Engagement **".]

## SKILLS_TAXONOMY
[Paste a list of your core technical and functional skills, organized by category (e.g., "Data Analysis", "Project Management").]

## USER_CERTIFICATIONS_TEXT
[Optional for Option 9: earned credentials, issuing bodies, award dates and known validity/expiry status. Leave blank if none are supplied.]

## USER_LANGUAGES_TEXT
[Optional for Option 9: language levels, actual speaking/listening/reading/writing use, and any assessments with dates.]

## PORTAL_SKILL_ENTRIES
[Optional for Option 9: copy the actual selectable Skill / Certification / Language names and types. State the portal source; a personal skills list is not a verified portal list.]

## PORTAL_PROFICIENCY_GUIDANCE
[Optional for Option 9: exact proficiency choices and any item-specific help, including how certification proficiency is defined. The World Bank workflow defaults to High / Medium / Low as advisory ratings when definitions are absent.]

## PORTAL_ENTRY_MODE
[Optional for Option 9: CONTROLLED_LIST | FREE_TEXT | UNKNOWN. Without a verified list or free-text mode, generated item names are drafts.]

## LIMITS

For the named UNESCO skills, the following optional source sections may be
added above LIMITS: `EMPLOYMENT_HISTORY_FORM_REFERENCE`, `PORTAL_FIELD_SCHEMA`,
`PORTAL_DOMAIN_OPTIONS`, `PORTAL_DOMAIN_SELECTION_RULES`,
`EXPERIENCE_AS_OF_DATE`, `USER_PUBLICATIONS_TEXT`, `PORTAL_PUBLICATION_GUIDANCE`.
Those skills also accept raw inputs directly. Their field-specific rules take
precedence over the generic example limits below; dropdowns have no text budget.
For UNESCO use `TARGET_SYSTEM: OTHER` and `TARGET_ORGANIZATION: UNESCO`.
For UNOPS use `TARGET_SYSTEM: OTHER` and `TARGET_ORGANIZATION: UNOPS`;
optional `UNOPS_ADAPTER: apex-unops-application-fit`. Supply `PORTAL_FIELD_SCHEMA`
and dated Position Area options/help when available. The catalog defaults to
`private/inputs/taxonomies/unops/UNOPS_Skills_2026-09-27.csv` with its adjacent
source manifest. A requested selection budget of 20 is a drafting default,
not a verified portal cap. For UNOPS, replace the generic numeric examples below
with field-specific verified limits or UNKNOWN; do not inherit these examples.

[Define the character constraints for the application fields.]

CHAR_LIMIT: 2000
TARGET_LOW: 1800
TARGET_HIGH: 1950
WORD_TARGET: 300

## RUN_MODE
# Runtime profile. `single` is the low-cost default.
# `ensemble_v2` is opt-in and uses the lists below plus agents/apex/topology/server_manifest.yaml.
AGENT_MODE: single

# v2 multi-agent ensemble configuration. Empty lists = single-agent linear mode (default).
# Authority: docs/architecture/01_tier_a_ensemble_workflow.md §A2.
#
# One name = that name is writer; remaining authoring agents participate as advisors on each server.
# Two or three names = ensemble fold launched per phase.
# qa-auditor is always co-resident on author servers as canonical tester regardless of RUN_MODE.
ENSEMBLE_PHASE_1_7: []                                                    # e.g., [screening-lead, technical-lead, ats-format-lead]
ENSEMBLE_PHASE_8:   []                                                    # same shape
MAX_REVISION_PASSES: 1                                                    # critic-author cap inside each author server
JD_COVERAGE_FLOOR:  0.70                                                  # E2; 0.0 to disable

## BUDGETS
# v2 operational safety budgets. Read at Phase 0 by apex-orchestrator-report.
# Authority: docs/architecture/07_tier_g_safety_budgets.md.
# Counter file convention: private/tmp/_budget_<server>.json
MAX_ROUND_TOOL_CALLS: 40            # per IMPLEMENT round, per writer
MAX_ROUND_TOKENS:     120000        # approximate, per round, per writer
MAX_ADVISOR_MESSAGES: 8             # per advisor per round (prompt-level convention)
MAX_REVISION_PASSES:  2             # critic-author loop cap; also referenced in RUN_MODE
ON_BUDGET_EXCEEDED:   DEGRADE_AND_FLAG    # alternatives: HARD_STOP | ASK_USER
