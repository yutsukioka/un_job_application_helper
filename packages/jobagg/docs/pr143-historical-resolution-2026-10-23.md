# PR #143 historical reconciliation and runtime-setting review

Observed **23 October 2026, Africa/Nairobi**. This increment follows the
[isolated cutover and reboot renewal](pr143-isolated-cutover-2026-10-23.md).
**Production remains fenced and inactive. Deployment readiness is false.**
The unresolved proof and capacity gates cannot be waived to promise deployment
on the next turn.

## Historical controls

The original complete census contains 6,471 control records, including six
previously verified terminal records. It did not represent 6,471 jobs or tasks.
Of the 6,465 initially unresolved records, **861 diagnostic wrappers are now
preserved references** to exact independently indexed worker outcomes. The new
operator verifies both original wrapper controls, their retained physical
identities, attempt/configuration/PID/time bindings, exact child outcome hash
and contents, and process absence. It refuses extra actions, differing recovery
results, missing children, aliases and changed evidence. A reference does not
certify that its worker succeeded, that acceptance was valid, or that publication
completed. The worker record and its unresolved disposition remain retained.

A bounded source search examined 101 retained Git revisions and 506 distinct
Python blobs (16,599,182 bytes). Eight historical fingerprints were reproduced.
A separate retained-release search read 36,480,136 source bytes and additionally
located the local October 21 implementation. Two full historical module maps
were found in review metadata. These discoveries are provenance evidence;
matching a reported hash alone is insufficient approval.

This increment admitted **six exact historical implementations already merged
by the original PR #142 boundary**, using their reproduced complete package
fingerprints. The M0 approval is unchanged. Unmerged/local-only discoveries
were not added to that reviewed set. The reviewed commits are:

| Merged commit | Package fingerprint prefix | Original reports |
| --- | --- | ---: |
| `c32dcf7d` | `72f7e64ad228` | 130 |
| `4aa8b8f0` | `fa57f6e056aa` | 63 |
| `874b7915` | `985d89ddc9b9` | 368 |
| `9e3729ee` | `ca42922ee798` | 164 |
| `7eb148cf` | `60eb97ffaca7` | 116 |
| `0e7f0633` | `8a0d0229c470` | 105 |

The explicit owner-held acceptance review reverified original controls and
successful request/report/exit/task-interval bindings for **946 records**. It
retains the original hold and review digest in each proof. It cannot remove
missing-interval, old-owner, publication-contradiction or publication-proof holds.
The copied accepted-exit catalogue grew from **1,201 to 26,451 exact accepted
attempt identities**. These are acceptance references, not newly fetched jobs
or proof of historical publication. No original worker exit was rewritten.

The resulting census is:

| Disposition | Records |
| --- | ---: |
| Terminal verified | 6 |
| Wrapper reference preserved | 861 |
| Accepted, publication intent absent | 8 |
| Existing generation pending | 3 |
| Publication identity/export reconciliation required | 968 |
| Repair required | 4,438 |
| Worker claim uncertain | 187 |
| **Total** | **6,471** |

Thus **5,604 records remain unresolved**. The 946 acceptance reviews are part of
that remaining count; they are not another 946 completed publications. Three
previously hidden unfinished generations now have an explicit pending-generation
disposition. Repair work includes 3,242 auxiliary sets, 1,140 implementation-only
holds, 42 implementation/missing-interval holds, eight relocated old-owner cases
with missing intervals, and six publication-binding contradictions. The original
51 reports without task intervals remain accounted for; one was not a bound
successful worker exit. Unknown historical process failure remains failure.

The copy retained 23,438 jobs, 9,500 tasks, 67,017 attempts, 7,265 observations,
7,314 dirty units and 29,278 dirty versions during these control reviews.
No accepted work was acknowledged or erased by review. Later explicit contract
revalidation may add dirty versions and must record that transition separately.

## Backup remount correction

The HDD locator map still contained device **16777268**, while the remounted HDD
reported **16777264**. All other pinned file-identity fields matched. The new
copy-only operator verified **all 469 files / 3,142,043,607 bytes in place** against
the retained expected hashes. All 469 expected hashes also match the original
SSD-resident bootstrap evidence list. **No HDD payload was copied.** The pass
completed in 29.974 seconds within a 4 GiB / 300-second allowance and 330-second
outer supervisor; this is not a certified cold-HDD latency measurement.

An immutable plan preserves the original map and policy head. A verified receipt
precedes the pending-head/map/final-head handoff; interruption at either write
window resumes only that exact handoff. Each file retains its original content
hash, inode, size and timestamps; only its device locator changes. The renewed
map is sealed read-only before the new head binds its identity. Other immutable
controls, wrong scope, alias/hard-link, source activity and policy changes stop.
Original migration receipts are retained rather than rewritten as new events.

Copied policy revision remains **zero**. Its database remains byte-for-byte
`2d131d3366d849afd3c4b7efed24a26d1735b69bb7613f2c5d53eb09e7b9a4c7`.
There was no quota/charge/history reset. This explicit operator does not relax
ordinary policy identity guards or automatically renew an active production
mount. Production's marker, owner inode, policy and source remain untouched.

Private verification/reconciliation receipts remain under the existing isolated
snapshot; their digests are recorded with preparation readiness:

- Wrapper reconciliation: `3075e733eb322d0bf8e1cbb440da7a5b82504f31c30fb0c3b73c7a71e9fb360c`.
- Historical implementation review: `c3d3467617bbce36fecfd114ff096ca75a9cd85a6d0c376de3478feccee0b891`.
- Backup verification receipt: `fd1d2a3b43e0076a5405737d6bd56ee76b15e34991c9f0f6bb53d107783bd995`.

## Runtime settings

A demonstrated API validation defect accepted non-finite or boolean audit lag
and clock values, allowing an overdue comparison to be bypassed or invalid
values to reach a write. Invalid lag/clock values now fail before authority
access or mutation. Ten cases cover finite/type/clock constraints and preserved
head/revision; nine reproduced failures before the correction.

Read-only measurements on the copy covered 147 indexed policy admission calls:
P50 **0.928 ms**, P95 **1.508 ms**, P99 **1.815 ms**. These are warm sampled calls,
not whole-entry overhead percentiles. The copied authority retains **72,357
evidence objects**. Two 512-object audit previews verified 193,908 and 193,384
bytes in **0.557** and **0.365 seconds**. A different page verified 248 objects /
521,320,334 bytes in **5.071 seconds** and then stopped at the shared 512 MiB
read ceiling. It was not a completed 512-object audit. The previews changed no
cursor, completion timestamp, quota row or policy revision.

These observations demonstrate heterogeneous page cost. They do not establish
largest/cold-page capacity, complete sweep latency under history growth, an
approved maximum audit lag, 40 changed jobs with production beforeimage/export
cost, or the whole 840-second entry proposal. The prior one-job and unchanged-job
measurements remain limited as previously documented. `phase_budgets`,
`publication_admission` and `policy_audit` stay **null and unapproved** in both
deployment drafts. Existing 100-task/80-detail/200-request, 360-second worker,
420-second worker outer, 40-job/five-listing and 720-second publication ceilings
are preserved. No retention/compression or supplementary fetching was enabled.

Deployment still requires exact remaining historical acceptance/publication
reconciliation, reviewed complete writer/admission scope, viable production
cohort and audit-capacity settings, and a separately authorized cutover. The
large historical generation plans also exceed the current 16 MiB selected-control
reader, and copied destination paths need an explicit historical identity
crosswalk; neither is permission to infer old publication from current rows.
Any future bounded reconciliation change must retain those proofs and guards.
The isolated worker's native source head is preserved; do not restore or discard
it to repeat bootstrap. Ordinary dispatcher entry continues to honor maintenance.

## Validation and preservation

The final seal covers all 169 package Python modules, without exclusions:
implementation `1ea470f3e46bcff3b9a911ee88de17cdb3744d95bb56a3604836946247bcf2a2`,
reviewed scope `e729cac668067722f748bad956b8fb51e94782686d2cf22fb5fa44c3917b08a4`.
Focused tests cover native authority fencing, original-proof preservation,
unknown/missing-interval rejection, wrapper binding and alias failures, finite
audit settings, remount hash/identity failures and both interruption windows.
The final sealed complete package suite passed **3,942 tests**, with **nine
skips**, in **600.60 seconds**. The earlier intermediate 3,913-pass run and
focused subsets are not added to this total. Dispatcher/repository hygiene:
**167 passed, 17 subtests**, **25.30 seconds**. Changed-file lint and staged diff
checks passed. The final package code remained unchanged after these runs.

All mutable production-sized operations used the existing copy under the actual
owner and unchanged maintenance marker, with the OS network/production-write
denying sandbox and finite outer supervisors. The deployed 152-module M0 overlay
and both reconciled M0 baseline documents are preserved. Production has not been
activated, and readiness remains false.

At **17:12 EAT**, deployed M0, production configuration, maintenance marker,
health and publication gate hashes matched the retained boundary. The actual
owner remained inode 4728, was available and empty; there were no production
source heads, active JobAgg cron lines or matching worker/publisher processes.
