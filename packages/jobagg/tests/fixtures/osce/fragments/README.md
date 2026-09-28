# OSCE public result-fragment fixture

Extracted from the replacement browser HAR captured on 18 September 2026.
Original HAR SHA-256:
`c9fabf9710a25f9442982450860c8d3d8bfe0f2d0bed83ee73430351857776db`.

Only the five balanced `jResultsContent` HTML regions and the public search
session/site identifiers are retained. They represent 10 + 10 + 10 + 10 + 5
cards, matching 45 unique IDs. The first region is from entry 206; subsequent
regions are from the JSON `Result` fields of entries 264, 267, 270 and 273.
Indices are zero-based. Request/response headers, cookies and the rest of the
HAR are excluded. Synthetic cookie values in HTTPS tests are unrelated to the
user's browser session.

Runtime discovery must not depend on this fixture's session ID, site name,
job IDs or total. The original HAR and cookie values are not test inputs.
