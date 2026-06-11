# Operational runbook — multi-agent v2

This is the per-vacancy operational sequence. Authority for the
underlying mechanics: [../spec/00_consolidated_plan.md](../spec/00_consolidated_plan.md) and
[../spec/01_tier_a_ensemble_workflow.md](../spec/01_tier_a_ensemble_workflow.md). Server identifiers
match [server_manifest.yaml](server_manifest.yaml).

## 0. Preconditions

1. `inputs/application_context.md` exists with at minimum:
   - `## RUN_MODE` (may be empty lists for single-agent mode)
   - `## BUDGETS`
   - `## LIMITS` (TARGET_SYSTEM)
2. Vacancy-specific output directory exists:
   `output/generated_documents/history/<position>/`
3. `tmp/agent_sync/` is writable.

If `RUN_MODE.ENSEMBLE_PHASE_1_7` and `RUN_MODE.ENSEMBLE_PHASE_8` are
both `[]`, **stop here** and use the existing single-agent linear
pipeline. The remainder of this runbook is for ensemble mode.

Ensemble v2 document generation supports Phase 8 Options 1-4 and Option 7.
Options 5, 6, and 8 remain v1 single-agent fallback paths unless explicitly
enabled in a later v2 expansion.

## 1. Prep stage

### 1a. Launch P0a

- Port: 9800
- Writer: `screening-lead`
- Advisors: `technical-lead`, `ats-format-lead`
- Canonical tester: `qa-auditor`
- Writer produces:
  - `classification_proposal.md`
  - `phase1_2_core_requirements.md`
  - `metric_ledger.md`
- Per-server loop: IMPLEMENT → TEST → DISCUSS, bounded by
  `MAX_REVISION_PASSES`.
- Live persistence: writer or qa-auditor consumes advisor `listen`
  traffic and appends to `_discussion/advisor_notes_P0a.md`.
- Initialize `<OUTDIR>/_discussion/run_manifest.json` with
  `.agents/scripts/write_run_manifest.py init`.
- Pre-shutdown check: `advisor_notes_P0a.md` non-empty.
- Shut down P0a.

### 1b. Human confirmation gate

- Surface `classification_proposal.md` to the user.
- User confirms or amends the vacancy-type classification.
- DO NOT proceed to P0b until user confirms.

### 1c. Launch P0b

- Port: 9801
- Writer: `technical-lead`
- Advisors: `screening-lead`, `ats-format-lead`
- Canonical tester: `qa-auditor`
- Writer produces: `ccog_reference_resolved.md`
- Same per-server loop, persistence, pre-shutdown check.
- Shut down P0b.

### 1d. Freeze prep artifacts

The four prep artifacts are now read-only for all subsequent rounds:

- `classification_proposal.md`
- `phase1_2_core_requirements.md`
- `metric_ledger.md`
- `ccog_reference_resolved.md`

The metric ledger in particular is **frozen** (see Tier E1).

## 2. Strategy fold

### 2a. Server Topology and Ports

| Server | Port | Writer          | Advisors                              | Tester     |
|--------|------|-----------------|---------------------------------------|------------|
| P0a    | 9800 | screening-lead  | technical-lead, ats-format-lead       | qa-auditor |
| P0b    | 9801 | technical-lead  | screening-lead, ats-format-lead       | qa-auditor |
| S1     | 9811 | screening-lead  | technical-lead, ats-format-lead       | qa-auditor |
| S2     | 9812 | technical-lead  | screening-lead, ats-format-lead       | qa-auditor |
| S3     | 9813 | ats-format-lead | screening-lead, technical-lead        | qa-auditor |
| C1     | 9820 | qa-auditor      | screening-lead, technical-lead, ats-format-lead | (writer is qa) |
| D1     | 9831 | screening-lead  | technical-lead, ats-format-lead       | qa-auditor |
| D2     | 9832 | technical-lead  | screening-lead, ats-format-lead       | qa-auditor |
| D3     | 9833 | ats-format-lead | screening-lead, technical-lead        | qa-auditor |
| C2     | 9840 | qa-auditor      | screening-lead, technical-lead, ats-format-lead | (writer is qa) |
| **E1** | **9851** | **independent-panel-evaluator** | **(none — isolated session)** | **(self-evaluating)** |
| **E2** | **9852** | **independent-shortlisting-redteam** | **(none — isolated session)** | **(self-evaluating)** |
| **R1** | **9860** | **screening-lead** | **technical-lead, ats-format-lead** | **qa-auditor** |
| **R2** | **9861** | **qa-auditor** | **screening-lead, technical-lead, ats-format-lead** | **(writer is qa)** |

**Notes on E1/E2**:
- E1 and E2 do NOT follow the standard IMPLEMENT/TEST/DISCUSS loop.
- E1 and E2 run in isolated sessions with restricted inputs (see
  `independent_evaluation_protocol.md`).
- E1 and E2 produce evaluation artifacts and shut down.
- E1 and E2 launch concurrently AFTER all canonical Option outputs exist.

Concurrency: same agent name may be advisor on multiple servers
simultaneously, but writer on at most one (this constraint is satisfied
by the table above).

Per server, the loop is:

```text
JOIN
  -> writer joins first
  -> advisors and qa-auditor join only after status shows implementer=<writer>

IMPLEMENT
  -> writer drafts <agent>/phase1_7_strategy_report.md
  -> writer calls `impl-done <writer> --summary "<short>"` (advances IMPLEMENT -> TEST)

TEST
  -> advisors broadcast, or send the same note to writer and qa-auditor
  -> writer or qa-auditor calls listen (DESTRUCTIVE)
  -> writer or qa-auditor appends to _discussion/advisor_notes_<server>.md
       (THIS IS THE ONLY CHANCE — messages are gone after listen)
  -> qa-auditor calls test-result -> advances to DISCUSS

DISCUSS
  -> each advisor + qa-auditor: one structured discuss
  -> writer or qa-auditor calls get-discussion, appends to advisor_notes
  -> non-qa agents: discuss-done (no --next-impl)
  -> qa-auditor waits until non-qa agents are done
  -> qa-auditor: discuss-done --next-impl <writer> (loop) or discuss-done
     without --next-impl, followed by the separate shutdown command

  Loop bounded by MAX_REVISION_PASSES. The writer reads advisor_notes
  at the start of each subsequent IMPLEMENT pass.
```

### 2b. Pre-shutdown verification (per author server)

Before shutting down S1, S2, or S3:

- [ ] `_discussion/advisor_notes_<server>.md` exists and is non-empty.
- [ ] `_discussion/advisor_notes_<server>.md` contains at least one
  substantive technical observation, issue, or suggestion per advisor.
  Validate with:
  `python .agents/scripts/validate_advisor_notes.py --advisor-notes <OUTDIR>/_discussion/advisor_notes_<server>.md --advisors "<advisor1>,<advisor2>" --json`
- [ ] Writer's draft `<agent>/phase1_7_strategy_report.md` exists.
- [ ] No `test-result PASS` was sent by anyone other than `qa-auditor`.

If any check fails, do not shut down — re-open the round.

### 2c. Shut down S1, S2, S3

All three must be shut down before C1 launches.

### 2d. Launch C1

- Port: 9820
- Writer: `qa-auditor`
- Advisors: `screening-lead`, `technical-lead`, `ats-format-lead`
- Canonical tester: writer is `qa-auditor`; no separate tester needed.
- qa-auditor reads:
  - all three `<agent>/phase1_7_strategy_report.md` drafts
  - all three `_discussion/advisor_notes_S*.md` files
- Merges per the per-section default leads
  ([../templates/per_section_default_leads.md](../templates/per_section_default_leads.md))
- Writes:
  - `phase1_7_strategy_report.md` (canonical, flat path)
  - `_discussion/round2_consensus.md`
  - `_discussion/disagreement_log.md` (append)
- Shut down C1.

## 3. Pre-Phase-8 gap review (Phase 7.5)

Run `apex-user-feedback-revision` on the canonical
`phase1_7_strategy_report.md`. Surface gaps, mitigation strategies, and
the "Metrics & Specifics Needed" list to the user.

Record the Phase 7.5 invocation in `_discussion/run_manifest.json`:
`python .agents/scripts/write_run_manifest.py add-skill --outdir <OUTDIR> --skill apex-user-feedback-revision --server Phase7.5 --artifact inputs/user_feedback_updates.md`

This is the **sole** pre-Phase-8 gate. v1's "A3 pre-generation red-team
pass" has been deleted in v2 (see [../CHANGELOG.md](../CHANGELOG.md) Edit 7).

If the user provides feedback under explicit apply intent, the
orchestrator may regenerate selected Phase 8 documents using the
controlled integration patch produced by `apex-user-feedback-revision`.

## 4. Document fold

### 4a. Launch D1, D2, D3 concurrently

Same per-server loop pattern as 2a, with:

| Server | Port | Writer | Advisors | Canonical tester |
|---|---|---|---|---|
| D1 | 9831 | screening-lead | technical-lead, ats-format-lead | qa-auditor |
| D2 | 9832 | technical-lead | screening-lead, ats-format-lead | qa-auditor |
| D3 | 9833 | ats-format-lead | screening-lead, technical-lead | qa-auditor |

Each writer produces `<agent>/option*.md` for the requested options.

Before IMPLEMENT pass 1, each D writer runs:
`python .agents/scripts/prepare_d_writer_scaffold.py --outdir <OUTDIR> --role <agent>`

This creates empty draft targets and fails if the writer's draft folder already
contains non-empty option files from a copied or pre-populated draft.

### 4b. Pre-shutdown verification (per author server)

Same as 2b, with `D*` substituted.

### 4c. Shut down D1, D2, D3

All three must be shut down before C2 launches.

### 4d. Launch C2

- Port: 9840
- Writer: `qa-auditor`
- Advisors: `screening-lead`, `technical-lead`, `ats-format-lead`
- Reads three draft folders + three `advisor_notes_D*.md`.
- Before merging, run the draft diversity gate:
  `python .agents/scripts/check_draft_diversity.py --outdir <OUTDIR> --option option1_admin_profile.md --threshold 0.95 --json`
  If any pair exceeds 95% character-level similarity, stop with
  `DIVERSITY_FAILURE` and surface the issue to the user before merging.
- Writes canonical flat-path `option*.md` files plus
  `_discussion/round4_consensus.md` and append to
  `_discussion/disagreement_log.md`.
- E2a phrase coverage floor enforced here (see Tier E2). R2 later performs
  E2b requirement-by-requirement coverage and E2c unsupported-claim scan.
- Shut down C2.

## 5. Post-eval

### 5a. Launch E1 / E2

Run `independent-panel-evaluator` and
`independent-shortlisting-redteam` on the canonical Option outputs.
Topology unchanged from v1.

Before E1/E2 join, create a sanitized benchmark:
`python .agents/scripts/prepare_independent_eval_input.py --context-pack inputs/application_context.md --output-file <OUTDIR>/_discussion/independent_eval_input.md`

E1/E2 may read only that sanitized benchmark plus canonical Option outputs.
They must not read full `inputs/application_context.md`, `metric_ledger.md`,
strategy reports, advisor notes, panel responses, or remediation files.

### 5b. Author response round (A3)

`screening-lead` writes `panel_response.md` accepting / contesting /
deferring each finding. `qa-auditor` consolidates accepted fixes into
`remediation_plan.md`.

### 5c. Mandatory `apex-application-audit` (D4)

Run audit. Findings merge into `panel_response.md`.
R2 writes `application_audit.md`, `remediation_plan.md`, and finalizes
`_discussion/run_manifest.json`.

### 5d. Optional revision pass

If `panel_response.md` requires fixes that exceed cosmetic edits,
re-launch the relevant D* server(s) with the remediation plan as
additional input.

## 6. Persist learnings (F1)

After all rounds complete, append findings to the per-vacancy-family
`lessons.md` in `memories/repo/`.

## 7. Cleanup

- Optional: run [../scripts/check_scope.py](../scripts/check_scope.py) to verify no
  agent wrote outside its declared scope.
- Archive `tmp/agent_sync/<server>/` directories to a run-specific
  archive folder if desired.
