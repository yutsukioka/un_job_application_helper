# PR #143 production deployment gates — 24 October 2026

Observed Africa/Nairobi; latest preservation check **2026-10-24T10:34:37.132823+03:00**.
The user's instruction to implement steps 1–7 authorizes a conditional production
cutover after its acceptance gates. No additional activation permission is being
inferred from this document. The standing consultation on substantial code changes
was approved for the [bounded archival reader/migration increment](pr143-storage-reader-migration-proposal-2026-10-24.md). Its [additive implementation](pr143-archival-implementation-2026-10-24.md) preserves every original; [guarded isolated-only removal](pr143-isolated-archive-removal-2026-10-24.md) is separately authorized and implemented. Production source handoff and final deployment gates remain open.
**Production remains fenced and deployment readiness is false.**

## 1. Complete capacity measurement; reader migration remains open

The read-only SSD census measured and verified **18,546/18,546**
selected artifacts in **1732.157 seconds** with explicit Zstandard
level 3. Every original stayed in place. All 8,618 export references, 174 plans and
128 snapshot files matched original producer hashes. Beforeimages initially had
only today's byte hashes; they were not silently called producer-certified.
Every distinct compressed stream was reconstructed and checked against its exact
logical bytes; every duplicate source was independently hashed before sharing its
measurement. No compressed payload was installed or HDD payload copied to SSD.

| Family | Files | Original charged bytes | Distinct contents | Compressed content bytes |
| --- | ---: | ---: | ---: | ---: |
| snapshot | 128 | 90,428,928,000 | 128 | 11,308,591,593 |
| plan | 174 | 15,661,223,936 | 174 | 1,232,457,484 |
| beforeimage | 9,626 | 17,408,786,432 | 8,033 | 1,468,983,668 |
| export | 8,618 | 280,323,158,016 | 4,227 | 9,792,693,025 |

The distinct objects total **23,802,725,770 bytes**,
or **23,828,123,648** under hypothetical 4 KiB object rounding. These are content
measurements, not an installed filesystem charge. The table does not certify
retirement eligibility or include all protected hot copies, directory/control
allocation, manifests/journals, restore scratch, growth reservations and the
remaining active scope. The exploratory 1 MiB chunk measurement is a different,
unimplemented representation and cannot be added as a separate percentage saving.

The separate strict original-plan review verified 174/174 producer plans and bound 9,626/9,626 beforeimages to their original producer hashes. Unbound inputs remain ineligible; this does not prove historical exits or authorize retirement.

A native inventory completed **587,118 entries** in
**124.004 seconds**, with no errors, for the production tree plus
explicitly resolved legacy Python/log targets. Its charged lower-bound total is
**442,900,754,002 bytes**. It still refuses admission
for publication/work and the total ceiling. All legacy run contents were
conservatively classified as work for this review; final category separation is
not approved. The two earlier inventory attempts retain their unregistered-target
and 240-second-timeout results. A timeout is not data loss or a completed census.

This lower-bound scope does not include all candidate/runtime/staging or active
HDD references. The two rehearsal trees alone measured approximately 44 GB
allocated. Explicitly review their future active/offline roles and preserve unique
proofs; do not hide them outside the registered scope or discard the accepted run.
The full registry, actual installed/peak category charges, 100 GB total and 20 GiB
physical reserve remain deployment gates. No larger ceiling is proposed.

## 2. Measured settings proposal, not production approval

A complete uncached read-only policy sweep verified **72,357 objects**
and **3,169,233,879 bytes** in **83.927 seconds**. This includes
**3,142,043,607 HDD bytes**, read in place with `F_NOCACHE` on
read descriptors. The slowest 1,024-object page took
**26.586 seconds**. The policy database/head,
quota history and native audit cursor were unchanged. External verification is
not a native cursor advancement or a fresh worker-cycle result.

The separate unapproved configuration proposal retains worker command/outer
ceilings 360/420 seconds, publication ceiling 720, total entry 840 and cleanup
10. It proposes projection maximum/minimum 180/2 seconds and a **180-second
publisher/admission floor**, above the measured 156.740-second real changed-batch
wrapper. It preserves 40 units, one source, two targets, 256 candidates and a
64 MiB projection cap. Its 32 GiB dependency estimate is neither allocated storage
headroom nor an exemption from category limits. The previous two-second floor
measured planning only and is not viable for the complete publisher path.

The proposed audit remains 1,024 objects/24 hours: the present 72,357 objects need
71 pages or 17.75 hours at one page per 15-minute wakeup. Sleep, missed ticks or
future growth cannot waive the native overdue repair gate. Worst-case publication,
peak write envelopes, hot-path performance and the exact migrated-layout cycle
still require validation. No independent clock is added. Insufficient shared
remaining time must preserve accepted work and defer publication.

The accepted rehearsal configuration, its contract, production draft and real
production configuration remain unchanged. The candidate proposal is private at
`pr143-deployment-settings-20261024/dispatcher.production.proposal.unapproved.json`
under the existing rehearsal root, with measurement seal
`d46c1852e7aedca35e02101f924e56f22f5e4325951b0814d26a7b3744bc9e26`.

## 3. Actual stock entry checks; full fresh cycle remains open

The immutable candidate's real supervised entry rejected an already exhausted
inherited deadline, refused a busy actual owner, then returned
`maintenance_paused` with its documented exit **75** after the scanner drained.
The final maintenance check took **0.107 seconds**;
both marker hashes and the worker database identity/stamps remained unchanged.
These tests do not certify watchdog containment during a worker/publisher dispatch
or a full fresh worker-to-publication cycle. Earlier private harness expectations
incorrectly treated maintenance as exit zero and mistook a Git diff mentioning
the launcher for a running Python writer; their outputs remain preserved. Neither
required a product code change or process kill.

The earlier same-run retained publication/recovery rehearsal remains passed:
39 changed jobs, eight exports, full 50-store/200-export boundary, no worker rerun.
Do not reset that copy to idle or change its accepted configuration to manufacture
a new cycle. Finish the preserved queue or use a separately bounded, reviewed
bootstrap for a fresh-cycle rehearsal on the final storage layout.

## 4. Complete current pending/held review

All 7,314 current units were reviewed read-only: **39 acknowledged, 37 held,
7,238 pending**. Of the pending units, **2,872 have scoped integrity holds** and
**4,366 do not have those particular holds**; this is not a provider/task eligibility
claim. Held dispositions comprise 29 missing-exit cases, seven missing independent
document-acceptance cases and one shorter-text publication rejection.

The next bounded selection contains **three AFDB units**, excludes scoped-held
units, and records 125 held candidates. Its conservative dependency estimate is
29,208,577,848 bytes, not a promise of that much free/reserved storage. Among task
headers, 1,879 pending tasks lack a scoped integrity hold and 67 have one; host,
quota, source and ordinary task gates remain necessary before claim. All 5,483
historical control holds, original dispositions, 67,017 worker charges and all
71,888 policy charges remain. No holds were released and no original exits
reconstructed. The authority stays valid in accepted continuation for the same
run/revision 48,664, publication sequence 2.

## 5. Candidate acceptance

All seven CI checks passed for head
`3a7f9b7b6bcbabe0b0abbdd00bc0c9b73f9efe6a`; review threads were empty.
The existing runtime validation remains 4,049 JobAgg passed/9 skipped and 169
dispatcher/repository passed plus 17 subtests; overlapping focused tests are not
added. This increment changes preparation/review records only. Runtime commit
`5b464480` and its 177-module seal
`338ab70868a45148b8e516acf111ed667753c6df13b2940e06ea187c6837b6aa`
remain unchanged. New storage code needs its own tests, R1–R8 review, explicit
complete reseal, passing CI and immutable merged source before activation. PR
#143 remains draft; this report is not a merge or release acceptance.

## 6–7. Conditional cutover and rollback sequence

1. Complete the consulted storage implementation and reader/dependency tests;
   execute only the reviewed lossless migration pages. Preserve useful bytes,
   historical holds and exact producer references. Obtain fresh complete scope,
   actual steady/peak capacity and production setting approval from the evidence.
2. Finish the full isolated fresh cycle, interruption/owner handoff and stock
   watchdog tests against the final immutable layout. Review the complete PR,
   merge the accepted candidate and stage/verify its exact source/configuration
   set. Preserve all prior immutable releases and M0.
3. Under the existing native production marker and actual shared owner, verify
   drained process identities, exact fence token, production configuration,
   policy/counters/claims and complete publication boundary. Reconcile only an
   applicable same-generation unfinished transaction. No provider request occurs
   during the authority switch.
4. Apply a separately sealed production-root migration receipt, not a copied
   rehearsal receipt or a bypass of a copy-only tool. Bind the complete writer
   inventory, source/policy/store/schema identities, authority epoch/revision and
   accepted/unpublished controls. Catch up compatibility exports and fence old
   manual/browser/cron writers. A remounted physical identity needs an explicit
   original-content/volume-bound attestation, never a token/head rewind.
5. Publish the coherent immutable launcher/configuration selection while holding
   the owner. Remove only this operation's unchanged marker as part of that
   owner-held handoff; retain the prior commented cron line and every unrelated
   schedule. Validate one bounded production cycle before ordinary scheduling.
   Stop and refence on uncertainty, identity/content loss, a new challenge,
   weakened transport policy or an unresolved publication transaction.
6. Record native process exit, acceptance, source/consolidated commits, exports,
   readback and exact version acknowledgements separately. The complete handover
   additionally requires three consecutive successful natural cycles, actual
   controller-approved overlap above one where eligible, and safe downshift.
   Do not reset the controller to configured initial concurrency 16, force
   overlap, equate wakeups with coverage or start an unrequested 24-hour monitor.
7. Before writer activation, rollback may restore the coherent prior reader/code
   selection while fenced. After native authority activation, use a compatible
   release or reviewed reverse projection with all new charges/data preserved.
   If reverse compatibility is unavailable, remain fenced and roll forward from
   the durable journal. Preserved M0 is not permission to run a stale file-based
   writer or overwrite current data with a pre-cutover copy.

The user's activation authorization is recorded; **none of these conditional
production mutations was executed**. M0, real maintenance/configuration/health/gate
hashes remain unchanged, actual owner inode 4728 was available, production source
heads and active JobAgg cron entries were absent, and no matching worker/publisher
was running at the latest preservation timestamp. A read-only Git diff merely
mentioning the launcher was distinguished from a writer. No production payload
was changed, compressed, retired or copied from HDD.

Private evidence remains local under the existing rehearsal root: the complete
capacity database/receipt, strict inventory v1–v3, uncached audit pages/receipt,
pending-unit census/selection, settings proposal, stock entry attempts and
preservation receipt. These observations are bounded preparation evidence, not
claims that all seven deployment steps are complete.
