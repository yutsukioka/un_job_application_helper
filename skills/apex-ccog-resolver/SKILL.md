---
name: apex-ccog-resolver
description: >-
  Dynamically resolve relevant CCOG (Common Classification of Occupational
  Groups) entries from the full ICSC database for a specific vacancy analysis.
  Reads the entire CCOG database once, scores entries against JD signals
  (vacancy title, responsibilities, competencies, department/agency, and
  user-confirmed vacancy-type classification), selects the top 10-20 relevant
  entries representing the vacancy's target register, writes a compact resolved
  subset, and clears the full database from context.
  Use this skill early in the pipeline (Phase 1) before any CCOG-dependent
  analysis steps. Do not use for document generation.
---

# apex-ccog-resolver — Dynamic CCOG Entry Resolution

## Purpose

Resolve a compact, vacancy-specific subset of CCOG occupational definitions
from the full ICSC CCOG database (~338 entries). The resolved subset represents
the **vacancy's target register** — the occupational language the JD expects —
and is used by downstream skills for register alignment, verb bank construction,
and register mismatch detection.

**Critical design principle:** This skill resolves CCOG entries based on **JD
signals only**, not on the candidate's job history. The resolved set must
represent what the vacancy demands, independent of what the candidate brings.
If candidate history were mixed in, it would contaminate the target register
with the candidate's native register (e.g., pulling in 1.A.02.e Programme
specialists when the JD requires 1.L.03.a Political Affairs specialists),
making downstream register-mismatch detection ineffective.

Candidate-to-CCOG mapping is performed separately by `apex-candidate-evidence-bank`
(Step 5) at the point of mismatch detection, using the JD-derived resolved
reference as the comparison baseline.

This skill replaces any static, pre-determined CCOG entry list. Every vacancy
analysis gets a fresh resolution tailored to the specific JD.

## When to invoke

- **Required before:** `apex-keyword-insertion-map` (Step 3), `apex-candidate-evidence-bank` register mismatch detection (Step 5), `apex-guardrails` CCOG verb semantics (Step 8A).
- **Invoked after:** `apex-jd-core-requirements` vacancy-type classification (Step 2) — the vacancy-type and user-confirmed classification are inputs to this skill.
- **Trigger:** Automatically as part of the orchestrator pipeline, or manually when the user requests CCOG analysis for a new vacancy.

## Inputs

| Input | Source | Required |
|-------|--------|----------|
| `JOB_DESCRIPTION_TEXT` | `application_context.md` | YES |
| `JOB_REQUIREMENT_TEXT` | `application_context.md` | YES |
| `VACANCY_TYPE_CLASSIFICATION` | Output of `apex-jd-core-requirements` (user-confirmed) | YES |

## Resource

The full CCOG database is stored at:
```
.agents/skills/apex-ccog-resolver/resource/ccog_reference_full.md
```

This file contains all ~338 CCOG entries in the following structured format:

```markdown
### <code> — <title>
**Family:** <family code> — <family name>
**Canonical verbs:** <comma-separated list of verbs from the CCOG definition>
**Scope descriptors:** <comma-separated list of scope nouns/phrases>
**Level signal:** Professional/Managerial | General Service | Field Service
**Common Job Code Titles:** <comma-separated list of typical UN job titles>
```

### Preparing the full database (one-time setup)

1. Extract text from the ICSC CCOG 2015 PDF (77 pages) using pdfplumber or equivalent.
2. Parse each occupational group definition into the structured format above.
3. Clean PDF line-break artifacts, normalise whitespace, and fix encoding issues.
4. Validate completeness: count entries against the known ~338 definitions.
5. Save to `resource/ccog_reference_full.md`.

## Resolution procedure

### Stage 1 — Input collection

1. Read the full CCOG database from `resource/ccog_reference_full.md`.
2. Collect the JD text and vacancy-type classification.
3. Extract from the JD:
   - All action verbs (from responsibilities, duties, and competencies sections)
   - All scope nouns (programmes, policies, missions, systems, etc.)
   - Department/agency name
   - Duty station and context signals
   - Vacancy title

### Stage 2 — JD-driven CCOG mapping

For each CCOG entry in the full database:

1. **Verb overlap score:** Count how many of the entry's canonical verbs appear
   in the JD text (case-insensitive, lemmatised matching).
2. **Scope overlap score:** Count how many of the entry's scope descriptors
   appear in the JD text.
3. **Title match bonus:** If the entry's common job code titles contain words
   matching the vacancy title, add a bonus.
4. **Vacancy-type bonus:** If the entry's family is commonly associated with the
   confirmed vacancy type (using the register grouping table below), add a bonus.
5. **Compute JD relevance score:** `(verb_overlap × 2) + scope_overlap + title_bonus + vacancy_type_bonus`
6. **Rank** all entries by JD relevance score descending.
7. **Select** entries with score above the median of non-zero scores as
   **JD-relevant candidates**.

### Stage 3 — Validate and cap

1. **Force-include** the primary and secondary CCOG families from the
   vacancy-type classification (even if they scored below threshold).
2. **Deduplicate** by CCOG code.
3. **Cap at 20 entries.** If more than 20, drop the lowest-scoring entries.
4. **Minimum of 8 entries.** If fewer than 8, lower the threshold and re-select.

> **Why no candidate-history mapping here?** The resolved set must represent
> the vacancy's target register — what the JD demands. If the candidate's
> native register (e.g., 1.A.02.e Programme specialists) were merged in,
> downstream register-mismatch detection would fail to distinguish the
> candidate's existing language from the JD's target language.
>
> Example: A political affairs vacancy (1.L.03.a) requires verbs like
> "analyse political institutions," "forecast," "recommend improvements to
> policy." A candidate with programme delivery experience uses verbs like
> "implement," "oversee," "develop." If both families are in the resolved
> set as equals, the mismatch signal is diluted. Keeping the resolved set
> JD-only ensures a clean baseline for comparison.
>
> Candidate-to-CCOG mapping happens later in `apex-candidate-evidence-bank`
> (Step 5), where each role in the candidate's history is independently
> matched to CCOG families and compared against the JD-derived resolved set.

### Stage 4 — Output and context management

1. Write the resolved entries to the output working directory as
   `ccog_reference_resolved.md`, grouped by register type:

```markdown
# CCOG Resolved Reference — <Vacancy Title>
## Resolved on: <date>
## Vacancy type: <confirmed type>
## Total entries: <count>

### Political/Diplomatic Register
<entries>

### Programmatic Register
<entries>

### Humanitarian Register
<entries>

### Donor/Resource Mobilisation Register
<entries>

### Technical/Specialized Register
<entries>

### Information Management Register
<entries>

### Administrative/Operational Register
<entries>
```

2. **Clear the full CCOG database from the context window.** The full database
   should not be retained after this point — only the resolved subset persists.
3. Report to the user: "Resolved <N> CCOG entries for this vacancy. Primary
   register: <family>. See `ccog_reference_resolved.md`."

## Register grouping reference

| Register Group | Typical CCOG Families | Associated Vacancy Types |
|---|---|---|
| Political/Diplomatic | 1.L.03.a, 1.A.10.b, 1.G.02 | SPM, PEACEKEEPING, SECRETARIAT (political organs) |
| Programmatic | 1.A.02.e, 1.A.02.f, 1.A.11 | DEVELOPMENT_AGENCY, most SECRETARIAT roles |
| Humanitarian | 1.S.01, 1.L.04 | HUMANITARIAN_AGENCY |
| Donor/Resource Mobilisation | 1.A.10.c | Fundraising, partnership roles |
| Technical/Specialized | Domain-specific families | SPECIALIZED_AGENCY |
| Information Management | 1.A.05, 1.A.05.a, 1.C.04 | IT, data, knowledge management roles |
| Administrative/Operational | 1.A.12, 1.A.06, 1.A.09 | Administrative, HR, logistics roles |

## Constraints

- **Do not hallucinate CCOG entries.** Only use entries present in the full
  database. If no CCOG family matches a JD function, output: "No CCOG family
  match for <function>; using JD-derived register."
- **Do not retain the full database after resolution.** Context window
  management is a core purpose of this skill.
- **Resolved entries must include all structured fields** (family, canonical
  verbs, scope descriptors, level signal, common job titles). Do not truncate
  entries to save space — the resolved set is already compact.
- **Source grounding (Guardrail #1):** All canonical verbs and scope descriptors
  must come from the actual CCOG definitions, not from the JD or candidate
  materials.

## Output

The skill produces exactly one file:
- `ccog_reference_resolved.md` — written to the output working directory

This file represents the **vacancy's target register only**. It does not
contain candidate-side CCOG mappings.

And a brief summary message to the user confirming:
- Number of entries resolved
- Primary and secondary register families
- Any JD functions with no CCOG match

## Dependencies

- Requires the full CCOG database at `resource/ccog_reference_full.md` (one-time
  setup).
- Requires vacancy-type classification from `apex-jd-core-requirements` (Step 2).
- Must run before Steps 3, 5, and 8A.
