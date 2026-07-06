import XCTest
@testable import AtlasUI

final class AtlasExternalURLPolicyTests: XCTestCase {
    func testAllowsOnlyBrowserAndMailSchemes() throws {
        for rawValue in [
            "https://jobs.example.org/apply",
            "http://jobs.example.org/source",
            "mailto:jobs@example.org",
            "HTTPS://jobs.example.org/apply"
        ] {
            let url = try XCTUnwrap(URL(string: rawValue))
            XCTAssertTrue(AtlasExternalURLPolicy.isAllowed(url), rawValue)
            XCTAssertEqual(AtlasExternalURLPolicy.safeURL(url), url)
        }

        for rawValue in [
            "javascript:alert(1)",
            "file:///private/tmp/secrets.txt",
            "data:text/html;base64,PGgxPkJhZDwvaDE+",
            "ftp://jobs.example.org/file"
        ] {
            let url = try XCTUnwrap(URL(string: rawValue))
            XCTAssertFalse(AtlasExternalURLPolicy.isAllowed(url), rawValue)
            XCTAssertNil(AtlasExternalURLPolicy.safeURL(url))
        }
    }
}
