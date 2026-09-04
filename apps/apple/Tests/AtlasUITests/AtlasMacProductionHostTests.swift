import XCTest
@testable import AtlasUI

@MainActor
final class AtlasMacProductionHostTests: XCTestCase {
    func testProductionOwnerInitializesOnceAndStopsDeterministically() async {
        let harness = MacHarnessFake()
        let owner = AtlasMacAppProcessOwner(productionFactory: { harness })

        async let first = owner.start()
        async let second = owner.start()
        XCTAssertEqual(await first, .productionReady)
        XCTAssertEqual(await second, .productionReady)
        XCTAssertEqual(harness.startCalls, 1)
        XCTAssertNotNil(owner.productionRootView())

        async let firstStop = owner.stop()
        async let secondStop = owner.stop()
        XCTAssertEqual(await firstStop, .stopped)
        XCTAssertEqual(await secondStop, .stopped)
        XCTAssertEqual(harness.stopCalls, 1)
        XCTAssertNil(owner.productionRootView())
    }

    func testMacHostIsSeparateFromPreviewAndUsesNormalAppLifecycle() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let package = try String(
            contentsOf: root.appendingPathComponent("Package.swift"),
            encoding: .utf8
        )
        let host = try String(
            contentsOf: root.appendingPathComponent(
                "Sources/AtlasMacHost/AtlasMacHostApp.swift"
            ),
            encoding: .utf8
        )
        XCTAssertTrue(package.contains("AtlasMacHost"))
        XCTAssertTrue(host.contains("@main"))
        XCTAssertTrue(host.contains("NSApplicationDelegateAdaptor"))
        XCTAssertTrue(host.contains("beginStart()"))
        XCTAssertTrue(host.contains("beginTerminalStop()"))
        XCTAssertFalse(host.contains("AtlasPreviewApp"))
        XCTAssertFalse(host.contains("UserDefaults"))
    }
}

@MainActor
private final class MacHarnessFake: AtlasMacAppProcessHarness {
    private let owner = AtlasVaultProductionPresentationOwner()
    private(set) var startCalls = 0
    private(set) var stopCalls = 0

    func start() async throws -> AtlasLockedShellUnlockFlowState {
        startCalls += 1
        return owner.flowState
    }

    func stop() async -> AtlasLockedShellUnlockFlowState {
        stopCalls += 1
        return owner.flowState
    }

    func makeRootView() -> AtlasVaultProductionRootView {
        AtlasVaultProductionRootView(
            owner: owner,
            publicShellActions: AtlasLockedPublicShellActions(
                search: { _ in },
                requestUnlock: {}
            ),
            unlockActions: AtlasExplicitUnlockViewActions(
                select: { _ in },
                submit: { _ in .failed },
                cancel: {},
                didDisappear: {}
            )
        )
    }
}
