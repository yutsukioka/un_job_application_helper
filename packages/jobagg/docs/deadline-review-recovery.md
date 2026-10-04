# Deadline hold evidence and reviewed corrections

An identity-bound past-deadline review creates a `past_deadline` detail hold.
Its retained review, effective expired deadline and hold ID are recorded separately in
`remediation_deadline_holds`; task errors, receipts, claims, attempts and cooldown
are preserved. Source health reports the reason and release requirements.
A positive future deadline may release only the matching review's hold.
Missing or invalid marker authority never automatically releases a hold.
An undated refresh preserves a previously active review; an unknown deadline
does not establish a future extension. Active retained hold evidence remains
visible in source health when another protected task status takes precedence;
reporting does not clear that status or its accounting.

For a reviewed withdrawal, first correct the job classification through its
authorized evidence workflow. The queue repair does not alter job rows or
create/withdraw markers. Prepare an explicit review file:

```json
{
  "schema_version": 1,
  "kind": "reviewed_deadline_corrections",
  "corrections": [{
    "task_id": "<exact detail task>",
    "hold_id": "<retained active hold>",
    "source_id": "<source>",
    "external_id": "<vacancy>",
    "review_id": "<original classification review>",
    "authority": "user_requested_deadline_correction",
    "action": "withdraw_classification",
    "correction_id": "<distinct correction review>",
    "reviewed_at": "<ISO timestamp from the original review through now>",
    "reason": "<evidenced reason for withdrawal>"
  }]
}
```

Use `python -m jobagg.remediation_repair --workspace <workspace> --shared-lock
<owner> --deadline-corrections <review-file> --output <new-plan>` for a zero-write,
zero-network preview limited to those tasks. Review the plan and its SHA-256.
An authorized execution uses the existing `--execute --plan <plan>
--plan-sha256 <exact-sha> --output <new-receipt>` flow. It acquires the shared
owner, rechecks the review file, hold, job, task, listing capture and policy,
and journals the queue-only release atomically. Existing source/host admission
gates still apply. This command is not permission to activate or repair live
state. Holds lacking retained review evidence need separate evidenced
reconciliation; the tool will report and preserve them.
