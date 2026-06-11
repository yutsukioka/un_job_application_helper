# ApexStrategist — Consolidated Multi-Agent Improvement Plan (v2)

This is the canonical, fully-corrected v2 plan, integrating the original
proposal with everything agreed in subsequent rounds **and** all seven
follow-up edits identified during design review.

For the per-edit audit trail vs. v1, see [../CHANGELOG.md](../CHANGELOG.md).

---

## Foundation: design principles

1. **Two-fold ensemble, not linear pipeline.** The three authoring agents
   work in parallel twice: once for the strategy (Phase 1–7), once for
   documents (Phase 8). Each parallel round is followed by a consensus
   round.
2. **Opt-in, user-controlled cost.** Single-agent linear mode remains the
   default. Users choose ensemble depth per vacancy via `## RUN_MODE` in
   `application_context.md`.
3. **Multi-server, single-writer-per-server, advisors-co-resident.** Stock
   `agent_sync` allows only one implementer per server. Parallelism is
   achieved by running multiple servers concurrently. On each author
   server, all three authoring agents are present plus `qa-auditor`, but
   only one is the designated writer; the other authoring agents
   participate as **advisors** (read-only, contributing via
   `send`/`broadcast` in TEST and `discuss` in DISCUSS), and `qa-auditor`
   is the **canonical tester**. This gives every draft cross-perspective
   input **within the same author-server review loop, before consensus** —
   not only after.
4. **Filesystem-as-protocol, compatibility-first.** Per-author draft
   folders isolate parallel writes, while canonical outputs stay at the
   existing flat paths until prompt migration is complete.
5. **Collaboration rules over numeric tournaments.** Per-section default
   leads, evidence-only overrides, and disagreement logs replace brittle
   quantitative scoring. QA enforces process, not preference.
6. **Build on existing assets.** Extend the six existing `.agent.md` files
   and existing skills; do not invent parallel personas or competing
   skills.
7. **Stock `agent_sync` semantics are non-negotiable.** Single implementer
   per server; first `test-result` advances TEST → DISCUSS;
   `discuss-done --next-impl` from `qa-auditor` is the canonical phase
   closer; `listen` is destructive and there is no retrospective
   send/broadcast retrieval.

---

## Tier A — Two-fold ensemble authoring

### A1. Two-fold ensemble workflow via multi-server topology with author + advisors + canonical tester

Keep stock `agent_sync` unchanged. Run separate servers for parallelism,
and on each author server place exactly **one writer + two advisors +
`qa-auditor` (canonical tester)**. Advisors are `INTERNAL_QA`-mode peers
that may not write files and may not call `test-result`; they post
structured TEST messages (`send`/`broadcast`) and one structured DISCUSS
message (`discuss`). `qa-auditor` is the only agent on the server allowed
to call `test-result` and `discuss-done --next-impl`.

#### Per-server phase loop (applies to S1, S2, S3, D1, D2, D3)

```text
IMPLEMENT
  - WRITER only writes files (to its draft subfolder).
  - Advisors and qa-auditor are silent.
  - WRITER ends with go-test when its draft is review-ready.

TEST
  - Advisors send review notes via `send` (writer-targeted) or `broadcast`.
  - WRITER and qa-auditor consume incoming messages via `listen`.
    NOTE: `listen` is destructive and stock agent_sync exposes NO
    retrospective send/broadcast retrieval API. The writer or qa-auditor
    must consume and export incoming TEST-phase advisor messages while
    the server is still live, before shutdown.
  - WRITER or qa-auditor appends consumed advisor messages to
    `_discussion/advisor_notes_<server>.md` (live persistence).
  - qa-auditor submits `test-result` (PASS or FAIL with structured issues)
    — this is the call that advances TEST -> DISCUSS.

DISCUSS
  - Each advisor (and qa-auditor) submits exactly one structured
    `discuss` message per the apex-agent-sync-protocol rule.
  - WRITER or qa-auditor calls `get-discussion` and appends its content
    to `_discussion/advisor_notes_<server>.md`.
  - All non-qa agents call `discuss-done` WITHOUT `--next-impl`.
  - To loop, qa-auditor uses `discuss-done --next-impl <writer-name>`.
  - To end, qa-auditor calls `discuss-done` without `--next-impl`, then
    calls the separate `shutdown --reason "<server> complete"` command.
  - Never use `discuss-done --next-impl shutdown` or any shutdown marker wording.

REVISION CAP
  - The IMPLEMENT/TEST/DISCUSS loop repeats at most MAX_REVISION_PASSES
    times per server (default 1).
  - Cross-pollination from advisors reaches the writer via the
    advisor_notes file in the next IMPLEMENT pass (the writer reads
    it before resuming work).
```

#### Server topology (10 servers)

```text
Server P0a  (prep stage 1: classification + requirements + ledger)
Port: 9800
WRITER:    screening-lead
ADVISORS:  technical-lead, ats-format-lead
TESTER:    qa-auditor (canonical tester)
Writes only:
  - classification_proposal.md
  - phase1_2_core_requirements.md
  - metric_ledger.md

[ HUMAN CONFIRMATION GATE on classification_proposal.md ]

Server P0b  (prep stage 2: CCOG resolved subset)
Port: 9801
WRITER:    technical-lead
ADVISORS:  screening-lead, ats-format-lead
TESTER:    qa-auditor
Writes only:
  - ccog_reference_resolved.md

Server S1  (strategy author: screening lens)
Port: 9811
WRITER:    screening-lead
ADVISORS:  technical-lead, ats-format-lead
TESTER:    qa-auditor
Writes:
  - screening-lead/phase1_7_strategy_report.md
  - _discussion/advisor_notes_S1.md (writer or qa-auditor only)

Server S2  (strategy author: technical lens)
Port: 9812
WRITER:    technical-lead
ADVISORS:  screening-lead, ats-format-lead
TESTER:    qa-auditor
Writes:
  - technical-lead/phase1_7_strategy_report.md
  - _discussion/advisor_notes_S2.md

Server S3  (strategy author: ATS/format lens)
Port: 9813
WRITER:    ats-format-lead
ADVISORS:  screening-lead, technical-lead
TESTER:    qa-auditor
Writes:
  - ats-format-lead/phase1_7_strategy_report.md
  - _discussion/advisor_notes_S3.md

Server C1  (strategy consensus)
Port: 9820
WRITER:    qa-auditor
ADVISORS:  screening-lead, technical-lead, ats-format-lead
Writes:
  - phase1_7_strategy_report.md            (canonical, flat path)
  - _discussion/round2_consensus.md
  - _discussion/disagreement_log.md

Server D1  (document author: screening lens)
Port: 9831
WRITER:    screening-lead
ADVISORS:  technical-lead, ats-format-lead
TESTER:    qa-auditor
Writes:
  - screening-lead/option*.md
  - _discussion/advisor_notes_D1.md

Server D2  (document author: technical lens)
Port: 9832
WRITER:    technical-lead
ADVISORS:  screening-lead, ats-format-lead
TESTER:    qa-auditor
Writes:
  - technical-lead/option*.md
  - _discussion/advisor_notes_D2.md

Server D3  (document author: ATS/format lens)
Port: 9833
WRITER:    ats-format-lead
ADVISORS:  screening-lead, technical-lead
TESTER:    qa-auditor
Writes:
  - ats-format-lead/option*.md
  - _discussion/advisor_notes_D3.md

Server C2  (document consensus)
Port: 9840
WRITER:    qa-auditor
ADVISORS:  screening-lead, technical-lead, ats-format-lead
Writes:
  - option1_admin_profile.md               (canonical, flat path)
  - option2_cv.md                          (canonical, flat path)
  - option3_cover_letter.md                (canonical, flat path)
  - option4_qualification_answers.md       (if selected)
  - _discussion/round4_consensus.md
  - _discussion/disagreement_log.md

Post-eval (existing independent evaluation session(s))
Behavior: unchanged from current design.
```

Execution sequence:

```text
Prep stage
  -> P0a (writer: screening-lead) produces classification_proposal,
     phase1_2_core_requirements, metric_ledger
  -> shut down P0a
  -> HUMAN CONFIRMATION on classification_proposal.md
  -> P0b (writer: technical-lead) produces ccog_reference_resolved.md
  -> shut down P0b
  -> freeze all four prep artifacts for downstream rounds

Strategy fold
  -> S1 + S2 + S3 launched concurrently
  -> each runs IMPLEMENT -> TEST -> DISCUSS, optionally bounded loop
     under MAX_REVISION_PASSES, advisor_notes_*.md persisted live
  -> shut down S1/S2/S3
  -> C1 merges three drafts to canonical phase1_7_strategy_report.md
  -> shut down C1

Phase 7.5 — apex-user-feedback-revision (sole pre-Phase-8 gap review gate)

Document fold
  -> D1 + D2 + D3 launched concurrently (same per-server loop)
  -> shut down D1/D2/D3
  -> C2 merges to canonical option*.md
  -> shut down C2

Post-eval
  -> independent-panel-evaluator + independent-shortlisting-redteam
```

**Why writer + advisors + canonical tester rather than three blind solo
drafts:**
- Diversity is preserved (different writer = different voice/emphasis),
  but each draft is informed by three lenses within the same author-server
  review loop, so consensus rounds C1/C2 face smaller, more substantive
  disagreements.
- Stock `agent_sync` semantics remain intact: single implementer per
  server; advisors only `send`/`broadcast`/`discuss`; only `qa-auditor`
  calls `test-result`.

**Risk mitigation:**
- Each server has a clear named WRITER. Advisor messages must include
  `ADVISOR_TO=<writer-name>` so the writer can filter signal from noise
  (prompt-level convention; not runtime-enforced).
- Advisors are forbidden from calling `test-result` and writing files
  (mechanical enforcement via `write_scope.allowed_paths: []`; see C2).
- Prep artifacts produced in P0a/P0b are read-only inputs for S1/S2/S3
  and D1/D2/D3.
- Round 2 unified report must include an `## Open Questions` section
  listing unresolved disagreements.
- Do not run two live servers concurrently with the same agent name as
  WRITER. Advisor co-residency across multiple servers is fine.
- Author-fold servers (S1/S2/S3 or D1/D2/D3) must be shut down before
  the corresponding consensus server starts.
- Before any author server is shut down, verify that
  `_discussion/advisor_notes_<server>.md` exists and is non-empty
  (advisor messages cannot be retrieved retrospectively).

### A2. Opt-in `## RUN_MODE` configuration

Add to `application_context.md`:

```
## RUN_MODE
ENSEMBLE_PHASE_1_7: [screening-lead, technical-lead, ats-format-lead]   # or [] for single-agent
ENSEMBLE_PHASE_8:   [screening-lead, technical-lead, ats-format-lead]   # or subset
MAX_REVISION_PASSES: 1                                                   # critic-author cap
```

Empty list = single-agent mode for that phase. The `RUN_MODE` list defines
who is **writer** on which fold; advisors are inferred as the remaining
authoring agents from the same list. `qa-auditor` is always co-resident
on author servers as the canonical tester regardless of RUN_MODE.

### A3. Author response round to independent evaluation (Round 5.5)

After E1/E2 (Round 5), `screening-lead` writes a `panel_response.md`
accepting, contesting (with evidence), or deferring each finding. QA
produces a final `remediation_plan.md`.

**Why:** Converts the panel review from one-way verdict into structured
negotiation. A false-positive panel finding no longer forces unnecessary
rewrites.

> **Note:** v1 had an "A3. Pre-generation red-team pass (Round 2.5)"
> that has been **deleted** in v2. Pre-Phase-8 gap review is owned
> exclusively by `apex-user-feedback-revision` at rollout step 8.

### A4. Bounded critic-author revision per generator

For each Phase 8 document: writer produces draft → `qa-auditor` runs
structured `TEST::FAIL` with line-level issues → **same writer** revises
in a bounded second pass. Stop after pass 2 (controlled by
`MAX_REVISION_PASSES`). In ensemble mode this happens **inside each
author server** before the consensus round.

---

## Tier B — Persona definition and model assignment

### B1. Per-section default-lead collaboration rules

Each Phase 8 artifact's sections have a designated lead. Other agents flag
concrete defects, not preferences. Overrides require evidence (e.g.,
citing a JD line or strategy-report section).

| Artifact section | Default lead | Other agents' role |
|---|---|---|
| Admin Profile — duties/responsibilities body | screening-lead | ats-format-lead proposes keyword swaps; technical-lead flags register issues |
| Admin Profile — Direct Reports / Reason for Leaving | screening-lead | factual, low-disagreement |
| CV — Summary / UVP line | ats-format-lead | others propose alternative phrasings |
| CV — Experience bullets | screening-lead (content) + ats-format-lead (keyword pass) | technical-lead reviews technical accuracy |
| Cover letter — narrative arc | screening-lead | others comment, do not rewrite |
| Cover letter — technical paragraph | technical-lead | others comment |
| Qualification answers | screening-lead | ats-format-lead does final char-fit pass |
| Motivation statement (VACC) | screening-lead (V/A/C) + technical-lead (Competency) | ats-format-lead does char-fit |
| Competency mapping | technical-lead | others cross-check |

**`qa-auditor`'s redefined role:** verify (a) all flags addressed or
explicitly dismissed with reason, (b) merged section passes
lint/char/placeholder checks, (c) no new claims introduced beyond the
unified Phase 1–7 report. `qa-auditor` does **not** pick winners on
style.

### B2. Disagreement log instead of forced merge

Unresolved disagreements are appended to `_discussion/disagreement_log.md`
and surfaced to the user. Two or three competing phrasings is genuine
information for the candidate, not a defect to hide.

### B3. Tighten the existing six `.agent.md` files

Edit (don't replace) each authoring `.agent.md` to add three new sections.
Concrete overlay text is in [../templates/agent_overlays/](../templates/agent_overlays/).

- `## Context Scoping` — which `application_context.md` sections and
  which earlier-phase artifacts the agent reads vs. ignores. Forces real
  perspective divergence by hiding context selectively.
  - `screening-lead`: full context, emphasizes evidence bank,
    requirements, qualification questions.
  - `technical-lead`: full context, emphasizes CCOG resolved subset,
    technical sections of JD, methodology terms.
  - `ats-format-lead`: full JD + keyword bank + format profiles + char
    limits; minimal coaching/reflection content.
- `## Voice & Emphasis` — how the agent writes:
  - `screening-lead`: competency-language, "demonstrated ability to…",
    evidence-density-first.
  - `technical-lead`: register-correct technical terms from CCOG,
    programmatic scope/scale, methodology specificity.
  - `ats-format-lead`: keyword-density first, JD-phrase mirroring,
    strict format-profile compliance.
- `## Advisor Mode` — how the agent behaves when it is co-resident on a
  server but is not the writer:
  - **IMPLEMENT phase:** stay silent.
  - **TEST phase:** may call `send` (writer-targeted) or `broadcast`.
    Must NOT call `test-result` (only `qa-auditor` calls test-result).
    Each advisor message MUST begin with `ADVISOR_TO=<writer-name>`
    (prompt-level convention).
  - **DISCUSS phase:** submit exactly one structured `discuss` message
    per the apex-agent-sync-protocol rule. Call `discuss-done`
    WITHOUT `--next-impl` (only `qa-auditor` may close with --next-impl).
  - **All phases:** never write files. Advisor `write_scope.allowed_paths`
    is `[]`.
  - Stick to its primary lens (e.g., `ats-format-lead` advising
    `screening-lead` on S1 should comment on keyword/format only, not
    rewrite competency framing).
  - Cap messages per round (see G1 `MAX_ADVISOR_MESSAGES`, default 8).

`qa-auditor` and the two independent agents stay structurally as-is, but
`qa-auditor` gains the canonical-tester role on every author server (see
[../templates/agent_overlays/qa-auditor.overlay.md](../templates/agent_overlays/qa-auditor.overlay.md)).

### B4. Mixed-model assignment (recommended)

Run different agents on different LLM model classes to reduce correlated
error. Suggested mapping:

| Agent | Model class rationale |
|---|---|
| `screening-lead` | Strong long-context synthesis (e.g., Claude Opus/Sonnet) |
| `technical-lead` | Strong domain precision and classification matching (e.g., GPT-4-class) |
| `ats-format-lead` | Rule-following, keyword discipline; smaller/faster models suffice (e.g., GPT-4o-mini, Claude Haiku) |
| `qa-auditor` | Match `screening-lead`'s class for credible review |

In VS Code Copilot, run each agent in a separate chat tab with a different
model selected; co-resident advisors on a given server are simply
additional chat tabs pointed at that server's port. Codex/Claude Code can
declare a preferred model per agent in `agents/openai.yaml`.

**Caveat:** mixed-model setups are more sensitive to prompt drift, so the
structured-handoff schema (item D2) becomes important.

---

## Tier C — Filesystem & scope discipline

### C1. Per-author draft folders with canonical flat paths preserved

```
output/generated_documents/history/<position-name>/
├── classification_proposal.md     ← canonical prep artifact (P0a)
├── phase1_2_core_requirements.md  ← canonical prep artifact (P0a)
├── metric_ledger.md               ← canonical prep artifact (P0a)
├── ccog_reference_resolved.md     ← canonical prep artifact (P0b)
├── phase1_7_strategy_report.md    ← canonical strategy output (existing path preserved)
├── option1_admin_profile.md       ← canonical document output (existing path preserved)
├── option2_cv.md                  ← canonical document output (existing path preserved)
├── option3_cover_letter.md        ← canonical document output (existing path preserved)
├── option4_qualification_answers.md
├── screening-lead/                ← only screening-lead writes (when WRITER on S1/D1)
│   ├── phase1_7_strategy_report.md
│   └── option*.md
├── technical-lead/                ← only technical-lead writes (when WRITER on S2/D2)
│   ├── phase1_7_strategy_report.md
│   └── option*.md
├── ats-format-lead/               ← only ats-format-lead writes (when WRITER on S3/D3)
│   ├── phase1_7_strategy_report.md
│   └── option*.md
└── _discussion/                   ← consensus servers append; writer/qa-auditor append on author servers
    ├── round2_consensus.md
    ├── round4_consensus.md
    ├── advisor_notes_S1.md         ← writer or qa-auditor exports here during S1
    ├── advisor_notes_S2.md
    ├── advisor_notes_S3.md
    ├── advisor_notes_D1.md
    ├── advisor_notes_D2.md
    ├── advisor_notes_D3.md
    └── disagreement_log.md
```

#### Advisor-note persistence (mandatory operational rule)

Advisor notes flow into `_discussion/advisor_notes_<server>.md` via two
distinct paths:

**TEST-phase persistence (live consume; non-recoverable if missed):**
- Stock `agent_sync` `listen` is **destructive** and there is **no
  retrospective send/broadcast retrieval API**.
- The writer or `qa-auditor` MUST consume and export incoming TEST-phase
  advisor messages while the server is still live, by calling `listen`
  and immediately appending the result to
  `_discussion/advisor_notes_<server>.md`.
- This export must complete **before** the server is shut down.
  Otherwise the advisor messages are permanently lost.

**DISCUSS-phase persistence (recoverable via get-discussion):**
- DISCUSS messages are held in stock `agent_sync`'s `S.discussion` and
  exposed via `get-discussion`. They are NOT persisted to logs/v6.
- Before each `discuss-done` is processed, the writer or `qa-auditor`
  calls `get-discussion` and appends its content to
  `_discussion/advisor_notes_<server>.md`.

**Ownership rules:**
- The advisor_notes_*.md file is written by the WRITER or `qa-auditor`
  only. Advisors NEVER write to it directly.
- This is mechanically enforced via the writer's `write_scope` (it
  includes the file in `allowed_paths`) and the advisor's `write_scope`
  (which has `allowed_paths: []`).

**Why this works:**
- Existing prompts and skills that read canonical outputs do not break,
  because the flat paths stay in place.
- Concurrent authoring is collision-free: S1/S2/S3 and D1/D2/D3 write
  only to their own draft subfolders, and advisor notes go to per-server
  files in `_discussion/` written by a single agent per server.
- Consensus is reviewable: C1/C2 compare three explicit draft folders,
  read the corresponding advisor-notes files for context on why each
  draft made certain choices, and then write one canonical flat-path
  output set.
- This layout supports gradual migration: draft folders first, canonical-
  path changes later only if the prompt layer is updated.

### C2. `write_scope` block by server role

For an author server (e.g., S1 with `screening-lead` as writer):

```yaml
# screening-lead.agent.md (when WRITER on S1)
write_scope:
  allowed_paths:
    - output/generated_documents/history/<position>/screening-lead/**
    - output/generated_documents/history/<position>/_discussion/advisor_notes_S1.md
  forbidden_paths:
    - output/generated_documents/history/<position>/technical-lead/**
    - output/generated_documents/history/<position>/ats-format-lead/**
    - output/generated_documents/history/<position>/phase1_7_strategy_report.md
    - output/generated_documents/history/<position>/option*.md
    - output/generated_documents/history/<position>/_discussion/round*_consensus.md
    - output/generated_documents/history/<position>/_discussion/disagreement_log.md
```

For an advisor on the same server (e.g., `technical-lead` co-resident on S1):

```yaml
# technical-lead.agent.md (when ADVISOR on S1)
write_scope:
  allowed_paths: []                                                # advisors never write files (mechanically enforced)
  # Fields below are PROMPT-LEVEL CONVENTION ONLY, not runtime-enforced
  # by stock agent_sync. Only allowed_paths / forbidden_paths are
  # mechanically checkable (see scripts/check_scope.py).
  allowed_messages:
    - send
    - broadcast
    - discuss
  forbidden_actions:
    - test-result
    - discuss-done --next-impl
    - any file write
  message_prefix_required: "ADVISOR_TO=screening-lead"
```

For `qa-auditor` (canonical tester) on an author server (e.g., S1):

```yaml
# qa-auditor.agent.md (when CANONICAL TESTER on S1)
write_scope:
  allowed_paths:
    - output/generated_documents/history/<position>/_discussion/advisor_notes_S1.md
  forbidden_paths:
    - output/generated_documents/history/<position>/screening-lead/**
    - output/generated_documents/history/<position>/technical-lead/**
    - output/generated_documents/history/<position>/ats-format-lead/**
    - output/generated_documents/history/<position>/phase1_7_strategy_report.md
    - output/generated_documents/history/<position>/option*.md
  # Prompt-level convention:
  allowed_messages:
    - send
    - broadcast
    - discuss
  allowed_phase_actions:
    - test-result                          # canonical tester
    - discuss-done --next-impl             # canonical phase closer
```

Consensus and prep servers invert author scope:
- Prep server P0a writer (`screening-lead`) may write only the three P0a
  prep artifacts. Prep server P0b writer (`technical-lead`) may write
  only `ccog_reference_resolved.md`.
- Consensus servers (C1/C2): writer = `qa-auditor`, may write canonical
  flat-path outputs and `_discussion/round*_consensus.md` +
  `_discussion/disagreement_log.md`. Advisors on C1/C2 follow the same
  advisor-mode rules above.
- Author servers may never edit canonical flat-path outputs directly.

Operational notes:
- Launch each server from its own run directory (e.g., `tmp/agent_sync/p0a`,
  `tmp/agent_sync/p0b`, `tmp/agent_sync/s1`, …) so v6 logs do not collide.
- The same agent name may be **advisor on multiple concurrent servers**
  (e.g., `technical-lead` advises on both S1 and S3 at once) because
  advisors don't write — but it may be **writer on only one live server
  at a time**.

### C3. Optional 30-line scope verifier

`scripts/check_scope.py` maps each changed file to its declared owner
and fails if any agent wrote outside its allowed paths, including an
explicit check that no advisor produced any file write. Run on demand
or as a pre-commit hook. Useful but not required for correctness. This
spec ships only the stub; implementation is deferred.

---

## Tier D — Promote diagnostics to runtime gates

### D1. Promote `evidence-ranking-engine` from diagnostic to runtime gate

Insert between `apex-candidate-evidence-bank` and any document generator.
Generators must consume the ranked evidence list, not the raw bank.

### D2. Shared structured-handoff schema between skills

Each phase artifact gets a paired JSON/YAML representation alongside the
human-readable markdown (e.g., `phase1_2_core_requirements.json`).
Downstream skills consume the structured form; humans read the markdown.

Especially important for mixed-model ensembles (B4) since structured data
is parsed identically by any model while prose interpretation drifts.

### D3. Activate deterministic skill routing

Promote `deterministic-skill-router` + `skill-confidence-scorer` from
diagnostics to the orchestrator's runtime pre-step. Orchestrator must
log the routing rule that fired before invoking any skill.

### D4. Mandatory `apex-application-audit` after Phase 8

Make the audit non-optional. Merge its findings into the
`panel_response.md` from A3.

---

## Tier E — Integrity guardians

### E1. `metric-lineage-guardian` micro-agent

Owns `metric_ledger.md`. Rejects any generator output containing a metric
not present in the ledger or carrying a different scope.

**Operational rule:** the metric ledger is authored once during P0a and
**frozen** during S1–S3 and D1–D3.

### E2. JD-coverage hard floor

Calculate the % of `JD_KEYWORD_BANK` and `term-extractor` 5★ priority
terms appearing in the unified Phase 8 outputs. Block finalization if
below a configurable floor (default e.g., 70% of 5★ terms present
somewhere in the document set).

---

## Tier F — Closed-loop self-improvement

### F1. Persist agent self-critique findings across runs

After each run, persist `independent_panel_evaluation.md` and red-team
findings to a per-vacancy-family `lessons.md` (memory/repo-scoped, not
user-scoped, no PII).

**Honest framing:** this is the **agent's own recurring self-critiques**
across runs of the same vacancy family, not access to hiring-panel
cognition.

On a new run for the same family, surface those prior self-critiques as a
checklist for Round 2 consensus.

### F2. Blind A/B variant comparator (optional)

Generate two CV variants (e.g., compressed-metrics vs. expanded-narrative),
strip identifying differences, ask `independent-panel-evaluator` to rank
blindly.

---

## Tier G — Operational safety

### G1. User-settable budgets in `## BUDGETS`

Add to `application_context.md`:

```
## BUDGETS
MAX_ROUND_TOOL_CALLS: 40           # per IMPLEMENT round (per writer in ensemble mode)
MAX_ROUND_TOKENS: 120000           # approximate, per round (per writer)
MAX_ADVISOR_MESSAGES: 8            # per advisor per round, prevents flooding
MAX_REVISION_PASSES: 2             # critic-author loop cap (also referenced in RUN_MODE)
ON_BUDGET_EXCEEDED: DEGRADE_AND_FLAG   # alternatives: HARD_STOP | ASK_USER
```

Enforcement: `apex-orchestrator-report` reads `## BUDGETS` at Phase 0
and stores the active budget. Each writer increments a counter in
`tmp/_budget_<server>.json`; each advisor increments its own message
counter in the same file. `qa-auditor`'s pre-TEST check reads the
counter and auto-closes the round if exceeded. In ensemble mode budgets
are per-server.

---

## Items intentionally parked

- **Bundled coordination shim.** `agent_sync` already provides a working
  local server.
- **Splitting `ats-format-lead` by target system.** Replaced by the
  per-perspective ensemble (Tier A).
- **Numeric tournament scoring for QA.** Replaced by per-section
  collaboration rules (B1) and disagreement logs (B2).
- **Forking `agent_sync` to support multiple writers per server.**
  Replaced by the multi-server, single-writer-per-server,
  advisors-co-resident topology (A1).
- **Pre-generation red-team pass (formerly A3 / Round 2.5).** Deleted in
  v2; pre-Phase-8 gap review is owned by `apex-user-feedback-revision`
  (Phase 7.5) at rollout step 8.

---

## Recommended rollout order

| Step | Items | Why this order |
|---|---|---|
| **1** | **C1, C2** — per-author draft folders + write_scope blocks (writer/advisor/canonical-tester variants) | Foundation. Enables parallel drafts with co-resident advisors. |
| **2** | **A1 (server manifest only)** — document the 10-server topology incl. writer + advisors + qa-auditor (canonical tester) per server, ports, run directories, launch/shutdown order | Required operational scaffolding before any ensemble run. |
| **3** | **G1** — `## BUDGETS` (incl. `MAX_ADVISOR_MESSAGES`) and enforcement | Safety precondition for any iterative loop. |
| **4** | **A2** — `## RUN_MODE` opt-in | Lets users choose single vs. ensemble per vacancy. |
| **5** | **B3** — Tighten the existing six `.agent.md` files (Context Scoping, Voice & Emphasis, Advisor Mode incl. TEST/DISCUSS phase rules) | Ensures meaningfully different drafts and that advisors stay in lane. |
| **6** | **Pilot strategy fold only** — P0a → human gate → P0b → S1/S2/S3 → C1, with one writer + two advisors + qa-auditor per author server | Smallest end-to-end proof. |
| **7** | **B1, B2** — Per-section default-lead rules + disagreement log | Defines how C1/C2 merge conflicts should be resolved. |
| **8** | **Reuse `apex-user-feedback-revision` as the sole pre-Phase-8 gap-review gate** | Cheapest place to catch gaps before paying for the document fold. |
| **9** | **Pilot document fold only** — D1/D2/D3 + C2 for Option 1/2/3 | Confirms the second ensemble fold. |
| **10** | **A4** — Bounded critic-author revision per generator | Adds quality lift once topology is stable. |
| **11** | **E1, E2** — Metric-lineage guardian + JD-coverage floor | Critical once parallel document drafting is live. |
| **12** | **A3, D4** — Author response to independent evaluation + mandatory `apex-application-audit` | Strengthens post-generation correction loop. |
| **13** | **D1** — Promote evidence-ranking to runtime gate | Tightens generator inputs. |
| **14** | **D2, B4** — Structured handoff schema, then mixed-model assignment | Mixed-model benefit is real only after handoff parsing is deterministic. |
| **15** | **C3, F2** — Optional `check_scope.py` verifier and blind A/B comparator | Polish and empirical validation. |

---

## The thesis, restated

The current design has the **personas** of a multi-agent system but the
**runtime** of a linear single-author pipeline. The improvements above
move runtime behavior to match the design intent without forking
`agent_sync`:

- **Real diversity** (parallel ensemble across two folds, optionally with
  mixed models),
- **Cross-pollination within each author-server review loop** (one writer
  + two co-resident advisors + canonical tester per server, instead of
  three blind solo drafts),
- **Collision-free coordination** (multi-server single-writer-per-server,
  per-author draft folders, no `agent_sync` fork required),
- **Process-based merging** (per-section leads, evidence-only overrides,
  disagreement logs — not numeric tournaments),
- **Defended invariants** (metric-lineage guardian, JD-coverage floor,
  mandatory audit),
- **Bounded loops** (revision caps, advisor-message caps, and budgets),
- **Empirical learning over time** (persisted self-critiques as a
  vacancy-family checklist).

Every change extends the existing six `.agent.md` files and existing
skills rather than introducing parallel personas. The default user
experience (single-agent linear mode) is unchanged; ensemble depth is
opt-in per vacancy via `## RUN_MODE`. This gives a clear, incremental
path from today's pipeline to a robust multi-agent system without a
single big-bang rewrite and without modifying the upstream `agent_sync`
runtime.

## Section: Discussion Quality Standards

### Advisor Content Quality Gate

The pre-shutdown check "advisor_notes_<server>.md exists and is non-empty"
is NECESSARY but NOT SUFFICIENT. The following additional quality gate
applies to all servers (P0a, P0b, S*, D*, C*):

**Each advisor's notes for a given server MUST contain at least one entry
that includes all three of the following fields:**

```
OBSERVATION: <what was reviewed — specific artifact section, quality
             dimension, or compliance criterion>
FINDING:     <specific issue, risk, strength, or gap identified —
             must reference concrete content, not generic status>
RECOMMENDATION: <action: ACCEPT / REVISE <specific change> /
                 FLAG_FOR_USER <reason>>
```

**Validation rules:**
- Pure confirmations without specifics do NOT satisfy the gate.
  Examples of FAILING entries:
  - "No blocker; artifact is ready for next step."
  - "Looks good. No issues found."
  - "PASS — ready to proceed."
- Examples of PASSING entries:
  - "OBSERVATION: Option 1, Duty 3 (data management). FINDING: The duty
    description uses 'managed' without specifying team size or data volume,
    which weakens the INSPIRA screening score. RECOMMENDATION: REVISE —
    add '5-member team' and 'datasets covering 47 counties' per
    metric_ledger entries ML-012 and ML-023."
  - "OBSERVATION: Cover letter paragraph 2. FINDING: Strong alignment
    with JD requirement 'experience in humanitarian data systems' —
    cites UNICEF Kenya HCT dashboard with specific coverage metrics.
    RECOMMENDATION: ACCEPT — no change needed; this is a model paragraph."

**If the gate fails**, the server MUST NOT shut down. qa-auditor reopens
the round and requests specific technical observations from the
non-compliant advisor(s).

### Minimum Revision Justification

If ALL advisors AND qa-auditor report zero revision-worthy issues for a
server, qa-auditor MUST produce a `ZERO_REVISION_JUSTIFICATION` block
before issuing shutdown:

```
ZERO_REVISION_JUSTIFICATION:
  server: <server_id>
  pass_number: <n>
  criteria_checked:
    - <criterion_1>: <status and evidence>
    - <criterion_2>: <status and evidence>
    ...
  conclusion: No revision needed because <specific reasoning>.
```

This block is appended to `advisor_notes_<server>.md`.

### Extended Discuss Phase

The base protocol allows each advisor exactly one structured discuss
message. The following extension applies:

- If Advisor A's FINDING directly contradicts or materially extends
  Advisor B's FINDING on the same artifact section, qa-auditor MAY
  request ONE follow-up round.
- In the follow-up round, each involved advisor submits one additional
  structured message responding to the contradiction/extension.
- qa-auditor then closes the discuss phase normally.
- Maximum discuss messages per advisor per server: **2** (base + 1
  follow-up). No further extensions.

---

## Section: CCOG Resolver Scoring Refinement

### Title-Mismatch Penalty

Before score sorting, apply the following penalty:

1. Extract content words (nouns, adjectives) from the vacancy title
   after stopword removal. Call this set `V_title`.
2. Extract content words from the CCOG entry title after stopword
   removal. Call this set `C_title`.
3. Compute `title_overlap = |V_title ∩ C_title|`.
4. If `title_overlap == 0`, apply a penalty of **-10** to the raw
   score for that CCOG entry.

**Rationale**: Entries like "Air traffic management specialists" share
zero title content words with "Data Management Reporting Officer" once
generic words are removed. The penalty ensures these entries sink below
the selection threshold regardless of body-text verb matches.

### TF-IDF Weighting

Replace raw term overlap counts with TF-IDF weighted scores:

- **Term Frequency (TF)**: Computed against the JD full text.
  Terms appearing more frequently in the JD receive higher weight.
- **Inverse Document Frequency (IDF)**: Computed against the full
  CCOG corpus (~221 entries). Terms appearing in many CCOG entries
  (e.g., "apply", "plan", "provide", "services") receive LOW IDF.
  Terms appearing in few entries (e.g., "humanitarian", "data management",
  "reporting framework") receive HIGH IDF.
- **Score formula**: For each CCOG entry, `score = Σ(TF(term) × IDF(term))`
  for all terms shared between the JD and the CCOG entry description.

### Domain Gating Pre-Filter

Before scoring, reduce the candidate pool:

1. Extract the JD's functional domain keywords (top 10 by TF-IDF from
   the JD text).
2. For each CCOG entry, check whether its ISCO-08 major group or
   sub-major group description contains at least ONE of these domain
   keywords.
3. Entries with zero domain keyword overlap are EXCLUDED from scoring.
4. Expected reduction: from ~221 entries to ~30-50 candidates.

The domain gating pre-filter is applied BEFORE scoring and BEFORE the
title-mismatch penalty. Entries excluded by the pre-filter are logged
in `ccog_resolver_debug.json` with `status: "domain_gated_out"`.

---

## Section: Independent Evaluation Isolation Protocol

### Principle

Real UN application processes follow: document check → written test →
interview. This AI Agent focuses on the first layer: application
document generation. Independent evaluators MUST simulate what a real
screening panel sees.

### Permitted Inputs for E1/E2

| Input | Source | Required |
|-------|--------|----------|
| JD text | `## JOB_DESCRIPTION_TEXT` section of `inputs/application_context.md` ONLY | YES |
| Qualification questions | `## JOB_QUALIFICATION_QUESTIONS` section (when present, INSPIRA only) | CONDITIONAL |
| Canonical Option outputs | `option1_admin_profile.md`, `option2_cv.md`, `option3_cover_letter.md`, `option4_qualification_answers.md`, `option7_motivation_statement.md` | YES |

### Prohibited Inputs for E1/E2

The following MUST NOT be provided to evaluators:

- `## APPLICANT_PROFILE` or any other section of `inputs/application_context.md`
- `metric_ledger.md`
- `phase1_2_core_requirements.md`
- `phase1_7_strategy_report.md`
- `ccog_reference_resolved.md`
- `classification_proposal.md`
- Any `_discussion/` artifacts (advisor_notes, consensus, disagreement_log)
- Any D-server draft folders
- `user_feedback_updates.md`

### INSPIRA-Specific Evaluation Flow

When `## JOB_QUALIFICATION_QUESTIONS` is present in `application_context.md`:

1. E1/E2 first perform **knockout assessment**: do the Option documents
   demonstrate the minimum qualifications stated in the JD? If knockout
   criteria are not met, the evaluation terminates with a FAIL.
2. After knockout passes, E1/E2 assess **document-to-job fit**: how well
   do the application documents address each qualification question and
   JD requirement?
3. E1 (panel evaluator) produces a scored evaluation.
4. E2 (red-team) produces a risk review identifying weaknesses a real
   panel would flag.

### Session Isolation

- E1 and E2 MUST run in a **fresh session** with no shared context
  from the document generation phase.
- If running on a single-model platform (e.g., Codex), E1/E2 must be
  launched as separate tasks/sessions after the Phase 8 run completes.
- If running on a multi-model platform, E1/E2 SHOULD use a different
  LLM than the D-server writers for maximum independence.

---

## Section: Draft Diversity Gate (C2 Precondition)

### C2 Precondition: Draft Diversity Check

Before C2 IMPLEMENT pass 1, the following check MUST pass:

1. For each Option file (option1 through option7), compute pairwise
   character-level similarity between the three D-server versions:
   - `screening-lead/option<N>.md` vs `technical-lead/option<N>.md`
   - `screening-lead/option<N>.md` vs `ats-format-lead/option<N>.md`
   - `technical-lead/option<N>.md` vs `ats-format-lead/option<N>.md`

2. Similarity metric: Levenshtein ratio (normalized edit distance).
   `similarity = 1 - (edit_distance / max(len(a), len(b)))`

3. **Threshold**: 0.95 (95% character-level similarity).

4. **If any pair on any Option file exceeds the threshold**:
   - Flag as `DIVERSITY_FAILURE`.
   - Log the failing pairs with their similarity scores.
   - Surface to the user with the message:
     ```
     DIVERSITY_FAILURE: The following D-server draft pairs exceed 95%
     similarity, indicating insufficient ensemble independence:
     - <pair>: <score>
     Proceed with merge anyway? (y/n)
     If no, recommend re-running D-servers with heterogeneous LLMs
     or manual D-writer independence enforcement.
     ```
   - Do NOT proceed with C2 merge until the user responds.

### D-Writer Independence Scaffold

To prevent D2/D3 from copying D1's output:

1. **Preflight step** (runs before D1/D2/D3 IMPLEMENT):
   - Create empty draft target folders for each D-server writer.
   - Verify that no draft folder contains files from another writer.

2. **IMPLEMENT constraint**:
   - Each D-server writer reads ONLY from:
     - Frozen prep artifacts (read-only)
     - Canonical `phase1_7_strategy_report.md` (read-only)
   - Each D-server writer writes ONLY to its own `<agent>/` subfolder.
   - Cross-reading between D-server draft folders is PROHIBITED
     during IMPLEMENT.

3. **Enforcement**:
   - Before each D-server's IMPLEMENT phase begins, verify that the
     writer's draft folder is empty.
   - If the folder contains files, BLOCK IMPLEMENT and surface to user.

---

## Section: Run Manifest

### Schema

File: `_discussion/run_manifest.json`

```json
{
  "schema_version": "1.0",
  "job_slug": "<JOB_SLUG>",
  "target_system": "<INSPIRA | OTHER>",
  "run_start_utc": "<ISO 8601>",
  "run_end_utc": "<ISO 8601>",
  "max_revision_passes": "<int>",
  "jd_coverage_floor": "<float>",
  "servers_launched": [
    {
      "server": "<P0a | P0b | S1 | ... | C2 | E1 | E2 | R1 | R2>",
      "port": "<int>",
      "writer": "<agent_name>",
      "start_utc": "<ISO 8601>",
      "end_utc": "<ISO 8601>",
      "revision_passes_used": "<int>",
      "shutdown_reason": "<normal | error | user_abort>"
    }
  ],
  "skills_invoked": [
    {
      "skill": "<skill_name>",
      "server": "<server_id>",
      "artifact": "<output_file_path>",
      "invoked_utc": "<ISO 8601>"
    }
  ],
  "skills_not_invoked": [
    {
      "skill": "<skill_name>",
      "expected_server": "<server_id>",
      "reason": "<why_skipped>"
    }
  ],
  "unexpected_invocations": [
    {
      "skill": "<skill_name>",
      "server": "<server_id>",
      "reason": "<why_unexpected>"
    }
  ],
  "quality_gates": {
    "jd_coverage_score": "<float>",
    "diversity_check_passed": "<bool>",
    "advisor_quality_gate_passed": "<bool>",
    "all_pre_shutdown_checks_passed": "<bool>"
  }
}
```

### Lifecycle

1. **Initialized at P0a**: qa-auditor creates the manifest with
   `job_slug`, `target_system`, `run_start_utc`, and configuration
   parameters.
2. **Updated per server**: Before each server's shutdown, qa-auditor
   appends the server's entry to `servers_launched` and any
   `skills_invoked` entries.
3. **Finalized at R2**: qa-auditor fills `run_end_utc`,
   `skills_not_invoked`, `unexpected_invocations`, and `quality_gates`.
4. **Immutable after R2**: The manifest MUST NOT be modified after
   R2 shutdown.

---

## Section: E1/E2/R1/R2 Evaluation Server Topology

### Server Definitions

| Server | Port | Writer/Evaluator | Role | Inputs |
|--------|------|-------------------|------|--------|
| E1 | 9851 | independent-panel-evaluator | Scored evaluation of Option outputs | JD text + Option outputs ONLY |
| E2 | 9852 | independent-shortlisting-redteam | Risk review identifying panel-flaggable weaknesses | JD text + Option outputs ONLY |
| R1 | 9860 | screening-lead | Writes `panel_response.md` (accept / contest / defer each E1/E2 finding) | E1/E2 outputs + full context |
| R2 | 9861 | qa-auditor | Consolidates into `remediation_plan.md` + runs `apex-application-audit` (mandatory) | R1 output + E1/E2 outputs + full context |

### Sequencing

- E1 and E2 launch concurrently AFTER all canonical Option outputs exist.
- E1 and E2 MUST both complete before R1 launches.
- R1 MUST complete before R2 launches.
- R2 finalizes the run manifest.

### Session Isolation for E1/E2

See "Independent Evaluation Isolation Protocol" section above.
E1 and E2 run in fresh sessions. On single-model platforms, they are
launched as separate tasks. On multi-model platforms, they SHOULD use
a different LLM than the D-server writers.

---

## Section: Semantic Coverage Assessment

### Three-Layer JD Coverage

The single-metric `jd_coverage_score` (phrase presence) is RETAINED
as Layer 1 but is no longer sufficient alone. Three layers are now
required:

#### E2a: Phrase Coverage Floor (Existing)

- Deterministic check: each JD keyword/phrase is present in at least
  one Option document.
- Floor: `JD_COVERAGE_FLOOR` (default 0.80).
- Metric: binary term presence.
- This layer catches obvious keyword omissions.

#### E2b: Requirement-by-Requirement Coverage Matrix (New)

- For each JD requirement (extracted from `phase1_2_core_requirements.md`),
  assess coverage across ALL Option documents:

| JD Requirement | Option 1 | Option 2 | Option 3 | Option 4 | Option 7 | Strength |
|---------------|----------|----------|----------|----------|----------|----------|
| Req 1: ... | ✓ strong | ✓ brief | ✓ strong | ✓ direct | ○ absent | HIGH |
| Req 2: ... | ○ absent | ✓ brief | ✓ strong | ○ absent | ✓ brief | MEDIUM |
| ... | ... | ... | ... | ... | ... | ... |

- Strength ratings: HIGH (addressed with specific evidence in 3+ docs),
  MEDIUM (addressed in 1-2 docs), LOW (mentioned but not evidenced),
  GAP (absent from all docs).
- Any requirement rated GAP triggers a remediation flag.

#### E2c: Unsupported-Claim Scan (New)

- For each factual claim in the Option documents, verify that it has
  backing in either:
  - `metric_ledger.md` (for quantitative claims)
  - `inputs/application_context.md` (for biographical/qualification claims)
- Claims with no backing are flagged as `UNSUPPORTED`.
- Claims that contradict the metric_ledger are flagged as `CONTRADICTORY`.
- Claims in the metric_ledger's `DO_NOT_USE` list are flagged as
  `PROHIBITED`.

### Assessment Timing

- E2a runs at C2 (existing behavior).
- E2b runs at R2 as part of the consolidated quality assessment.
- E2c runs at R2 as part of `apex-application-audit`.
