import Foundation
import XCTest

@testable import AtlasUI

final class AtlasVaultDeviceRemovalAuthorizationTests: XCTestCase {
  @MainActor func testFreshDeviceRemovalAuthorizationNeverCaches() async {
    var calls = 0
    let authorizer = AtlasAppleDeviceRemovalAuthorizer(evaluate: {
      calls += 1
      return calls > 1
    })
    let denied = await authorizer.authorize()
    let first = await authorizer.authorize()
    let second = await authorizer.authorize()
    XCTAssertFalse(denied)
    XCTAssertTrue(first)
    XCTAssertTrue(second)
    XCTAssertEqual(calls, 3)
    XCTAssertEqual(String(describing: authorizer), "AtlasAppleDeviceRemovalAuthorizer(<redacted>)")
  }
}
