# Rollout order and intentionally-parked items

> Companion to [00_consolidated_plan.md](00_consolidated_plan.md). Canonical 15-step rollout
> table is in that file. This document expands rationale per step and
> documents what is NOT in v2 and why.

## Rollout (15 steps)

The canonical step table is in [00_consolidated_plan.md §"Recommended rollout order"](00_consolidated_plan.md).

### Per-step expansion

**Step 1 — C1, C2 (filesystem foundation).** Per-author draft folders
plus all four `write_scope` variants (writer / advisor /
canonical-tester / consensus-writer). This step has zero behavioural
impact in single-agent mode and is therefore safe to ship first.

**Step 2 — A1 server manifest documentation.** Document only; no
servers are launched yet. Establishes ports, run-dir conventions, and
the writer-vs-advisor-vs-canonical-tester matrix per server.

**Step 3 — G1 budgets.** Required before any iterative loop; otherwise
the first ensemble pilot has unbounded cost risk.

**Step 4 — A2 RUN_MODE.** Defaults to `[]` (single-agent), so this is
a no-op for existing users until they opt in.

**Step 5 — B3 .agent.md tightening.** Splice the three new sections
(Context Scoping, Voice & Emphasis, Advisor Mode) into the existing
six `.agent.md` files. Concrete overlays are in the
[archived v2 templates](../archive/legacy-multi-agent/version2/templates/agent_overlays/).

**Step 6 — Strategy fold pilot.** Run P0a → human gate → P0b → S1/S2/S3
→ C1 on one real vacancy. This is the smallest end-to-end proof. Each
author server runs with one writer + two advisors + qa-auditor.

**Step 7 — B1, B2.** Per-section default-lead rules and disagreement
log. These define how C1 and C2 actually merge — without them,
qa-auditor falls back to silent style preferences.

**Step 8 — `apex-user-feedback-revision` as the sole pre-Phase-8 gate.**
This is the **replacement** for the deleted v1 A3 (pre-generation
red-team pass). It catches gaps and unsupported claims before the
expensive document fold.

**Step 9 — Document fold pilot.** D1/D2/D3 + C2 for Option 1/2/3.
Confirms the second ensemble fold works.

**Step 10 — A4.** Bounded critic-author revision per generator, inside
each author server.

**Step 11 — E1, E2.** Metric-lineage guardian and JD-coverage floor.
Critical once parallel document drafting is live.

**Step 12 — A3, D4.** Author response to independent evaluation +
mandatory `apex-application-audit`.

**Step 13 — D1.** Promote `evidence-ranking-engine` to runtime gate.

**Step 14 — D2, B4.** Structured handoff schema first, then mixed-model
assignment. Order matters: B4 is unsafe without D2.

**Step 15 — C3, F2.** Optional `check_scope.py` verifier and blind A/B
comparator. Polish.

## Intentionally parked

These items are explicitly **not** in v2 and the reason is documented
so future contributors don't redo the analysis.

### Bundled coordination shim
Reimplementing `agent_sync` would be substantial work for marginal
benefit. Stock `agent_sync` works.

### Splitting `ats-format-lead` by target system (INSPIRA / UNICEF / IOM)
Only one TARGET_SYSTEM applies per run, so a per-system split would
idle two of three writers. The per-perspective ensemble (Tier A1)
already gives the diversity benefit a per-system split would provide.

### Numeric tournament scoring for QA
Replaced by per-section collaboration rules (B1) and disagreement logs
(B2). Numeric scoring on aesthetic outputs is brittle and produces
false precision.

### Forking `agent_sync` to support multiple writers per server
Replaced by the multi-server, single-writer-per-server,
advisors-co-resident topology (A1). No upstream fork needed.

### Pre-generation red-team pass (formerly v1 A3 / Round 2.5)
**Deleted in v2.** Pre-Phase-8 gap review is owned exclusively by
`apex-user-feedback-revision` (Phase 7.5), surfaced at rollout step 8.

Why deleted:
- v1 A3 ran `independent-shortlisting-redteam` before document
  generation, but that agent's contract is rejection-risk analysis on
  finished documents. Running it on a strategy report is a category
  error.
- `apex-user-feedback-revision` already implements the structurally
  correct pre-Phase-8 gate: it surfaces gaps, mitigation strategies,
  and a "Metrics & Specifics Needed" list, with an intent-gate so user
  edits are not blindly absorbed.
- Two pre-generation gates would duplicate work and create conflicting
  remediation lists.

### A unified "consensus" agent persona
Considered: making consensus a separate `.agent.md` instead of a role
played by `qa-auditor` on C1/C2. Rejected because qa-auditor is
already the canonical phase closer in apex-agent-sync-protocol, and a
separate persona would compete with it.
