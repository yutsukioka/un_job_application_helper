# Next Slice

Gate state: G1 approved. G2 design review, G3 physical device pass, and G4 review feedback remain pending.

Intent: replace the generated counter surface with the first Android Atlas app shell and Search tab skeleton, backed by the newly added domain/API contracts. This slice should establish the Material app name, Android bottom navigation, Atlas tab labels, search field, filter-chip ribbon placeholder, result-count/sort bar, and empty/offline-ready search content without yet implementing the full view model or API-backed result list.

Acceptance tests to add before implementation:

- Widget test proving the Atlas app title/theme replaces the generated counter app.
- Widget test proving Android bottom navigation exposes Search, Saved, Updates, Sources, and Settings tabs and switches visible tab content.
- Widget test proving the Search tab contains a search field, filter-chip ribbon placeholder, result-count/status/sort bar, and empty/offline state.
- Integration test launching the app on `Pixel_8_Pro_API_17` and navigating all primary tabs without crash.
- Remove or replace the generated counter widget test so the test suite verifies Atlas behavior only.

Implementation should remain scoped to app shell/search-skeleton UI and tests. Full API search execution, local cache, detail screen, goldens, and settings connection flows stay in later slices.
