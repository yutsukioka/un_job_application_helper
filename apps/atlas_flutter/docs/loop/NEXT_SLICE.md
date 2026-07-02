# Next Slice

Gate state: G1 approved. G2 design review, G3 physical device pass, and G4 review feedback remain pending.

Intent: implement the first persisted offline-cache slice for Android. The app now refreshes session results from `POST /api/search`; the next slice should persist that list snapshot and the saved API base URL to Android app storage, reload cached results on first launch, and fall back to the cached list when the local server is unavailable.

Acceptance tests to add before implementation:

- Unit tests for an `AtlasLocalCache` implementation that writes and reads the saved API base URL, list snapshot, cached-job count, refresh timestamp, and refresh interval using injectable storage paths.
- Unit tests for corrupted/missing cache files proving the controller keeps a usable offline empty state and reports a clear cache error without crashing.
- Widget tests proving Settings Save and Reload persists the normalized server and refreshed jobs, then a fresh controller/app instance loads those cached values without another server call.
- Widget tests proving Refresh Local Save Now updates both the session result list and the persisted list snapshot.
- Integration test on `Pixel_8_Pro_API_17` that refreshes from `http://10.253.1.43:8765`, restarts the app, and confirms cached jobs appear before a live refresh.
- Preserve existing app-shell, Settings, Search, and Android network tests; coverage must stay above `98.19%`.

Implementation should remain scoped to list-level offline persistence and saved server preference. Detail cache, filter sheet, saved jobs/searches, source updates/summaries, feedback loop, and goldens stay in later slices.
