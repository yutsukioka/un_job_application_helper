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

## Candidate and outstanding gates

All **173 package Python modules**, including the four offline modules, remain
inside one implementation seal:
`f6c03c159b4ad8a52835efc6aeaad8353cc4e74a1bd6c82abcc41cec6739aff5`.
Reviewed scope:
`18994554ceb326c43d8123ddcd2fd83abbcacbf1dfa3147e12da7166130e9e34`.
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

The final 173-module seal passed the complete JobAgg suite: **3,986 passed,
nine skipped**, **623.15 seconds**. Dispatcher/repository checks: **167 passed,
17 subtests**, **26.62 seconds**. Focused subsets are included in the full suite
and are not added to it. New cases exercise skipped malformed JSON, chunked
UTF-8/escapes, retained-memory/node and time/byte ceilings, changed aliases,
forged mappings, missing exact journals despite a fresh current boundary,
checkpoint resume, unknown-implementation/partial-generation rejection,
production alias guards, deleted evidence and corrupted capture bodies.
Changed-file lint and staged whitespace checks pass. This validates the tested
local candidate; it is not provider validation, fresh completeness, a completed
admitted-cycle rehearsal or production activation.
