# Next Slice

Gate state: G1 approved. G2 design review, G3 physical device pass, and G4 review feedback remain pending.

Intent: implement the first API-backed Search view-model slice for Android. This slice should connect the Search tab to `AtlasAPIClient.search`, use the Settings base URL state where available, render loading/error/result-count states from real responses, show an initial result list with `JobResultRow`-level essentials, and preserve the current offline empty state when no server/cache data is available.

Acceptance tests to add before implementation:

- Unit tests for a Flutter `AtlasSearchController`/view model mapping query, sort, and filters to `AtlasSearchRequest`.
- Widget tests for Search loading, API error, empty response, and loaded response states using a fake `AtlasAPIClient`.
- Widget tests for at least one rendered job row with source initials, organization display, deadline pill text, grade/contract/work mode, and match summary.
- Integration test that enters the configured server URL, taps Test in Settings, returns to Search, and performs a fake or local-server-backed search on `Pixel_8_Pro_API_17`.
- Preserve existing app-shell and Settings tests; coverage must stay above `97.42%`.

Implementation should remain scoped to search state and initial result rendering. Full filter sheet, local cache, detail screen, saved jobs/searches, source updates/summaries, feedback loop, and goldens stay in later slices.
