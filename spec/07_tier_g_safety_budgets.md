# Tier G — Operational safety (deep dive)

> Companion to [00_consolidated_plan.md §"Tier G"](00_consolidated_plan.md).

## G1. `## BUDGETS` block

### Add to `application_context.md`

```
## BUDGETS
MAX_ROUND_TOOL_CALLS: 40           # per IMPLEMENT round, per writer
MAX_ROUND_TOKENS: 120000           # approximate, per round, per writer
MAX_ADVISOR_MESSAGES: 8            # per advisor per round
MAX_REVISION_PASSES: 2             # critic-author loop cap (also in RUN_MODE)
ON_BUDGET_EXCEEDED: DEGRADE_AND_FLAG   # alternatives: HARD_STOP | ASK_USER
```

### Default values rationale

| Field | Default | Rationale |
|---|---|---|
| `MAX_ROUND_TOOL_CALLS` | 40 | Empirical: most successful rounds use < 25 tool calls; 40 is generous headroom. |
| `MAX_ROUND_TOKENS` | 120000 | Approx 4× a typical strategy-report round. |
| `MAX_ADVISOR_MESSAGES` | 8 | Empirical: > 8 messages per advisor floods the writer. |
| `MAX_REVISION_PASSES` | 2 | Pass 1 + 1 revision. Beyond pass 2, returns diminish sharply. |
| `ON_BUDGET_EXCEEDED` | `DEGRADE_AND_FLAG` | Best UX default — return partial output with a flag rather than hard-stop. |

### Counter file conventions

Per server, in `tmp/_budget_<server>.json`:

```json
{
  "server": "S1",
  "round": 1,
  "writer": {
    "agent": "screening-lead",
    "tool_calls": 18,
    "tokens_estimate": 42000
  },
  "advisors": {
    "technical-lead": {"messages_sent": 3},
    "ats-format-lead": {"messages_sent": 5}
  },
  "revision_pass": 1
}
```

### Enforcement points

- **Pre-IMPLEMENT (writer):** `qa-auditor` reads
  `tmp/_budget_<server>.json` and refuses to advance if writer's
  `tool_calls` ≥ `MAX_ROUND_TOOL_CALLS` for the current round.
- **Pre-TEST (advisor send):** advisor self-checks its own
  `messages_sent` count and refuses additional sends past
  `MAX_ADVISOR_MESSAGES` (prompt-level discipline; not mechanically
  enforced).
- **Pre-DISCUSS:** `qa-auditor` re-reads counters and decides
  whether to invoke `discuss-done --next-impl <writer>` (loop) or
  `discuss-done --next-impl shutdown` (terminate) based on
  `revision_pass` and `MAX_REVISION_PASSES`.

### `ON_BUDGET_EXCEEDED` policies

| Value | Behavior |
|---|---|
| `DEGRADE_AND_FLAG` | qa-auditor closes the round; writer's draft is taken as-is; `_discussion/disagreement_log.md` gains a `BUDGET_EXCEEDED` entry. |
| `HARD_STOP` | qa-auditor refuses to close; user is asked to intervene. |
| `ASK_USER` | qa-auditor pauses and emits a user-facing question via the orchestrator's question channel. |

### Per-server budget isolation

In ensemble mode, budgets are **per-server**. A slow S2 does not starve
S1 or S3 of budget. The orchestrator aggregates final cost in a
post-run summary but does not preempt across servers.
