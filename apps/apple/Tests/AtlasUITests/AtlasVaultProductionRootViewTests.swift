import SwiftUI
import XCTest
@testable import AtlasUI

@MainActor
final class AtlasVaultProductionRootViewTests: XCTestCase {
    func testIntendedProductionRootCompilesAsSwiftUIView() {
        requireView(AtlasVaultProductionRootView.self)
    }

    private func requireView<Content: View>(_ type: Content.Type) {}
}
