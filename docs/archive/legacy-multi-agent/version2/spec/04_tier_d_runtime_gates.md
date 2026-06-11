# Tier D — Promote diagnostics to runtime gates (deep dive)

> Companion to [00_consolidated_plan.md §"Tier D"](00_consolidated_plan.md).

## D1. `evidence-ranking-engine` as runtime gate

Promote from diagnostic skill to mandatory runtime gate.

**Insertion point:** between `apex-candidate-evidence-bank` (Phase 1.3)
and any Phase 8 document generator.

**Contract:** generators read `evidence_ranking.json` (structured
output of the engine), not the raw evidence bank. If
`evidence_ranking.json` is missing or stale (older than the bank),
generators must fail with a clear message.

## D2. Structured handoff schema

Each Phase 1–7 artifact ships in two forms:

| Markdown (human-readable) | Structured (machine-readable) |
|---|---|
| `phase1_2_core_requirements.md` | `phase1_2_core_requirements.json` |
| `metric_ledger.md` | `metric_ledger.json` |
| `evidence_bank.md` | `evidence_bank.json` + `evidence_ranking.json` |
| `phase1_7_strategy_report.md` | `phase1_7_strategy_report.json` (key claims + provenance) |

Downstream skills consume the JSON. Humans read the markdown.

**Why critical for ensemble mode:** structured JSON is parsed identically
by any model, while prose interpretation drifts between models. Without
this, mixed-model assignment (B4) is unsafe.

## D3. Deterministic skill routing as runtime pre-step

Promote `deterministic-skill-router` + `skill-confidence-scorer` from
diagnostics to a mandatory orchestrator pre-step.

**Logging requirement:** orchestrator logs the routing rule that fired
before invoking any skill. Log line format:

```
[router] request="<user request>" matched_rule="<rule-id>" skill="<skill-name>" confidence=<0-100>
```

If confidence < threshold (default 70), orchestrator falls back to
asking the user instead of silently picking via semantic similarity.

## D4. Mandatory `apex-application-audit`

`apex-application-audit` becomes non-optional after Phase 8.

Output is merged into `panel_response.md` from A3 — the audit's findings
become additional "panel-style" findings the author response round must
address.
