import AppKit
import AtlasUI
import Foundation
import SwiftUI

@MainActor
private final class AtlasMacHostProcessDelegate:
    NSObject,
    NSApplicationDelegate
{
    let processOwner = AtlasMacAppProcessOwner(
        environment: ProcessInfo.processInfo.environment
    )

    func applicationDidFinishLaunching(_: Notification) {
        processOwner.beginStart()
    }

    func applicationShouldTerminate(
        _: NSApplication
    ) -> NSApplication.TerminateReply {
        if processOwner.presentation == .stopped {
            return .terminateNow
        }
        Task { @MainActor [weak self] in
            guard let self else {
                NSApplication.shared.reply(
                    toApplicationShouldTerminate: true
                )
                return
            }
            _ = await processOwner.stop()
            NSApplication.shared.reply(
                toApplicationShouldTerminate: true
            )
        }
        return .terminateLater
    }
}

@main
struct AtlasMacHostApp: App {
    @NSApplicationDelegateAdaptor(AtlasMacHostProcessDelegate.self)
    private var processDelegate

    var body: some Scene {
        WindowGroup {
            AtlasMacIntegratedAppRootView(
                owner: processDelegate.processOwner
            )
        }
    }
}
