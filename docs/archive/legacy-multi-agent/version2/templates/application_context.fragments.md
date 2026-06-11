# application_context.md fragments

Paste-ready snippets for the future implementation task. These splice
into `.agents/application_context.template.md` and into the user's
`inputs/application_context.md`.

---

## Fragment 1 — `## RUN_MODE`

```
## RUN_MODE
# Empty list = single-agent mode (current default; no ensemble servers launched).
# One name = that name is writer; ats-format-lead participates as advisor (if not the named writer) on each server.
# Two or three names = ensemble fold launched per phase.
ENSEMBLE_PHASE_1_7: []                                                   # e.g., [screening-lead, technical-lead, ats-format-lead]
ENSEMBLE_PHASE_8:   []                                                   # same shape
MAX_REVISION_PASSES: 1                                                    # critic-author cap inside each author server
JD_COVERAGE_FLOOR:  0.70                                                  # E2; 0.0 to disable
```

Resolution rules: see [../spec/01_tier_a_ensemble_workflow.md §A2](../spec/01_tier_a_ensemble_workflow.md).

`qa-auditor` is always co-resident on author servers as canonical
tester regardless of `RUN_MODE`. The user does not need to list
`qa-auditor` in `ENSEMBLE_PHASE_*`.

---

## Fragment 2 — `## BUDGETS`

```
## BUDGETS
MAX_ROUND_TOOL_CALLS: 40            # per IMPLEMENT round, per writer
MAX_ROUND_TOKENS:     120000        # approximate, per round, per writer
MAX_ADVISOR_MESSAGES: 8             # per advisor per round (prompt-level convention)
MAX_REVISION_PASSES:  2             # critic-author loop cap; also referenced in RUN_MODE
ON_BUDGET_EXCEEDED:   DEGRADE_AND_FLAG    # alternatives: HARD_STOP | ASK_USER
```

Counter file convention: `tmp/_budget_<server>.json`. See
[../spec/07_tier_g_safety_budgets.md](../spec/07_tier_g_safety_budgets.md).

---

## Fragment 3 — `## ENSEMBLE_TOPOLOGY` (informational, optional)

Some users may want to declare per-server overrides (e.g., a different
port). This is optional and the orchestrator falls back to
[../topology/server_manifest.yaml](../topology/server_manifest.yaml) defaults
if absent.

```
## ENSEMBLE_TOPOLOGY
# Override defaults from multi-agent-version2/topology/server_manifest.yaml.
# Omit any server you don't need; orchestrator uses defaults.
S1: { port: 9811, run_dir: tmp/agent_sync/s1 }
S2: { port: 9812, run_dir: tmp/agent_sync/s2 }
S3: { port: 9813, run_dir: tmp/agent_sync/s3 }
C1: { port: 9820, run_dir: tmp/agent_sync/c1 }
D1: { port: 9831, run_dir: tmp/agent_sync/d1 }
D2: { port: 9832, run_dir: tmp/agent_sync/d2 }
D3: { port: 9833, run_dir: tmp/agent_sync/d3 }
C2: { port: 9840, run_dir: tmp/agent_sync/c2 }
P0a: { port: 9800, run_dir: tmp/agent_sync/p0a }
P0b: { port: 9801, run_dir: tmp/agent_sync/p0b }
```
