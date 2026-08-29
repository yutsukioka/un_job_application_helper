import Foundation
import Synchronization
@testable import AtlasUI
import XCTest

final class AtlasVaultPairingKeyReleaseAuthorizationTests: XCTestCase {
    func testAuthorizationIsEvaluatedFreshAndFailsClosed() async {
        let decisions = Mutex<[Bool]>([false, true])
        let calls = Mutex<Int>(0)
        let authorizer = AtlasApplePairingKeyReleaseAuthorizer(evaluate: {
            calls.withLock { $0 += 1 }
            return decisions.withLock { $0.removeFirst() }
        })

        let denied = await authorizer.authorize()
        let approved = await authorizer.authorize()

        XCTAssertFalse(denied)
        XCTAssertTrue(approved)
        XCTAssertEqual(calls.withLock { $0 }, 2)
    }

    func testProductionAuthorizerUsesDeviceOwnerAuthentication() throws {
        let source = try String(
            contentsOf: sourceURL(),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("import LocalAuthentication"))
        XCTAssertTrue(source.contains("let context = LAContext()"))
        XCTAssertTrue(source.contains(".deviceOwnerAuthentication"))
        XCTAssertTrue(source.contains("canEvaluatePolicy"))
        XCTAssertTrue(source.contains("evaluatePolicy"))
        XCTAssertFalse(source.contains("reuseDuration"))
    }

    private func sourceURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent(
                "Sources/AtlasUI/AtlasVaultPairingKeyReleaseAuthorization.swift"
            )
    }
}
