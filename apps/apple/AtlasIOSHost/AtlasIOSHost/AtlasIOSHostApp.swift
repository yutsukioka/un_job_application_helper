import AtlasUI
import Foundation
import SwiftUI
import UIKit

@MainActor
private final class AtlasIOSHostProcessDelegate:
    NSObject,
    UIApplicationDelegate
{
    let processOwner: AtlasIOSAppProcessOwner

    override init() {
        let environment = ProcessInfo.processInfo.environment
        processOwner = AtlasIOSAppProcessOwner(
            environment: environment
        )
        super.init()
    }

    func application(
        _: UIApplication,
        didFinishLaunchingWithOptions _:
            [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        processOwner.beginStart()
        return true
    }

    func applicationWillTerminate(_: UIApplication) {
        processOwner.beginTerminalStop()
    }
}

@main
struct AtlasIOSHostApp: App {
    @UIApplicationDelegateAdaptor(
        AtlasIOSHostProcessDelegate.self
    )
    private var processDelegate

    var body: some Scene {
        WindowGroup {
            AtlasIOSIntegratedAppRootView(
                owner: processDelegate.processOwner
            )
        }
    }
}
