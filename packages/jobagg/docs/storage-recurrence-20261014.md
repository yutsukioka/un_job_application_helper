# Publication-copy storage recurrence: 14 October 2026

## Observed failure and scope

The production implementation matched commit
`874b79156f420418cb31a08669ccd8cdb3e46218` across all 141 package Python files.
Its 15-minute cron remained enabled. After disk reclamation on 13 October,
55 runs published successfully; subsequent invocations failed the 20 GiB reserve
check before dispatch. Seven of the last nine successful runs deferred retention.
The runtime policy was 20 seconds, two groups, and two protected newest groups.

Across the 54 publications after the first recovered cycle finished at
13 October 09:24:49 Africa/Nairobi, local files and retention receipts measured:

| Material | Logical GiB |
| --- | ---: |
| Newly created generation files | 250.47 |
| Of which: rendered exports | 127.56 |
| Of which: previous-export rollback copies | 113.03 |
| Newly created projection snapshots | 36.29 |
| Retired generation export copies | 152.57 |
| Retired snapshots | 30.62 |
| Net generation/snapshot balance | +103.57 |
| New fetched captures, separately | 0.433 |

No new blob files were found during that interval. The derivative balance was
about 1.918 GiB per publication; it is not a physical free-space reconciliation
or a prediction that every future day has the same workload. Raw machine-local
captures, database contents and logs are not included in Git. These aggregates
are an incident observation, not a reproducible performance benchmark.

The earlier projection excluded baseline history from worker snapshots; it did
not eliminate full export rendering. Each affected database still generated
current/history JSON and CSV, plus sealed copies for publication and recovery.
Cleanup could remove substantial bytes and still fall behind creation. Once the
reserve failed, the configuration check prevented even a cleanup-only attempt.

## Change and regression contract

1. Use distinct-inode copy-on-write clones for sealed export copies where macOS
   supports them. Retain verified streaming fallback and all publication hashes,
   journals, durability and recovery checks. No mutable hardlinks are introduced.
2. Persist completed per-file retention validation across budget deferrals, bound
   to controls, manifest and current file identity. Recheck the whole group before
   intent; never delete from a partial proof. Permit reviewed bounded maintenance
   settings up to 120 seconds/eight groups; existing 20-second/two-group defaults
   remain unchanged.
3. Allow enabled retention below the reserve only under the shared owner and the
   existing mount/identity/maintenance/publication gates. Require a fresh hard
   reserve check before any worker. Preserve fetch holds and use only one
   maintenance budget per invocation.
4. Explicitly reseal the reviewed retry-compatibility declaration over the full
   package. Preserve the existing predecessor and recognize the deployed
   `874b79156f420418cb31a08669ccd8cdb3e46218` package fingerprint
   `985d89ddc9b99e0e6d869b8d9175adc69818b216f5b9261fcb04cbd3fc760f3b`.
   The storage-only changes must preserve blocked/dead-letter tasks, charged
   attempts and retry counters. Registry, robots, owner and semantic-input
   changes retain their existing independent checks; arbitrary later package
   changes still invalidate compatibility.

The combined branch also retains the WHO/IFAD captured-inventory verifier repair
from merged PR #136 (`33edfb9cae5cefa932a29b1b4cbe3ab4cb608a72`) unchanged.
Its 142-module package fingerprint
`7ec4f5b4d37797599eed3ae04af4c18992dd8a2d7e3c01db742e6d3a8c34362b`
is an additional exact retry predecessor for the storage-only delta from that
master revision. A fresh explicit compatibility seal covers the entire combined
package; neither parent branch's seal is reused. Every compatibility regression
is exercised against all three reviewed predecessors, including rejection of
later unreviewed changes. This declaration does not assert that PR #136 was
deployed or authorize deployment or hold release.

Synthetic fixtures cover copy isolation, native/fallback interruption recovery,
readback corruption, capacity/permission failures, deadline/resume and evidence
mutation, protected generations/checkpoints, owner contention and low-space
worker exclusion. Actual APFS tests skip explicitly when native cloning is not
supported. Single-file SHA state is not persisted; a largest-file validation
must fit the configured budget. Other-platform streaming has no clone savings.

## Activation and bounded validation

This PR does not deploy code, reseal a live workspace, change cron or reset task holds.
An activation must follow the existing exact implementation-binding procedure
and keep the previous coherent release/configuration available for rollback.
It must preserve worker/live databases, source evidence and unresolved journals.
At a clean publication boundary, record the reviewed module manifests and
configuration; pause dispatch and acquire the shared owner during activation.

Validate one ordinary bounded cycle first, retaining existing source/task/request
limits and disabled supplementary attachments. Check worker acceptance,
publication result/gate and live readback separately. Confirm clone isolation
with synthetic data on the target volume before relying on physical savings.
Then compare new logical and allocated bytes, actual free space, retention
progress/removals and eligible backlog across the next normal cycles; report the
observed denominator rather than inferring capacity from a single successful run.
If one largest file cannot validate within the current allowance, review a larger
bounded maintenance setting while preserving the worker/publication limits.

Stop activation validation on integrity mismatch, unexpected mutation, data loss,
unresolved publication or new access challenge. Recover through the existing
journal, not by replaying providers or erasing holds. A code rollback restores the
reviewed code/configuration/fingerprint without overwriting intervening live data.
