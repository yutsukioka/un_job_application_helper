# Server Topology

This document defines the default v1 server map and its role assignments.

## Default Ports

| Server | Port | Writer | Advisors | Canonical Tester | Main Output |
|---|---:|---|---|---|---|
| `P0a` | 9800 | `screening-lead` | none by default | `qa-auditor` | `classification_proposal.md`, `phase1_2_core_requirements.md`, `metric_ledger.md` |
| `P0b` | 9801 | `technical-lead` | none by default | `qa-auditor` | `ccog_reference_resolved.md` |
| `S1` | 9811 | `screening-lead` | `technical-lead`, `ats-format-lead` | `qa-auditor` | `screening-lead/phase1_7_strategy_report.md` |
| `S2` | 9812 | `technical-lead` | `screening-lead`, `ats-format-lead` | `qa-auditor` | `technical-lead/phase1_7_strategy_report.md` |
| `S3` | 9813 | `ats-format-lead` | `screening-lead`, `technical-lead` | `qa-auditor` | `ats-format-lead/phase1_7_strategy_report.md` |
| `C1` | 9820 | `qa-auditor` | active strategy writers only | n/a | `phase1_7_strategy_report.md` |
| `D1` | 9831 | `screening-lead` | `technical-lead`, `ats-format-lead` | `qa-auditor` | `screening-lead/option*.md` |
| `D2` | 9832 | `technical-lead` | `screening-lead`, `ats-format-lead` | `qa-auditor` | `technical-lead/option*.md` |
| `D3` | 9833 | `ats-format-lead` | `screening-lead`, `technical-lead` | `qa-auditor` | `ats-format-lead/option*.md` |
| `C2` | 9840 | `qa-auditor` | active document writers only | n/a | `option1_admin_profile.md`, `option2_cv.md`, `option3_cover_letter.md`, optional `option4_qualification_answers.md` |
| `E1/E2` | runtime-defined | independent evaluators | none | n/a | post-document evaluation outputs |

## Active-Set Rules

The topology is parameterized by `## RUN_MODE`.

### Strategy Fold

- Active strategy writers = `ENSEMBLE_PHASE_1_7`
- Instantiate:
  - `0` selected writers: fall back to the existing single-agent path
  - `1` selected writer: one author server only, no `C1`; the writer publishes
    the final canonical strategy output directly before shutdown
  - `2+` selected writers: one author server per writer plus `C1`

### Document Fold

- Active document writers = `ENSEMBLE_PHASE_8`
- Instantiate:
  - `0` selected writers: fall back to the existing single-agent path
  - `1` selected writer: one author server only, no `C2`; the writer publishes
    the final canonical document output set directly before shutdown
  - `2+` selected writers: one author server per writer plus `C2`

## Shared Reads

All author and consensus servers read from the canonical prep artifacts:

- `classification_proposal.md`
- `phase1_2_core_requirements.md`
- `metric_ledger.md`
- `ccog_reference_resolved.md`

Document-fold servers additionally read:

- canonical `phase1_7_strategy_report.md`
- `_discussion/round2_consensus.md`
- output from `apex-user-feedback-revision`

## Shutdown Preconditions

### Author Servers

Before shutdown, an author server must satisfy all of:

- final TEST/DISCUSS cycle complete
- no additional revision pass is authorized
- advisor notes exported by the writer or `qa-auditor`

### Consensus Servers

Before shutdown, a consensus server must satisfy all of:

- all expected active draft inputs are present
- all expected exported advisor note files are present
- canonical outputs for that fold are written

## Operational Notes

- Launch each server from its own directory under `tmp/agent_sync/`.
- Do not run the same agent name as writer on multiple live servers.
- An agent name may appear on multiple live servers as advisor, but notifier
  usage must be managed carefully because stock notifier files are keyed by
  agent name rather than port.
