import SwiftUI

@MainActor
public struct AtlasMacIntegratedAppRootView: View {
    @ObservedObject private var owner: AtlasMacAppProcessOwner

    public init(owner: AtlasMacAppProcessOwner) {
        self.owner = owner
    }

    @ViewBuilder
    public var body: some View {
        switch owner.presentation {
        case .productionPending, .productionStarting:
            status("Preparing AtlasVault", progress: true)
        case .productionReady:
            if let root = owner.productionRootView() {
                root
            } else {
                status("AtlasVault Unavailable", progress: false)
            }
        case .productionUnavailable:
            status("AtlasVault Unavailable", progress: false)
        case .productionStopping:
            status("Stopping AtlasVault", progress: true)
        case .stopped:
            status("AtlasVault Stopped", progress: false)
        }
    }

    private func status(_ title: String, progress: Bool) -> some View {
        VStack(spacing: 12) {
            if progress { ProgressView() }
            Text(title).font(.headline)
            Text("Private data remains protected.")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .padding(20)
        .frame(minWidth: 520, minHeight: 360)
        .accessibilityElement(children: .combine)
    }
}
