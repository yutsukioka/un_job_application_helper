# ApexStrategist Multi-Agent V1 Specification

## Summary

Version 1 defines a multi-server staging architecture for ApexStrategist that
preserves stock `agent_sync` semantics while enabling parallel draft authoring.
The design remains isolated from the live `.agents/` repo and is intended to be
implemented later as a separate effort.

This spec is the only canonical v1 plan document in `multi-agent-version1/`.

## Locked Decisions

1. Use stock `agent_sync` unchanged. No runtime fork, no multi-writer-per-server
   extension, and no reimplementation of the coordination server in v1.
2. Use multi-server parallelism with exactly one file-writing implementer per
   server.
3. Keep canonical shared outputs at the existing flat paths under
   `output/generated_documents/history/<JOB_SLUG>/...`.
4. Write parallel draft artifacts to per-author subfolders:
   `screening-lead/`, `technical-lead/`, and `ats-format-lead/`.
5. Use resident `qa-auditor` on author servers as the canonical tester and
   default closer.
6. Use co-resident advisors on author servers, but only within the server's
   TEST and DISCUSS phases. IMPLEMENT remains writer-only.
7. Split prep into two sequential servers:
   - `P0a`: `screening-lead` writes `classification_proposal.md`,
     `phase1_2_core_requirements.md`, and `metric_ledger.md`, then the human
     confirmation gate occurs.
   - `P0b`: `technical-lead` writes `ccog_reference_resolved.md`.
8. Use `S1/S2/S3` for strategy drafts, `C1` for strategy consensus, `D1/D2/D3`
   for Phase 8 drafts, and `C2` for Phase 8 consensus.
9. Use `apex-user-feedback-revision` as the only pre-Phase-8 review gate.
   `independent-shortlisting-redteam` remains post-document only.
10. Scope the initial document-fold topology to:
   - `option1_admin_profile.md`
   - `option2_cv.md`
   - `option3_cover_letter.md`
   - optional `option4_qualification_answers.md`

## Default Server Topology

### Prep Servers

`P0a`
- Default port: `9800`
- Writer: `screening-lead`
- Canonical tester: `qa-auditor`
- Advisors: none required by default
- Writes:
  - `classification_proposal.md`
  - `phase1_2_core_requirements.md`
  - `metric_ledger.md`
- Shutdown preconditions:
  - human vacancy-type confirmation step completed or explicitly deferred by
    operator runbook

`P0b`
- Default port: `9801`
- Writer: `technical-lead`
- Canonical tester: `qa-auditor`
- Advisors: none required by default
- Writes:
  - `ccog_reference_resolved.md`
- Shutdown preconditions:
  - `P0a` artifacts exist
  - human confirmation from `P0a` has been recorded

### Strategy Author Servers

`S1`
- Default port: `9811`
- Writer: `screening-lead`
- Advisors: `technical-lead`, `ats-format-lead`
- Canonical tester: `qa-auditor`
- Writes:
  - `screening-lead/phase1_7_strategy_report.md`

`S2`
- Default port: `9812`
- Writer: `technical-lead`
- Advisors: `screening-lead`, `ats-format-lead`
- Canonical tester: `qa-auditor`
- Writes:
  - `technical-lead/phase1_7_strategy_report.md`

`S3`
- Default port: `9813`
- Writer: `ats-format-lead`
- Advisors: `screening-lead`, `technical-lead`
- Canonical tester: `qa-auditor`
- Writes:
  - `ats-format-lead/phase1_7_strategy_report.md`

### Strategy Consensus Server

`C1`
- Default port: `9820`
- Writer: `qa-auditor`
- Participants: active strategy-fold writers only
- Reads:
  - all active strategy draft files
  - exported advisor note files for active strategy servers
- Writes:
  - `phase1_7_strategy_report.md`
  - `_discussion/round2_consensus.md`
  - `_discussion/disagreement_log.md`
- Required output:
  - canonical strategy report includes an `## Open Questions` section when
    disagreements remain unresolved

### Phase 7.5 Gate

After `C1`, run `apex-user-feedback-revision` against the canonical
`phase1_7_strategy_report.md` before starting the document fold.

### Document Author Servers

`D1`
- Default port: `9831`
- Writer: `screening-lead`
- Advisors: `technical-lead`, `ats-format-lead`
- Canonical tester: `qa-auditor`
- Writes:
  - `screening-lead/option*.md`

`D2`
- Default port: `9832`
- Writer: `technical-lead`
- Advisors: `screening-lead`, `ats-format-lead`
- Canonical tester: `qa-auditor`
- Writes:
  - `technical-lead/option*.md`

`D3`
- Default port: `9833`
- Writer: `ats-format-lead`
- Advisors: `screening-lead`, `technical-lead`
- Canonical tester: `qa-auditor`
- Writes:
  - `ats-format-lead/option*.md`

### Document Consensus Server

`C2`
- Default port: `9840`
- Writer: `qa-auditor`
- Participants: active document-fold writers only
- Reads:
  - all active document draft files
  - exported advisor note files for active document servers
- Writes:
  - `option1_admin_profile.md`
  - `option2_cv.md`
  - `option3_cover_letter.md`
  - `option4_qualification_answers.md` when selected
  - `_discussion/round4_consensus.md`
  - `_discussion/disagreement_log.md`

### Post-Evaluation

Use the existing post-document evaluation flow only after at least one
candidate-facing document exists:

- `independent-panel-evaluator`
- `independent-shortlisting-redteam`

## Author-Server Phase Model

Each author server follows the same loop.

### IMPLEMENT

- Only the writer acts.
- Advisors and `qa-auditor` do not write files.
- `discuss` is not used in this phase.
- Writer produces or revises the server's draft artifact.

### TEST

- `qa-auditor` submits `TEST::PASS` or `TEST::FAIL`.
- Advisors may send review notes via `send` or `broadcast`.
- Advisors must not call `test-result`.
- TEST-phase advisor notes must be consumed while the server is still live;
  stock `agent_sync` does not provide retrospective retrieval once those
  messages are read and cleared.

### DISCUSS

- Each advisor submits exactly one structured discuss message for the round:
  `ISSUE=... | FILE=... | OWNER=... | NEXT=... | ACTION=... | BLOCKER=...`
- Advisor messages must be prefixed:
  `ADVISOR_TO=<writer-name>`
- `qa-auditor` is the default closer and may call
  `discuss-done --next-impl <writer>` if a bounded revision pass is needed.

### Optional Second Pass

- The same writer performs the revision.
- Revision count is bounded by `MAX_REVISION_PASSES`.
- After the final DISCUSS round, advisor notes are exported before the author
  server shuts down.

## Advisor Export Contract

The default persistence mechanism is explicit export before shutdown.

- Export owner:
  - writer, or
  - `qa-auditor`
- Advisors never write export files directly.
- Export destination:
  - `_discussion/advisor_notes_S1.md` ... `_discussion/advisor_notes_D3.md`
- Export timing:
  - after final TEST and DISCUSS notes are available
  - before the server is shut down
- Export sources:
  - DISCUSS notes from `get-discussion`
  - TEST-phase advisor notes that were actually received while the server was
    live

If an author server shuts down before export, the discuss history and consumed
TEST-phase advisor notes are considered lost for consensus purposes.

## Filesystem Contract

Canonical outputs remain at the current flat paths. Parallel drafts live in
per-author subfolders.

```text
output/generated_documents/history/<JOB_SLUG>/
├── classification_proposal.md
├── phase1_2_core_requirements.md
├── metric_ledger.md
├── ccog_reference_resolved.md
├── phase1_7_strategy_report.md
├── option1_admin_profile.md
├── option2_cv.md
├── option3_cover_letter.md
├── option4_qualification_answers.md
├── screening-lead/
│   ├── phase1_7_strategy_report.md
│   └── option*.md
├── technical-lead/
│   ├── phase1_7_strategy_report.md
│   └── option*.md
├── ats-format-lead/
│   ├── phase1_7_strategy_report.md
│   └── option*.md
└── _discussion/
    ├── round2_consensus.md
    ├── round4_consensus.md
    ├── advisor_notes_S1.md
    ├── advisor_notes_S2.md
    ├── advisor_notes_S3.md
    ├── advisor_notes_D1.md
    ├── advisor_notes_D2.md
    ├── advisor_notes_D3.md
    └── disagreement_log.md
```

## Run Mode Defaults

Expose these config keys in `inputs/application_context.md`:

```text
## RUN_MODE
ENSEMBLE_PHASE_1_7: [screening-lead, technical-lead, ats-format-lead]
ENSEMBLE_PHASE_8:   [screening-lead, technical-lead, ats-format-lead]
MAX_REVISION_PASSES: 1
```

Interpretation:

- `[]` means use the existing single-agent path outside the v1 topology.
- One selected writer means run one author server only. Skip the consensus
  server for that fold, and have that writer publish the final canonical
  flat-path output directly before shutdown.
- Two or three selected writers mean instantiate one author server per selected
  writer and one consensus server for that fold.
- Advisors for a fold are inferred from the other selected writers in that same
  fold.
- `qa-auditor` is always present where testing and closing are required.

## Prompt-Level Conventions Versus Runtime Enforcement

V1 may document fields such as:

- `allowed_messages`
- `forbidden_actions`
- `message_prefix_required`

These are prompt-level conventions only. Stock `agent_sync` does not enforce
them at the server. Mechanical enforcement is deferred to future validators
outside this spec.

## Deferred Items

The following are explicitly out of scope for v1 and are documented in
`specs/deferred-items.md`:

- candidate-specific persistent `lessons.md` memory across sessions
- promoting `deterministic-skill-router` to an orchestrator runtime gate
- any `agent_sync` fork or multi-writer-per-server design
- any direct backport or cutover into `.agents/`

## Acceptance Checks

V1 is considered complete when:

- this workspace contains one canonical spec and the planned supporting docs
- the canonical spec includes `P0a`, `P0b`, `S1`, `S2`, `S3`, `C1`, `D1`,
  `D2`, `D3`, `C2`, and post-eval
- the author-server phase model matches stock `agent_sync`
- advisor export before shutdown is explicitly defined
- `apex-user-feedback-revision` is the pre-Phase-8 gate
- deferred policy-conflict items are explicitly parked
