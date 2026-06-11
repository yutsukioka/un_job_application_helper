# Phase Rules

This document records the runtime-correct role behavior for v1.

## Source of Truth

These rules must remain compatible with stock `agent_sync` semantics and the
current Apex protocol:

- one implementer writes per server
- `qa-auditor` is the canonical default `test-result` sender
- `discuss` is a DISCUSS-phase action

## IMPLEMENT

Writer behavior:

- may edit only the server's owned draft or canonical output files
- may not edit another writer's draft subtree
- may not edit canonical flat-path outputs from author servers

Advisor behavior:

- no file writes
- no `discuss`
- no `test-result`

`qa-auditor` behavior:

- no file writes on author servers
- no `test-result` until TEST phase

## TEST

`qa-auditor` behavior:

- reads the writer's current draft
- reads any advisor review notes that arrive
- submits `TEST::PASS` or `TEST::FAIL`

Advisor behavior:

- may send review notes via `send` or `broadcast`
- must not call `test-result`
- must keep notes within their primary lens

Writer behavior:

- receives TEST-phase review notes
- does not close the phase directly

Important runtime limitation:

- TEST-phase `send` and `broadcast` traffic is not available later through a
  retrospective server API. Once consumed and cleared, it must be exported by
  the writer or `qa-auditor` if consensus needs to read it later.

## DISCUSS

Advisor behavior:

- each advisor submits exactly one structured discuss message for the round
- required body shape:
  `ISSUE=... | FILE=... | OWNER=... | NEXT=... | ACTION=... | BLOCKER=...`
- required prefix:
  `ADVISOR_TO=<writer-name>`

`qa-auditor` behavior:

- default closer
- may call `discuss-done --next-impl <writer>` if a bounded revision pass is
  needed

Writer behavior:

- reviews the structured discuss notes
- performs the next IMPLEMENT pass only if one is assigned

## Prompt-Level Conventions

The following are documented conventions, not runtime-enforced server fields:

- `allowed_messages`
- `forbidden_actions`
- `message_prefix_required`

These may appear in future prompt YAML or validator configs, but stock
`agent_sync` does not enforce them itself.

## Consensus Servers

`C1` and `C2` differ from author servers:

- writer is always `qa-auditor`
- there are no draft-subtree writes
- active writers from the corresponding fold participate as reviewers
- canonical flat-path outputs are written only from the consensus server or in
  single-writer mode when consensus is skipped
