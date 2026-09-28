# IDB public search API

`public_search_api: true` uses the observed public POST endpoint
`https://jobs.iadb.org/services/recruiting/v1/jobs` for the unfiltered All Jobs
category (`9638000`), locale `en_US`, with both default and date ordering.
The adapter requires consistent totals, consecutive terminal pages and valid
public identities. It preserves usable records when the two-sort union is
smaller than the advertised total while marking the listing incomplete.

The independent enumeration verifier replays hashed response captures, checks
exact request scope, method, response status and URL, and compares the resulting
identities, titles and URLs with the emitted jobs. Only a complete union equal
to the advertised total may certify this configured listing scope. Missing or
modified captures cannot certify closure of unseen jobs. Detail parsing and
publication gates continue to use the existing verified pipeline.

The sanitized 21 September 2026 fixture retains only public request bodies and
responses (no headers, cookies, authentication or private paths). Its 20 page
responses advertise 91 jobs but yield 89 unique IDs across both sort orders;
this is deliberately an incomplete-inventory regression fixture. The fixture
does not prove current counts, vacancy status or live endpoint availability.
Disabling the flag restores the existing public-board/RSS route.
