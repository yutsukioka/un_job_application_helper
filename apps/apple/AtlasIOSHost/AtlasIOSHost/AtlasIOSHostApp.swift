import AtlasUI
import SwiftUI

@main
struct AtlasIOSHostApp: App {
    var body: some Scene {
        WindowGroup {
            if let rawMode = ProcessInfo.processInfo.environment["ATLAS_REFERENCE_CAPTURE"],
               let mode = AtlasReferenceCaptureMode(rawValue: rawMode) {
                AtlasReferenceCaptureView(mode: mode)
            } else {
                AtlasRootView()
            }
        }
    }
}
