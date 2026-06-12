# Apple App

Shared SwiftUI Mac/iOS app prototype for the `jobagg` search experience.

The app uses the HTTP API and must not read SQLite bundles or scrape ATS sites
directly. `AtlasSearchScreen` now talks to `services/job-api` and falls back to
an empty offline state when the local server is unavailable. Fictional preview
rows are used only by explicit Xcode previews.

## Xcode Preview Canvas

Open the package at:

```text
/Users/yutsukioka2/git/un_job_application_helper/apps/apple/Package.swift
```

Then open:

```text
Sources/AtlasUI/SearchScreen.swift
```

Select the `AtlasUI` library scheme if Xcode does not pick it automatically.
The root package is intentionally library-only so SwiftUI previews do not need
the executable target debug-dylib setting.

## iOS Simulator

The committed package contains the shared `AtlasUI` library and a Mac preview
host. To view the iOS app shell now:

1. Open `apps/apple/Package.swift` in Xcode.
2. Open `Sources/AtlasUI/SearchScreen.swift`.
3. Use the SwiftUI preview canvas and select an iPhone preview device.

To run a full iOS simulator app, create a small Xcode iOS App target that imports
the local `AtlasUI` package and uses this app entry point:

```swift
import AtlasUI
import SwiftUI

@main
struct AtlasIOSHostApp: App {
    var body: some Scene {
        WindowGroup {
            AtlasRootView()
        }
    }
}
```

Run `job-api` first so the simulator can load real data from
`http://127.0.0.1:8765`.

## Current Prototype

- `AtlasRootView`: iPhone tab shell and Mac three-pane split view.
- `AtlasSearchScreen`: search screen with chips, sort control, result list,
  filter sheet, detail navigation, local-server status, and refresh behavior.
- `AtlasAPIClient`: DTO/client layer for health, search, detail, saved-search,
  updates, and source-summary endpoints.
- `AtlasSearchViewModel`: API-backed search state with sample data limited to
  explicit Xcode previews. It also owns saved searches, saved job posts, source
  summaries, update history, and facet labels.
- `JobResultRow`: divider-row result component matching the Atlas design system.
- `JobDetailView`: detail screen with a leading "Why this matched" panel,
  full server-loaded descriptions, classification evidence, locations, source
  features, raw source data, and server-provided Apply/Source links when
  available.
- Reusable components: `FilterChip`, `ConfidenceDot`, `DeadlinePill`,
  `ScoreRing`, `SourceMonogram`.

## Local API

The default API URL is:

```text
http://127.0.0.1:8765
```

For a physical iPhone, use the Mac LAN address in the app Settings tab, for
example:

```text
http://192.168.50.208:8765
```

Run the server from the repository root:

```bash
uv run --with-editable ./packages/jobagg --with-editable ./services/job-api --module uvicorn job_api.app:app --host 0.0.0.0 --port 8765
```

If the server is not running, the search screen shows an offline banner and an
empty result state. Sample rows are reserved for explicit SwiftUI previews.

## Local Save

The app stores a local search snapshot in Application Support. On launch it shows
the saved snapshot immediately and only refreshes from the API when the snapshot
is older than the user-selected interval. The default interval is 24 hours.

Users can change the interval and force an update from:

```text
Settings -> Local Save
```

Job details are cached opportunistically after opening a vacancy, so previously
opened details remain fast even when the local API is not reachable.

## Local Build Check

```bash
cd apps/apple
swift build
```

To run a standalone Mac preview app:

```bash
cd apps/apple/PreviewHost
swift run AtlasPreviewApp
```

## Planned Shared Client Modules

- Tracker state
- Strategy-fit and assistant run views
