# Run Mode and Budgets

## Run Mode Interface

Add or reserve this block in `inputs/application_context.md`:

```text
## RUN_MODE
ENSEMBLE_PHASE_1_7: [screening-lead, technical-lead, ats-format-lead]
ENSEMBLE_PHASE_8:   [screening-lead, technical-lead, ats-format-lead]
MAX_REVISION_PASSES: 1
```

## Run Mode Semantics

### `ENSEMBLE_PHASE_1_7`

- `[]`
  Use the existing single-agent strategy path outside the v1 topology.
- `[one writer]`
  Run one strategy author server only. Skip `C1`. The selected writer publishes
  the final canonical strategy output directly before shutdown.
- `[two or three writers]`
  Run one strategy author server per selected writer and then run `C1`.

### `ENSEMBLE_PHASE_8`

- `[]`
  Use the existing single-agent document path outside the v1 topology.
- `[one writer]`
  Run one document author server only. Skip `C2`. The selected writer publishes
  the final canonical document output set directly before shutdown.
- `[two or three writers]`
  Run one document author server per selected writer and then run `C2`.

### Advisor Inference

For a given fold, advisors are inferred from the other selected writers in that
same fold.

Examples:

- `S1` active with all three writers selected:
  advisors = `technical-lead`, `ats-format-lead`
- `S1` active with `[screening-lead, technical-lead]`:
  advisors = `technical-lead`
- one selected writer:
  advisors = none

`qa-auditor` is not inferred from these lists. It is present whenever the
server requires canonical testing and closing.

## Budget Interface

Reserve this block in `inputs/application_context.md`:

```text
## BUDGETS
MAX_ROUND_TOOL_CALLS: 40
MAX_ROUND_TOKENS: 120000
MAX_ADVISOR_MESSAGES: 8
MAX_REVISION_PASSES: 2
ON_BUDGET_EXCEEDED: DEGRADE_AND_FLAG
```

## Budget Semantics

### `MAX_ROUND_TOOL_CALLS`

- applies to the writer on a given server
- counted per IMPLEMENT/TEST/DISCUSS server cycle

### `MAX_ROUND_TOKENS`

- approximate token budget for the writer on a given server cycle

### `MAX_ADVISOR_MESSAGES`

- applies per advisor, per server cycle
- covers TEST-phase `send`/`broadcast` plus the single DISCUSS message
- DISCUSS still remains limited to one structured discuss message per advisor

### `MAX_REVISION_PASSES`

- maximum number of writer revision passes on the same author server

### `ON_BUDGET_EXCEEDED`

Expected values:

- `DEGRADE_AND_FLAG`
- `HARD_STOP`
- `ASK_USER`

Default v1 assumption:

- `DEGRADE_AND_FLAG`

## Non-Goals in V1

- No separate config key is introduced for a dedicated tester budget.
- No budget enforcement mechanism is implemented in v1.
- The values above are an interface contract only for future implementation.
