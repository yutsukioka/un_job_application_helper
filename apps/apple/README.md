# Apple App

Shared SwiftUI Mac/iOS app prototype for the `jobagg` search experience.

The app uses the HTTP API and must not read SQLite bundles or scrape ATS sites
directly. `AtlasSearchScreen` now talks to `services/job-api` and falls back to
an empty offline state when the local server is unavailable. Fictional preview
rows are used only by explicit Xcode previews.

## Xcode Preview Canvas

Open the package at `apps/apple/Package.swift` from the repository root.

Then open:

```text
Sources/AtlasUI/SearchScreen.swift
```

Select the `AtlasUI` library scheme if Xcode does not pick it automatically.
The root package is intentionally library-only so SwiftUI previews do not need
the executable target debug-dylib setting.

## iOS Simulator

The committed package contains the shared `AtlasUI` library, a Mac preview host,
and a separate iOS host Xcode project. The root package at `apps/apple` is
library-only, so `swift run` from that directory will not launch an app.

To run the iOS app:

1. Open `apps/apple/AtlasIOSHost/AtlasIOSHost.xcodeproj` in Xcode.
2. Select the `AtlasIOSHost` scheme.
3. Choose an iPhone simulator or your connected iPhone.
4. Press Run.

The iOS host app imports the local `AtlasUI` package and uses:

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

For a physical iPhone, use the Mac LAN URL in the app Settings tab and keep
`job-api` running on the Mac.

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
http://<current-mac-ip>:8765
```

Find the current Wi-Fi IP on the Mac with:

```bash
ipconfig getifaddr en0
ipconfig getifaddr en1
```

Use the address that matches the active Wi-Fi network. If you paste a full health
URL such as `http://192.168.50.22:8765/api/health`, the app normalizes it to the
base URL `http://192.168.50.22:8765`.

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

After the list snapshot is saved, the app also backfills full job-detail payloads
for every cached vacancy and saved job. Manual and scheduled local-save refreshes
rewrite those detail files from the current local server. Detail caching is
resumable: if the app is closed or suspended partway through, the next launch
keeps the files already written and fetches only missing detail records. Settings
shows both cached job rows and cached detail rows so it is clear when the local
offline bundle is complete.

## Local Build Check

```bash
cd apps/apple
swift build
```

The command above builds the shared `AtlasUI` package only. It does not launch an
app because `AtlasApple` has no executable product.

To run the standalone Mac preview app from a terminal:

```bash
cd apps/apple/PreviewHost
swift run AtlasPreviewApp
```

If you see an error such as `no executable product named AtlasPreviewApp`, you
are probably still in `apps/apple`; change into `apps/apple/PreviewHost` first.
The terminal remains attached while the app is open. Close the app window or use
`Control-C` in the terminal to stop it.

To validate both app surfaces without launching a GUI:

```bash
cd apps/apple
swift test

cd ../..
xcodebuild -project apps/apple/AtlasIOSHost/AtlasIOSHost.xcodeproj \
  -scheme AtlasIOSHost \
  -destination generic/platform=iOS \
  CODE_SIGNING_ALLOWED=NO build
```

## Planned Shared Client Modules

- Tracker state
- Strategy-fit and assistant run views
