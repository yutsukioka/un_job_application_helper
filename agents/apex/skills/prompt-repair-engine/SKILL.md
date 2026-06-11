---
name: prompt-repair-engine
description: >-
  Analyze SKILL.md files for structural defects, ambiguous instructions,
  missing format rules, and conflicting constraints. Produce a repair log
  with specific before/after fixes for each detected issue. Use this skill
  when debugging why a skill produces unexpected output, after editing a
  SKILL.md, or as a batch audit of all skills. This is a diagnostic skill —
  it does not produce application documents.
---

# prompt-repair-engine

## Purpose

This skill reads one or more SKILL.md files from `agents/apex/skills/` and
evaluates them against the structural conventions and constraint patterns
established in the ApexStrategist workflow. It detects:

- missing or malformed YAML frontmatter
- ambiguous instructions that allow multiple interpretations
- missing output format specifications
- conflicting rules (e.g., a rule says "use bullets" while the format
  profile says "single paragraph")
- insufficient constraints that could lead to non-deterministic output
- missing references to `apex-guardrails`
- missing Steps or Rules sections
- input specifications that do not reference actual context-pack
  variable names

For each detected issue, the engine produces a specific repair
recommendation with the original problematic text and a corrected version.

## Shared definitions

Reference the structural patterns and format profiles defined in
`apex-guardrails` as the "gold standard" for well-formed skill files.
Output format profile: `strategy_markdown`.

---

## Inputs

Required (one of):

- `skill_name`: name of a single skill to analyze (e.g.,
  `apex-generate-cv`). The engine reads
  `agents/apex/skills/<skill_name>/SKILL.md`.
- `batch_mode: true`: analyze all skills under `agents/apex/skills/`.

Optional:

- `generated_output`: a sample output produced by the skill, to
  cross-check whether the output matches the skill's stated format.
- `severity_filter`: `all` (default), `error_only`, or
  `error_and_warning`.

---

## Detection Rules

### D1: YAML Frontmatter Check

| Check | Severity | Pass Condition |
|---|---|---|
| Frontmatter exists | ERROR | File starts with `---` block |
| `name:` field present | ERROR | Matches directory name |
| `description:` field present | ERROR | Non-empty, > 20 chars |
| Description is actionable | WARNING | Contains a verb (e.g., "Generate", "Analyze", "Route") |

### D2: Shared Definitions Reference

| Check | Severity | Pass Condition |
|---|---|---|
| Guardrails reference exists | WARNING | Contains text referencing `apex-guardrails` |
| Format profile stated | WARNING | Mentions a profile from A-F or `strategy_markdown` |

### D3: Inputs Section

| Check | Severity | Pass Condition |
|---|---|---|
| Inputs section exists | ERROR | `## Inputs` heading present |
| Required inputs listed | ERROR | At least one required input |
| Input names are concrete | WARNING | Uses actual variable names (e.g., `USER_JOB_HISTORY_TEXT`) not vague terms (e.g., "user data") |
| Required vs optional separated | WARNING | Separate sub-lists for required and optional |

### D4: Output Section

| Check | Severity | Pass Condition |
|---|---|---|
| Output artifact specified | ERROR | File path stated (e.g., `private/output/tmp/...`) |
| Output format defined | ERROR | Template or structure block present |
| Format matches profile | WARNING | Output structure is compatible with stated format profile |

### D5: Steps Section

| Check | Severity | Pass Condition |
|---|---|---|
| Steps section exists | ERROR | `## Steps` heading present |
| Steps are numbered | WARNING | Uses `1. 2. 3.` or `### Step N` |
| Steps reference inputs | WARNING | At least one step reads a stated input |
| Steps reference output | WARNING | Final step(s) write the stated output |

### D6: Rules Section

| Check | Severity | Pass Condition |
|---|---|---|
| Rules section exists | WARNING | `## Rules` heading present |
| Rules are numbered | INFO | Consistent numbering |

### D7: Ambiguity Detection

| Pattern | Severity | Example |
|---|---|---|
| Vague verbs without object | WARNING | "Process the data" (what data? what processing?) |
| Conditional without else | WARNING | "If X, do Y" (what if not X?) |
| "etc.", "and so on", "similar" | WARNING | Leaves scope open-ended |
| Numeric ranges without units | ERROR | "Score: 0-100" without defining what 50 means |
| Conflicting instructions | ERROR | Two rules that contradict each other |

### D8: Cross-Reference Check (if generated_output provided)

| Check | Severity | Pass Condition |
|---|---|---|
| Output sections match spec | ERROR | All sections in format template appear in output |
| No extra sections | WARNING | Output does not contain sections not in the spec |
| Placeholder syntax consistent | WARNING | Uses `[bracketed]` style per guardrails |

---

## Rules

1. **Non-destructive.** This skill only analyzes and recommends — it
   does not modify SKILL.md files directly.
2. **Specific repairs.** Every detected issue must include the exact
   problematic text and a concrete replacement. Do not give generic
   advice like "improve clarity."
3. **Severity accuracy.** ERROR = will likely cause incorrect output.
   WARNING = may cause suboptimal output. INFO = style improvement.
4. **No false positives on intentional deviations.** Some skills
   (e.g., `apex-guardrails` itself) are structural authorities and do
   not need to reference themselves. Mark known exceptions as `SKIP`.

---

## Steps

1. Identify target skill(s): read the specified SKILL.md file or scan
   all `agents/apex/skills/*/SKILL.md` in batch mode.
2. For each SKILL.md:
   a. Run detection rules D1-D7 in order.
   b. If `generated_output` is provided, also run D8.
   c. For each failed check, extract the problematic text (or note its
      absence) and draft a repair recommendation.
3. Classify findings by severity (ERROR > WARNING > INFO).
4. Apply `severity_filter` if specified.
5. Write the repair log.

---

## Output Artifact

File: `private/output/tmp/0x_prompt_repair_log.md`

### Output Format

```
# Prompt Repair Log

## Summary
- Skills analyzed: <n>
- Total issues: <n> (ERROR: <n>, WARNING: <n>, INFO: <n>)
- Skills with no issues: <list or "none">

## Findings by Skill

### Skill: <skill-name>

#### Issue #<n> — [ERROR|WARNING|INFO] <D-rule ID>: <short title>

**Detection Rule:** <D1-D8>

**Location:** <section or line reference>

**Original Text:**
> <exact text or "[MISSING]">

**Problem:**
<one-sentence explanation>

**Repaired Text:**
> <corrected text>

**Reason:**
<why the repair fixes the issue>

---

(Repeat for each issue)

## Repair Priority Queue
| # | Skill | Issue | Severity | Impact |
|---|---|---|---|---|
| 1 | ... | ... | ERROR | ... |
| 2 | ... | ... | WARNING | ... |
```
