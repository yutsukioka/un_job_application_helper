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

## LIMITS
[Define the character constraints for the application fields.]

CHAR_LIMIT: 2000
TARGET_LOW: 1800
TARGET_HIGH: 1950
WORD_TARGET: 300

## RUN_MODE
# v2 multi-agent ensemble configuration. Empty lists = single-agent linear mode (default).
# Authority: .agents/spec/01_tier_a_ensemble_workflow.md §A2.
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
# Authority: .agents/spec/07_tier_g_safety_budgets.md.
# Counter file convention: tmp/_budget_<server>.json
MAX_ROUND_TOOL_CALLS: 40            # per IMPLEMENT round, per writer
MAX_ROUND_TOKENS:     120000        # approximate, per round, per writer
MAX_ADVISOR_MESSAGES: 8             # per advisor per round (prompt-level convention)
MAX_REVISION_PASSES:  2             # critic-author loop cap; also referenced in RUN_MODE
ON_BUDGET_EXCEEDED:   DEGRADE_AND_FLAG    # alternatives: HARD_STOP | ASK_USER
