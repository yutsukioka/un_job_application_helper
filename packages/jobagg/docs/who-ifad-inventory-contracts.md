# WHO and IFAD captured inventory contracts

This candidate adds independent listing verification for `who_taleo` and
`ifad_peoplesoft`. It changes no provider request, parser, detail identity,
shorter-text replacement, quota, retry ceiling, schedule or transport policy.
No deployment or state repair is authorized by this document.

## Demonstrated problem and evidence

Read-only investigation on 8 October 2026 used production master
`874b79156f420418cb31a08669ccd8cdb3e46218`, implementation
`985d89ddc9b99e0e6d869b8d9175adc69818b216f5b9261fcb04cbd3fc760f3b`.
Raw captures and databases remain outside Git; checked-in regressions generate
synthetic public structures without session values.

WHO's 15:33 UTC listing advertised 65 English postings but returned 25 + 25 + 12
rows: 62 unique native identities. Collector replay reproduces all 62 IDs and
titles; no three-row parser loss occurred. Eight normal captured listings since
7 October 17:32 UTC all have the same three-row discrepancy, including two with
61/64. The precise provider reason is unproved. Repeating a subset does not
establish completeness. Capture attempt: `eef51be4-a752-42c5-861a-87c95b87d439`.
A further 32 archived normal listings back to 2 October 16:03 UTC also fail
count reconciliation: 31 are short by three, one has 54/59. Thus none of the
40 bounded historical replays is a positive WHO census fixture; the complete
WHO path is validated with synthetic structures, while these actual captures
validate its refusal to infer absence.

IFAD's 15:30 UTC capture contains 11 unique native result rows, an explicit
"11 jobs found" count and "11 rows" grid count, an empty keyword field and
unselected facets. Collector replay reproduces the same 11 IDs and titles.
The previous independent verifier reported unsupported. Capture attempt:
`08351bbf-cc47-46cf-90ed-5f97c13934c8`; frame SHA-256:
`2a072d19e2d77d9571033fb679b21f6360175fcd473bdee87f004b83671f73ff`.

## Exact supported scopes

WHO: the configured `careers.who.int/careersection/ex/jobsearch.ftl` board and
English REST endpoint, portal `101430233`, configured empty keyword/location
and unselected search filters. Every POST must match the configured payload
with the next page number. Every page must retain its total and page size,
return the expected row count, and expose only the English posting-language
facet whose count matches the total. Require unique `contestNo` and `jobId`,
displayed/native identity agreement, all terminal pages, and equality with the
parsed population. A detail-fetch exclusion does not subtract a listing from
the census. No other locale or Taleo board is certified.

IFAD: the configured public `HRS_APP_SCHJOB_FL` empty-search route, one successful
GET, exact guest form action and named result grid, no keyword or selected
facets, matching result/grid counts, complete visible indexed native rows and
unique IDs/titles matching the parsed population. Enabled grid pagination,
ambiguous or hidden DOM identities, malformed/truncated results and a count
of 100 or more remain incomplete. The provider's empty-search ceiling is 100;
matching 100 rows cannot establish a census. Zero requires explicit matching
zero counts and an empty native grid/form, never a generic empty page.

Both contracts bind source/family, listing phase, exact HTTPS request/response
scope, URL and request-body hashes, successful captured status, ordered aware
timestamps, body length/hash and metadata hashes. Duplicate JSON members and
ambiguous identity attributes fail closed. Counts, source diagnostics and
parser success alone do not certify completeness.

Reads are capped at 64 metadata files of 256 KiB each, 4 MiB compressed and
expanded per response, 16 MiB expanded in total, 20,000 IFAD DOM nodes and
128 levels. They make no requests. These are input/CPU bounds, not an inner
wall-clock deadline: blocking filesystem reads still depend on the existing
outer worker/publisher deadline. Main-text completeness and supplementary
attachments remain separate and uncertified by these contracts.

## Queue, publication and retry effects

The existing worker only reconciles absent `pending`,
`unavailable_pending_inventory` or `listing_detail_conflict` details when the
new census is complete; typed unavailable outcomes additionally require a
newer census. It records `not_observed`, never closure, and retains text,
first-seen values, charged attempts and receipts. Blocked tasks are not released
by absence reconciliation. The publisher independently repeats verification
and requires both the saved proof and new verification to be complete.
Deploying this code therefore cannot upgrade a previously saved incomplete
IFAD receipt; the next normal listing must produce a new proof.

At the 8 October 16:27 UTC readback IFAD had no absent reconcilable detail tasks
(10 done, 8 blocked and 1 past-deadline). WHO's current incomplete census changes
no held outcome. Fresh state must be checked at activation.

The implementation fingerprint still includes every package Python byte.
This exact candidate renews the scope-bound retry compatibility seal for two
known predecessors: the prior OSCE release `ca42922e…` and deployed incident
release `985d89dd…`. No detail/retry semantics change in this candidate. This
prevents a code-only reseal from reopening unrelated failures or granting fresh
semantic retry budgets. Configuration/input changes are not aliased. Package
edits/additions/removals/renames invalidate the successor scope; later releases
require a new explicit review, not automatic alias inheritance.

## Separate reviewed recovery proposals

WHO: captured HTTP 200 responses for all twelve investigated vacancies bind
to the exact active unavailable template. Eight are already
`unavailable_pending_inventory`: 2603643, 2603971, 2603997, 2604014, 2604027,
2604094, 2604310 and 2604311. Four remain blocked: 2603711, 2603978, 2604025
and 2604143. A separate exact-task `who_unavailable` repair can classify those
four without fetching, retrying, changing eligibility or resetting counters.
None of the twelve appears in the current 62 rows, but the incomplete census
cannot establish absence. Retain all twelve until a newer complete inventory
or separately approved source investigation resolves the missing three rows.

IFAD vacancy 37851: both historical and current captures replay to their stored
texts exactly. The provider removed `Grade: P-1` and inserted one whitespace
character; normalized description-section characters and heading are unchanged.
Retained text has 8,261 characters / SHA-256
`e087586680239178f2baffe30e65e67e89a558ad653f27171102ea6f37f6392d`;
incoming has 8,251 /
`173087cd0e655ad22272fe66c17680acc54daf0befac201648ac72652040c4f2`.
The current native Grade element is explicitly blank. This is a provider
metadata revision; the parser and shorter-text hold are working as intended.
The candidate does not add an exception. Approving this specific revision
requires an exact source/ID/before/incoming/capture-bound review through the
existing reviewed-text mechanism and normal journalled publication, retaining
the prior before-image. It must not become a general IFAD grade-removal bypass.

## Validation and activation boundary

Tests cover valid/empty censuses; the real 62/65 shape; native duplicates,
changing totals, missing/extra pages, wrong filters/locale/source/phase/hashes,
malformed JSON/gzip/DOM, count ceiling, parser disagreement, worker absence
and text/attempt preservation, publisher re-verification and refusal to upgrade
old incomplete proofs. An IFAD parser-to-publication regression retains prior
grade/text in both destinations when a captured blank-grade revision arrives.
Retry regressions exercise both deployed predecessor generations and changed
package/configuration/payload negatives.

Before any separately authorized activation: freeze/review the exact diff and
full package fingerprint; use the real owner lock and maintenance/publication
gates; recheck both configured runtime implementations and protected state;
reseal only the reviewed candidate; preserve the normal 15-minute cron and
source admission times. Observe the next ordinary WHO/IFAD listings under
existing request/time budgets and both publication readbacks. IFAD must show
matching native/parsed/persisted/published census IDs; WHO must remain incomplete
while the provider mismatch persists. Refresh the four-task repair preview
under that actual seal and journal only the approved task changes.

Stop the affected activation/recovery path on identity/scope mismatch, text or
archive loss, unexpected counter/state changes, integrity drift, a new access
challenge or an incomplete publication journal. Roll back a coherent reviewed
code/config/seal set without overwriting intervening live data. No ad hoc
provider probe, challenge bypass or task retry is part of this proposal.
