# IFAD public job links

`public_job_deeplinks: true` selects the public GET route for an exact
`JobOpeningId`, with `Page=HRS_APP_JBPST_FL`, `PostingSeq=1`, `SiteId=1000`,
`FOCUS=Applicant`, `Action=U`, and `languageCd=ENG`. Adding `job_id` to the search
page does not select a posting. Listing and detail records use the public
posting route as their source and apply URL.

The adapter requires an exact final route/query and matching returned job ID.
Standing programmes omit that ID; their title must uniquely match the requested
ID in a freshly fetched listing. Search/session responses, redirects, mismatched
IDs and ambiguous titles fail detail validation. Stored HTML is limited to the
public page container, excluding the surrounding session form.

The sanitized fixture contains nine public page containers captured on
21 September 2026. It contains no request headers, cookies, session form or
private filesystem paths. These historical fixtures validate parsing and
identity checks; they do not certify current vacancy status or live availability.
The guest-form route remains available when the feature flag is disabled.
