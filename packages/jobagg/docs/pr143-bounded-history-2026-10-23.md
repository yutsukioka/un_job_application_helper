# PR #143 bounded historical evidence reconciliation

Observed **23 October 2026, Africa/Nairobi**. This follows the
[historical census and setting review](pr143-historical-resolution-2026-10-23.md).
The starting candidate was `440103be`; the starting unresolved census was
**5,604 controls**, within **6,471 total controls**. Controls are not jobs,
attempts or publication occurrences. Production remains fenced and inactive.

## Reader and copied-path contract

The new reader validates the complete UTF-8 JSON grammar and hashes every byte
while retaining only publication identities, text, proofs and destination/archive
references. It rejects duplicate keys, malformed skipped content, non-finite
numbers, aliases, changing files and hash mismatches. Reads use 64 KiB chunks;
limits include 1 GiB per plan, 64 levels, 200,000 entries per container,
200,000 retained nodes, a 64 MiB retained-text default, and a finite deadline.
The operator additionally requires a reviewed finite inventory (at most 512
plans), at most 64 plans per page, explicit per-plan/total byte allowances,
at most 300 seconds per invocation and an outer supervisor. Ordinary ticks do
not discover or parse these historical plans.

The actual pass read **174 original plans / 15,660,857,882 bytes** in place,
using 8-plan pages, 90-second inner deadlines, 120-second outer supervisors,
a 768 MiB per-plan and 16 GiB total allowance. It took **617.352 seconds**
over 22 pages. No plan or archive payload was copied. The separate sealed
7,192,576-byte index contains **4,176 detail and 637 listing occurrences**.
All **4,813 occurrences** matched exact publication-key, generation and job/source
commit journals in both copied destinations. No document occurrence was indexed.

Destination mapping is derived from the exact snapshot hash already pinned by
the copied native bootstrap. Each original path maps to one exact copied path;
prefix and basename guessing are rejected. All 50 copied store fences and the
200-export boundary remain independently bound. A rehashed forged mapping is
rejected against the original snapshot. Resume requires the same reviewed
inventory and mapping; changed selected source controls, destinations or index
bytes invalidate reuse. No schema, table, index or trigger was added to the
already activated copied worker: the historical index is a separate database.

Older producer receipts omit the modern `readback_verified` field. That omission
is retained explicitly. Historical commit proof uses the original plan/result
pair and the copied destination journals; it does not invent that flag, prove
fresh public content or substitute for the current full export certificate.

Private immutable index receipt:
`10555d22b5a1ae0dd9e1081e31d0abf1e5641a87e8383d24e65614dd97e2429e`
(database SHA-256). Its original generation review SHA-256 is
`58d2c23619eb54d1f10ff479e7afc9a4929a2be7bd0ccd75315997de0a4a5fad`;
copy-map SHA-256 is
`f2e86c4438ad3fcd1618660d3f70dd59ad6e2fb002c4719ec996d997f3897ad5`.

## Scoped record reconciliation

The owner-held operator selects only terminal-publication records with an
already imported, reviewed successful exit. Unknown implementations, incomplete
generations, old-owner holds and binding contradictions cannot use this closure
path. Every successful closure rechecks exact accepted artifacts/task identities,
historical occurrences or exact current readback, original controls and the
current complete export boundary. Per-record native transactions and durable
copy-local receipts survive interruption. A changed review/checkpoint is rejected.

The pass uses 16-record pages, 120-second page budgets, 150-second outer
supervisors and explicit 5-second assessment ceilings per record. An unresolved
record can stop at its first demonstrated gap: its receipt then explicitly says
that the assessment was not exhaustive. Successful closures always exhaust the
accepted interval set and repeat canonical verification inside the native
controller. Read-only destination handles are reused within each run, capped at
128 and closed on success or error. Missing files, a missing older occurrence
and diagnostic timeout are different outcomes. Timeout is not evidence of data
loss. Unfinished page commits remain recorded and resume skips only matching
native checkpoints. There is no blanket success or task/policy reset.

Auxiliary wrappers with additional retention actions remain held. In particular,
a child worker outcome does not certify a wrapper's later deletion actions.
Archive/reference metadata has not been promoted to a content-verification or
retirement certificate. The large retained archives have not been reread or
copied merely to label them. The existing global admission gate is unchanged:
unknown shared scope cannot be declared independent by changing a status label.

## Revalidation after historical loss

The bounded filename search for the 187 interrupted runs checked 374 expected
acceptance/exit locations in the HDD backup. It found one 58,617-byte acceptance
report and no successful-exit files. That report lacks task intervals. Retained
stdout/stderr do not reconstruct a missing successful exit. Neither timestamps
nor later successful work establish membership in those runs. Their requests
covered all 49 sources, so a narrow source exemption is not evidenced.

After consultation, the user authorized preparing separate isolated surviving-
artifact revalidation despite possible earlier deletion or power loss. The new
read-only operator verifies retained attempt/task crosswalks, artifact hashes,
source/native identity, text hashes, capture metadata and bounded decompressed
capture-body hashes. Missing files, corrupt bytes and changed identity remain
explicit holds. Listing/document controls require further selected semantic
verification. This procedure cannot reconstruct lost data or original run exits;
it changes no native rows and authorizes no recovery, publication or retirement.
All receipts explicitly leave historical exit, publication and completeness
certification false. The selected rehearsal inventory has one deterministic
retained detail attempt for each of **46 sources**, plus **eight listing
controls**. This is a sample, not all retained observations or all 49 sources.

## Completed scoped assessment

At **19:26 EAT**, all **968** reviewed terminal-publication controls had durable
assessment receipts. **94** closed through exact acceptance and publication
verification. **874** stayed held: 551 with unproven older detail occurrences,
265 with unproven listing identities, 12 with both, and 46 that reached the
explicit assessment deadline. These are record counts, not counts of lost files
or accepted tasks. Most held diagnostics stop at the first gap; the 46 timeouts
are not data-loss findings and can receive a separately bounded larger review.

The private scoped ledger accounts for every one of the starting **5,604**
records, retaining scope, native disposition and next review step. Unknown/shared
auxiliary scope stays explicit. It does not provide an admission exemption.
The resulting native census is:

| Disposition | Records |
| --- | ---: |
| Terminal verified (six original plus 94 newly reconciled) | 100 |
| Preserved wrapper references | 861 |
| Publication reconciliation required | 874 |
| Repair required | 4,438 |
| Uncertain original worker claim | 187 |
| Accepted without publication intent | 8 |
| Existing generation pending | 3 |
| **Total** | **6,471** |

Thus **5,510 remain unresolved**. The repair subset retains 1,140 implementation-
only reviews, 42 missing-original-task-identity cases, eight old-owner/missing-
identity cases, six legacy binding reviews and 3,242 auxiliary sets. The six
binding cases omit modern ownership/recovery attestation fields; they are not
approved by inventing those fields. No global admission gate was relaxed.

The first surviving-artifact sample held four otherwise valid detail artifacts
because their capture lists included deliberately bodyless redirect/error steps.
The revalidator was corrected to preserve those immutable metadata records as
having no recorded body. Such records contribute no bytes and cannot prove a
successful detail. A successful identity-bound 200 capture is still mandatory;
missing successful bodies, contradictory body metadata, corrupt bytes and wrong
source/identity stay held. Positive 302/400 and negative bodyless-only/missing-
success cases are regression tested. No redirects are followed or fetched.

The corrected same **54-item** sample now verifies retained bytes and task
crosswalks for **45 of 46 detail artifacts**. One actual path alias remains held
for exact locator review; eight listing controls retain further semantic-review
requirements. No sample outcome reconstructs the 187 missing run exits or
certifies enumeration, whole public text, publication, freshness or retirement.
The original diagnostic receipt is preserved. Corrected sample receipt SHA-256:
`6ecfc6278e92ac8846a3e1b087450d5a6a2e85d3ffa1c8197f29b6d8f064194b`
(final immutable-source repetition; earlier receipts remain retained).

The scoped control review preserved 23,438 jobs, 9,500 tasks, 67,017 attempts,
7,265 observations, 26,451 accepted-exit attempt identities and 7,314 dirty units.
Source revision stayed **26,196**, idle. Separately recorded explicit code-contract
revalidation adds new dirty versions; it neither acknowledges nor discards work.

## Candidate and outstanding gates

All **173 package Python modules**, including the four offline modules, remain
inside one implementation seal:
`1d2f34c6cd56212b4bb607728fb3d850a9674ee760045512cfc3afaa226091bf`.
Reviewed scope:
`8c66a584c99f3ef9a766e8943c2d5d13205af1122d97026db78aeafcfb59aa10`.
Deployed M0 remains its separate unchanged 152-module implementation, and both
tracked M0 baseline files remain unchanged.

Readiness remains **false**. This increment supplies the large-plan reader and
exact copy mapping; it does not waive unknown historical scope, accepted-event
backfill, the complete writer inventory, measured production phase/admission/
audit settings, the managed-storage gate, or a full admitted-cycle rehearsal.
The maintenance marker remains present, cron stays paused, and no production
worker/publisher is dispatched. No HDD payload is copied to SSD. Historical
verification is kept separate from fresh listings, independent completeness,
whole-public-text fidelity and current public availability.

## Validation

The final 173-module seal passed the complete JobAgg suite: **3,989 passed,
nine skipped**, **596.52 seconds**. Dispatcher/repository checks: **167 passed,
17 subtests**, **24.72 seconds**. Focused subsets and the earlier 3,986-pass revision are not added to this total. New cases exercise skipped malformed JSON, chunked
UTF-8/escapes, retained-memory/node and time/byte ceilings, changed aliases,
forged mappings, missing exact journals despite a fresh current boundary,
checkpoint resume, unknown-implementation/partial-generation rejection,
production alias guards, deleted evidence and corrupted capture bodies.
Changed-file lint and staged whitespace checks pass. This validates the tested
local candidate; it is not provider validation, fresh completeness, a completed
admitted-cycle rehearsal or production activation.

## Final immutable rehearsal and production boundary

Committed runtime **`6d029df64afaa72570626cf89ff0969967aa2d83`** is staged
read-only at **`pr143-6d029df6-1d2f34c6cd56`**, containing 1,968 source files.
Source-manifest SHA-256:
`e7de5a0febdb434bb14185c2ae022b9eeea2323ab06761979c0ea908e3c34f07`.
Only inactive preparation drafts reference this release. The documentation
follow-up does not change its tested package bytes or implementation seal.

At **19:42 EAT**, final immutable entry returned `maintenance_paused`. OS-level
network and production-write denials were demonstrated. Preflight separately
rejected missing measured phase, admission and audit contracts; those settings
remain null and unapproved. No provider worker or publisher was dispatched.
The 50-store/200-export boundary verifies; policy revision remains zero and its
database hash is unchanged. Backup locator identities are current without a
new payload copy. This is a fenced entry rehearsal, not a full admitted cycle.

The first explicit immutable code-contract revalidation retained all dirty units
and advanced copied dirty versions from 36,592 to 43,906. The corrected seal's
separate explicit revalidation added another 7,314 versions: **51,220 total**,
with **7,314 dirty units** still pending. Jobs, tasks, attempts, observations,
accepted-exit identities, source revision 26,196 and the idle phase are preserved.
The earlier native head was not discarded or recreated. Each code/configuration
transition and its prior receipt remain retained. Final rehearsal receipt:
`6a5f46139fc98eba7bf95080ee08ad68d004aaf39114ef0853c2b205c7495b17`.

At **19:43 EAT**, production configuration, maintenance, health and publication
state hashes matched the preserved boundary. Deployed M0 and both tracked M0
baseline files were unchanged. Owner inode **4728** remained empty and available;
there were no production source heads, active JobAgg cron entries, or matching
worker/publisher processes. No native maintenance handoff, code switch, quota
reset, payload retirement or HDD-to-SSD payload copy occurred. Readiness remains
**false** with the 5,510 explicit unresolved controls and other gates above.
