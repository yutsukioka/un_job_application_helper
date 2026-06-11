# Advisor Export Contract

## Purpose

Consensus servers must be able to read the advisory input that influenced each
author server. Stock `agent_sync` does not persist that information in a form
that later servers can safely reconstruct, so v1 requires an explicit export
step before author-server shutdown.

## Export Owner

The export owner must be one of:

- the server writer, or
- `qa-auditor`

Advisors never write the export file directly.

## Export Timing

Export must happen:

1. after the final TEST and DISCUSS notes are available
2. before the author server is shut down

If the server shuts down first, the advisory record is incomplete by design.

## Export Destinations

Use one file per author server:

- `_discussion/advisor_notes_S1.md`
- `_discussion/advisor_notes_S2.md`
- `_discussion/advisor_notes_S3.md`
- `_discussion/advisor_notes_D1.md`
- `_discussion/advisor_notes_D2.md`
- `_discussion/advisor_notes_D3.md`

Create the file even when no notes were received. In that case, the file must
explicitly state that no advisor input was captured.

## Export Sources

### DISCUSS

Source:

- `get-discussion`

Retention behavior:

- available only while the server is live
- in-memory only unless separately exported

### TEST

Sources:

- advisor `send` messages received by the writer or `qa-auditor`
- advisor `broadcast` messages received by the writer or `qa-auditor`

Retention behavior:

- no retrospective API once the messages are consumed and cleared
- must be buffered and written out while the server is live

## Normalization Format

Each exported file must follow this structure:

```text
# Advisor Notes Export: <SERVER_ID>

- Writer: <writer-name>
- Exporter: <writer-name or qa-auditor>
- Fold: <strategy | document>
- Round count: <n>
- Timestamp: <ISO-8601>

## TEST Phase Notes

### From <advisor-name>
- Channel: <send | broadcast>
- Lens: <technical | screening | ats-format>
- Note: <verbatim or normalized note text>

## DISCUSS Phase Notes

### From <advisor-name>
- Structured discuss:
  ISSUE=... | FILE=... | OWNER=... | NEXT=... | ACTION=... | BLOCKER=...

## Export Summary

- Adopted immediately in latest draft: <yes/no + short note>
- Deferred to consensus: <yes/no + short note>
- Missing information or export gaps: <none or note>
```

## Default Recommendation

The default v1 mechanism is explicit export to `_discussion/advisor_notes_*.md`.
Keeping author servers alive until consensus reads them is valid, but not the
default operator path because it makes server lifetime coordination harder.
