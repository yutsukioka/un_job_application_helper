# Tier F — Closed-loop self-improvement (deep dive)

> Companion to [00_consolidated_plan.md §"Tier F"](00_consolidated_plan.md).

## F1. Persisted self-critique findings (`lessons.md`)

### What gets persisted

After each completed run, append to a per-vacancy-family
`lessons.md` (memory/repo-scoped, not user-scoped, no PII):

- Findings from `independent_panel_evaluation.md`.
- Red-team rejection-risk flags from `independent-shortlisting-redteam`.
- Unresolved entries from `_discussion/disagreement_log.md`.
- `apex-application-audit` findings.

### Honest framing (important)

This is **not** access to hiring-panel cognition. It is the **agent's
own recurring self-critiques** across runs of the same vacancy family.

If the agent flagged "weak supervisory evidence" four times for UNICEF
P-3 vacancies, that pattern is a real signal of the user's evidence
base, not insight into panel minds.

### Vacancy family key

```
<organization>:<grade-level>:<functional-area>
e.g., "UNICEF:P-3:programme-management"
```

### Surfacing on a new run

On a new run for the same family, `apex-orchestrator-report` reads the
relevant `lessons.md` and surfaces prior self-critiques as a checklist
for Round 2 consensus (C1) — not as automatic content, but as a
"watch-list".

### Privacy

`lessons.md` lives in `memories/repo/` (per-workspace, not user-global)
and contains no candidate-identifiable data — only abstract finding
categories and counts.

## F2. Blind A/B variant comparator (optional)

### Workflow

1. Generate two CV variants with deliberate strategic differences:
   - Variant A: compressed-metrics (numeric-density first).
   - Variant B: expanded-narrative (context-rich, fewer numbers).
2. Strip identifying differences (variant labels, ordering cues).
3. Submit both to `independent-panel-evaluator` for blind ranking.
4. Record the verdict in `lessons.md`.

### When to use

- Only when the user explicitly requests A/B testing.
- Useful for empirically validating which authoring strategies win on
  this vacancy class — not for default runs (cost-prohibitive).

### Limitation

The blind ranking reflects the panel-evaluator agent's preferences, not
necessarily real-panel preferences. Treat as one signal among many.
