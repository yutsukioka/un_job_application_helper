# Tier A — Two-fold ensemble authoring (deep dive)

> Companion to [00_consolidated_plan.md §"Tier A"](00_consolidated_plan.md). This file
> focuses on the operational mechanics: server lifecycle, RUN_MODE
> resolution, the panel-response round, and the bounded critic-author loop.

## A1. Server topology — full lifecycle

See [00_consolidated_plan.md](00_consolidated_plan.md) for the canonical 10-server table.
This file documents only the lifecycle aspects that don't fit cleanly in
the canonical plan.

### Authoritative agent-on-server matrix

| Server | Writer | Advisor 1 | Advisor 2 | Canonical tester | Notes |
|---|---|---|---|---|---|
| P0a | screening-lead | technical-lead | ats-format-lead | qa-auditor | 3 prep artifacts |
| P0b | technical-lead | screening-lead | ats-format-lead | qa-auditor | CCOG only |
| S1 | screening-lead | technical-lead | ats-format-lead | qa-auditor | strategy lens |
| S2 | technical-lead | screening-lead | ats-format-lead | qa-auditor | strategy lens |
| S3 | ats-format-lead | screening-lead | technical-lead | qa-auditor | strategy lens |
| C1 | qa-auditor | screening-lead | technical-lead | (writer is qa-auditor) | consensus |
| D1 | screening-lead | technical-lead | ats-format-lead | qa-auditor | document lens |
| D2 | technical-lead | screening-lead | ats-format-lead | qa-auditor | document lens |
| D3 | ats-format-lead | screening-lead | technical-lead | qa-auditor | document lens |
| C2 | qa-auditor | screening-lead | technical-lead, ats-format-lead | (writer is qa-auditor) | consensus |

`qa-auditor` is co-resident on every author server (P0a, P0b, S1, S2, S3,
D1, D2, D3) as **canonical tester** — i.e., the agent that calls
`test-result` to advance TEST → DISCUSS, and `discuss-done --next-impl`
to close the round.

### Per-server phase loop (mandatory)

```text
[ IMPLEMENT ]
  WRITER writes to its draft subfolder.
  Advisors / qa-auditor: silent.
  WRITER calls go-test when ready.

[ TEST ]
  Advisors call send / broadcast (TEST messages).
  WRITER or qa-auditor calls listen (DESTRUCTIVE — no retro API).
  WRITER or qa-auditor appends consumed messages to
    _discussion/advisor_notes_<server>.md
    (this is the only chance — messages are gone after listen).
  qa-auditor calls test-result -> advances TEST -> DISCUSS.

[ DISCUSS ]
  Each advisor + qa-auditor: exactly one structured discuss.
  WRITER or qa-auditor calls get-discussion, appends to
    _discussion/advisor_notes_<server>.md.
  Non-qa agents: discuss-done WITHOUT --next-impl.
  qa-auditor: discuss-done --next-impl <writer-name> to loop, OR
              discuss-done --next-impl <shutdown-marker> to end.

[ Loop bounded by MAX_REVISION_PASSES ]
  Default 1. Cross-pollination reaches the writer in the NEXT IMPLEMENT
  pass via the advisor_notes file (writer reads it at start).
```

### Lifecycle gate before shutdown

Before issuing SHUTDOWN to any author server:

1. Verify `_discussion/advisor_notes_<server>.md` exists and is
   non-empty.
2. Verify the writer's draft file exists in its draft subfolder.
3. Verify no `test-result PASS` was sent by anyone other than
   `qa-auditor`.

If any check fails, re-open the round (loop back to TEST or IMPLEMENT)
or escalate to the user.

### Concurrency rules

- The same agent name may be **advisor on multiple concurrent servers**
  because advisors do not write files.
- The same agent name may be **writer on at most one live server at a
  time**.
- Strategy fold (S1/S2/S3) and document fold (D1/D2/D3) must NOT run
  concurrently with their respective consensus servers (C1, C2).
- Author-fold servers must be shut down before launching the consensus
  server.

## A2. RUN_MODE resolution

```
## RUN_MODE
ENSEMBLE_PHASE_1_7: [screening-lead, technical-lead, ats-format-lead]
ENSEMBLE_PHASE_8:   [screening-lead, technical-lead, ats-format-lead]
MAX_REVISION_PASSES: 1
```

Resolution rules:

| ENSEMBLE_PHASE_1_7 | Servers launched for strategy fold |
|---|---|
| `[]` | single-agent linear mode (current default; no S1/S2/S3, no C1) |
| `[screening-lead]` | S1 only; C1 trivially passes through |
| `[screening-lead, technical-lead]` | S1 + S2 + C1; ats-format-lead participates as advisor on each |
| `[screening-lead, technical-lead, ats-format-lead]` | full S1/S2/S3 + C1 |

Same logic for `ENSEMBLE_PHASE_8` controlling D1/D2/D3 + C2.

`qa-auditor` is **always** co-resident on author servers as canonical
tester, regardless of RUN_MODE — there is no mode in which an author
server runs without a canonical tester.

## A3. Author response round to independent evaluation (Round 5.5)

> v1 had an A3 "Pre-generation red-team pass" that has been **deleted**.
> The renumbering: v1 A4 → v2 A3 (this section); v1 A5 → v2 A4.

After E1/E2 (Round 5):

1. `screening-lead` writes `panel_response.md` per finding:
   `ACCEPT` (with planned fix), `CONTEST` (with evidence citation), or
   `DEFER` (with reason).
2. `qa-auditor` produces `remediation_plan.md` consolidating the accepted
   fixes.
3. The remediation plan is the input to any post-eval document revision
   pass.

## A4. Bounded critic-author revision per generator

> v1 A5 → v2 A4.

Inside each author server's per-server loop:

```
pass 1: IMPLEMENT (draft) -> TEST (qa-auditor FAIL with line issues) -> DISCUSS
pass 2: IMPLEMENT (writer revises same draft) -> TEST -> DISCUSS
                                                  ^
                                                  qa-auditor must PASS or
                                                  the round ends with
                                                  unresolved issues
                                                  flagged for consensus.
```

`MAX_REVISION_PASSES = 1` means at most one revision after the initial
draft (i.e., 2 IMPLEMENT phases total). Default = 1.
