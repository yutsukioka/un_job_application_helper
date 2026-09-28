# IDB public search API

`public_search_api: true` uses the observed public POST endpoint
`https://jobs.iadb.org/services/recruiting/v1/jobs` for the unfiltered All Jobs
category (`9638000`), locale `en_US`, with both default and date ordering.
The adapter requires consistent totals, consecutive terminal pages and valid
public identities. It preserves usable records when the two-sort union is
smaller than the advertised total while marking the listing incomplete.
Each sort walk honors `extra.max_pages`, including the lower policy cap applied
by synchronization (the shipped IDB value is 5 pages per sort). Reaching the cap
before the terminal page preserves observed records but reports
`walk_capped_at_<cap>` in run evidence and cannot certify inventory completeness.
The independent verifier still requires both terminal walks. The observed page
size is 10; an upstream size change fails closed pending contract revalidation.
Valid percent escapes in the observed `urlTitle` path segment are preserved.

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
