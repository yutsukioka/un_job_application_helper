# PR #143 corrected M1: isolated activation and reboot renewal

Observed 23 October 2026, Africa/Nairobi. PR #143 is the corrected M1 candidate;
its original revert was reversed without rewriting branch history, and the
previous bootstrap follow-up was merged. The candidate remains a draft.
**Production is fenced and inactive; deployment readiness remains false.**

The later [historical resolution increment](pr143-historical-resolution-2026-10-23.md)
updates the historical counts, package seal and backup-device bindings below.
The following sections preserve the earlier observation rather than certify the
later candidate.

## Committed candidate and preservation

Runtime/preparation changes are commit `b3171bc9935e7b6a99ba9e66581bb3e8ba4ce0b7`.
Its independent read-only source archive is
`/Users/yutsukioka2/git/un_job_application_helper-releases/pr143-b3171bc9-1a9f53634aa9`:
1,948 tracked source files, complete source manifest SHA-256
`3ba2eaceeedd833f620b588994d5205fdbdb9e8c94316fc7f8e9f16a5ab2e13f`.
It references the original pinned PR #142 environment/browser manifests. Startup
imports and the complete worker seal were checked from that immutable source.
The original PR #142 and earlier follow-up archives are retained.

The seal covers all **169 package modules**, without exclusions:

- Implementation: `1a9f53634aa9645505b31e8e21016589004f19bfb5b07688d47e30a4ff0f168b`.
- Reviewed successor scope: `140d2affb11c76e5b9318330f45750aa702bc45d0044c788bdf571fc944fdf1b`.

M0 baseline JSON and Markdown hashes remain respectively
`19e40338f0ab1400f69c262636f0ddd6a87430ce6ffc56d513453b963f06d952`
and `a99306f6738792a03b8a253b1644136d70dc7e96a6a4e5a6bc22923126f2a7b5`.
The primary checkout's unrelated changes were not staged. No live selector,
configuration, cron entry, deployed M0 worker or production payload was changed.

## Runtime corrections

Explicit activation can place its restore head in an existing, operator-owned
external directory disjoint from the worker database family. That directory's
identity is pinned; aliases, unsafe permissions, replacement, and an unreviewed
handoff fail. Defaults remain compatible. Existing files and dangling symlinks
cannot be overwritten, including during initial creation. Unsafe roots fail
before authority schema creation. Owner-loss and stale SQLite restore checks
remain effective with either head location.

An explicit owner-held inactive preparation API can add the existing listing
and detail freshness transitions to a previously prepared dirty schema. It
preserves dirty versions, pending units, acknowledgements and retained data;
it cannot change an existing freshness policy or run after source activation.
Historical review-hold tables are excluded from publication-data triggers,
including when the catalogue is prepared before the dirty schema.

The earlier Linux Python CI failure was a real SQLite/export recovery test
exceeding an unrelated three-second fixture deadline. The functional fixture now
uses finite larger test allowances; generation counts, original-proof retention,
escrow recovery and true-empty assertions remain. Production deadlines are
unchanged. Independent deadline/interruption tests remain in the package suite.

## Historical review

The bounded review rechecked **2,182 original acceptance-report hashes** and
**248 release/review metadata files**, reading **583,766,935 SSD bytes** under a
1 GiB / 120-second ceiling. It observed 39 implementation hashes; 23 had matching
metadata references. These references do not reproduce complete historical
module bytes or supply missing task intervals. No new implementation was
approved. The original 51 reports lacking task intervals remain accounted for.

The scoped catalogue still has 6,471 control records: **6 terminal verified,
30 publication reconciliation required, 3 accepted pending, 6,245 repair required,
and 187 uncertain worker claims**. Thus **6,465 remain unresolved**. These are
control records, not counts of distinct eligible tasks or vacancies. Auxiliary
content-unverified records, old-owner relocations and the six reproduced recovery
binding contradictions remain held. The original census is not an independently
approved all-writer or chronological production admission inventory.

## Reboot remediation on the copy

The user confirmed a reboot. The SSD's persistent volume UUID matches the existing
storage sentinel, whose bytes are pinned by the unchanged original configuration.
The observed device number changed from **16777257 to 16777252**; owner inode
**4728** stayed the same. Metadata inspection found only device-field changes
for all 50 destination bindings, 200 export identities, six immutable policy
controls, policy database/owner/directory bindings and 42,152 historical file
references. Another 7,915 references were unchanged.

`m1_reboot_reattest.py` implements an explicit **copy-only** renewal. It verifies
the UUID against that independently pinned sentinel, requires the actual owner
and unchanged maintenance marker, rejects production aliases/hard links, and
admits only the exact old-device transition. Other identity/content changes stop.
It verifies every original producer export hash and every originally hashed
historical control. Originally unverified content stays unverified and held.

The immutable renewal plan retains original receipts and identities. Every
bounded destination update is journaled and can resume that same plan; the
preparation grant can update only the native owner-device column. Store IDs,
epochs, publication revisions and domain rows cannot be rewritten by that grant.
Policy renewal changes physical bindings in its external head; the policy SQLite
database stays byte-for-byte unchanged. Historical dependency identities receive
the reviewed device transition without changing their hashes or dispositions.
No quota, attempt, hold, transaction or source history is reset.

The completed pass verified **4,697,534,223 SSD bytes**, including **200 exports
and 22,385 hashed controls**, under a 16 GiB / 300-second verification ceiling
and a 330-second outer supervisor. A final report-format tuple/list comparison
stopped the initial operation after binding changes; the corrected check resumed
the preserved plan, without restoring a database. All historical dispositions
were unchanged. The renewed complete export certificate is
`6f0f3a6e49bb9651c25055535baa0d9b65a0b2d6aab881126bad92cb2a1b3954`;
the original certificate remains archived in its original bootstrap receipt.
The copied policy revision remains zero and its database SHA-256 remains
`2d131d3366d849afd3c4b7efed24a26d1735b69bb7613f2c5d53eb09e7b9a4c7`.

This tool deliberately refuses an already activated copied source. Ordinary
runtime device/owner guards are unchanged: another reboot still requires an
explicit reviewed handoff. Automatic persistent-UUID support across all active
source/policy/publication authorities is a separate runtime change; this renewal
is not permission to ignore device mismatches or automatically approve restores.

## Rehearsal, measurements and remaining gates

All mutable work remains under the existing isolated snapshot. The OS sandbox
denies network and production writes. No HDD payload was read or copied in this
increment; the earlier 469 in-place HDD hash matches remain historical evidence.

A separate one-detail fixture used a real previously accepted SSD detail and two
fresh isolated destination stores. Its selected projection took **0.041 seconds**;
publication took **0.569 seconds**, changed one job and verified eight exports.
Native source activation succeeded with a staging-only external restore head,
retaining its pending unit. This fixture excludes production history and scale.
Its final worker file is 446,464 bytes after native schema preparation; that is
not a measurement of the initial projection's byte size.

On the complete copy, freshness preparation retained 23,438 jobs, 9,500 tasks,
67,017 attempts, 7,265 observations, all 7,314 dirty units and the original 7,336
dirty versions. It added 7,314 freshness transitions. Dirty-contract revalidation
retains the old versions and creates explicit new versions for all active units.
The source authority is activated only on the copy, using the immutable candidate,
its actual runtime/dependency/configuration contract and the renewed full export
boundary. Its external restore head remains under staging. No provider worker or
publisher child is dispatched by this native bootstrap operation.

The first one-time full-copy integrity scan reached its 180-second verification
limit after native activation had committed. This was local verification expiry,
not provider failure or permission to discard the head/queue. The resumed
integrity/maintenance checks use a separate 600-second migration ceiling and
630-second outer supervisor. The scan reached the subsequent configuration
validation after its `ok` and boundary assertions; the failed configuration-stage
log and harness hash are retained. No standalone integrity completion receipt was
flushed before that later error, and the initial whole script is not reported as
a passing run. The copied attempt-status directory was then corrected to a
separate isolated internal-SSD directory, as required by the storage guard.
The added sandbox write permission covers only that isolated directory.

The corrected configuration received another explicit native dirty-contract
revalidation. All 7,314 units remain pending, with **21,964 retained versions**:
the original 7,336 plus two explicit 7,314-unit contract revalidations. No accepted
work was acknowledged by these preparations. The final runtime contract is
`1f5d555facfe3dc5b0881d25c3ca034540ba43e61f869d44aacf4672cfbafd7b`.
At **16:13:30 EAT**, normal dispatcher entry returned `maintenance_paused` with
no work dispatched. Explicit preflight separately rejected the missing measured
phase/admission/audit contracts. The final private rehearsal receipt SHA-256 is
`7242e235bd13571b22bf5925c21fe7270fb0e7ff3c6ec82f526a186b7b9a05dc`.
No production cron/worker deadline was increased by this migration allowance.

Measured production-size history remains the earlier 11-detail/five-listing
zero-changed-job pass (51.814 seconds), remaining listing passes (61.536 and
50.367 seconds), and the 50-store/200-export bootstrap (181.478 seconds).
Neither those cases nor the new small changed-job fixture establishes worst-case
40-job publication or ordinary policy-audit capacity. Therefore phase/admission/
policy-audit fields in the deployment drafts remain **unapproved and null**;
the dispatcher must continue refusing those unmeasured settings. The proposed
840-second entry ceiling also remains unapproved relative to the old 1,200.

Production readiness still needs historical proof/review resolution, independently
reviewed writer/admission scope, viable production cohort/audit measurements,
reviewed bounded settings and an authorized cutover plan. Native copied activation
and a verified export boundary do not certify an empty backlog or deployment
readiness. The actual production maintenance marker is never removed by rehearsal.

## Validation and policy scope

Core package: **3,885 passed, 9 skipped**, 621.55 seconds on macOS. Skips retain
the ignored/private-resource limitations recorded in the previous validation.
The subsequently added renewal tests are a separate **7 passed** group (0.42
seconds), covering exact device-only transition, wrong mount/alias/owner, changed
store bindings and idempotent renewal with retained rows/clocks. Dispatcher and
repository hygiene: **167 passed, 17 subtests**, 27.85 seconds. Focused tests
included in these scopes are not added again. Changed-file lint and diff checks
passed. Linux CI for the pushed code is reported separately by GitHub.

R1/R3 provider, enumeration, parsing and main-text contracts are unchanged.
R2/R4 charges, uncertainty and useful history remain retained under actual-owner
and maintenance boundaries. R5 uses bounded verification, retained interruption
journals and a hard supervisor. R6 provides explicit resealing, exact immutable
source and separate proposed/activated scope. R7 changes demonstrated preparation
and rehearsal failures. R8 separates small-fixture success, copied authority,
held history, actual production and deployment readiness.

At **16:05:53 EAT**, the deployed 152-module M0 fingerprint remained
`4642596ad8c19900a6d2cf474c144d22907df89b90e26ee78aa7d0c709f70c39`.
Production configuration, maintenance, health and publication gate bytes all
matched their prior SHA-256 values. There were zero active matching cron entries
and zero production source-head files. The later normal-entry rehearsal retained
the same actual maintenance marker.
