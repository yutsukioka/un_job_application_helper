# UNESCO expertise snapshot provenance

Snapshot: **13 September 2026**, UNESCO tenant on `career2.successfactors.eu`.
The source capture spans **10:33:29.502–10:36:59.516 UTC** and has 496 HAR entries.
The entire HAR SHA-256 is
`64fc478e26e6130cf3101618fd7d11223c054356914f57db1621485d74673616`.
Company `unesco` and page locale `en_GB` were observed in captured page URLs;
the page locale is not a claim about an API-intrinsic locale parameter.

The compact choice index preserves **19 areas and 420 subareas**, all enabled
in this snapshot. Sixty-one captured taxonomy responses comprise 23 area-list
responses and 38 child-list responses. Every child branch was unfiltered and
fully represented by its response's declared count; repeated records match.
The empty-value No Selection placeholders are excluded. IDs, display strings,
source ordering, parent relationships and enabled states were preserved.

Requests use the observed HTTPS POST endpoint
`/xi/ajax/remoting/call/plaincall/picklistControllerProxy.getPicklistByPageWithExcludeOption.dwr`,
controller `picklistControllerProxy`, and picklists `AreaExpertise` / `SubArea`.
DWR data statements were parsed as literals without executing the response.
Response totals, distinct IDs and page positions establish completeness.
Client pagination `totalCount` can be stale from a preceding branch and must
never override the actual response's count.

Entry **495** supplies the complete `YearsOfExperience` list: four enabled
choices plus the placeholder. The exact response hash is retained in
[the years reference](unesco-years-of-experience-2026-09-13.json). Their display
labels do not define fractional-year rounding, the ten-year overlap or
under-one-year handling; no official equivalence to proficiency is implied.

Entry **410**, `rcmV12CandidateProfileControllerProxy.getCandidateProfileVO`,
supplies the allowlisted `Expertise` field configuration. Its response SHA-256
is `e732e198a09f1a05f5b36c68f4a25b4a43c3bc556d1e3e5fc15429890329bbcb`.
Only this section's configuration is bundled, not populated applicant values.
The section was optional and each of its three dropdown controls was required
within a row. No positive row-count cap was established by `maxEntries:0`.

[The source manifest](unesco-source-manifest-2026-09-13.json) records exact
response indices/hashes and the canonical-record digest. It supports an audit
when the original evidence is supplied; it is not a download link to private
source data. The skill does not need the original HAR or research-report
directories at runtime. No cookies, tokens, request headers or applicant records
are included in these references.

For a refresh, inspect a newer actual portal choice set or supplied capture,
keep its date/tenant/locale provenance, and compare exact IDs, labels, parents,
enabled flags and coverage. Validate a derived compact index with the query
helper before adopting it. Keep the old snapshot separately; a partial capture
can confirm observed choices but cannot prove removals or a complete catalog.
Do not modify the bundled snapshot merely because an applicant selected fewer
entries. A catalog validator cannot verify the applicant's competence or years.
