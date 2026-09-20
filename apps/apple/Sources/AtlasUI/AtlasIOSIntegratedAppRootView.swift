import SwiftUI

@MainActor
public struct AtlasIOSIntegratedAppRootView: View {
    @ObservedObject private var owner: AtlasIOSAppProcessOwner

    public init(owner: AtlasIOSAppProcessOwner) {
        self.owner = owner
    }

    @ViewBuilder
    public var body: some View {
        switch owner.presentation {
        case let .referenceCapture(mode):
            AtlasReferenceCaptureView(mode: mode)
        case .invalidReferenceCapture:
            AtlasIOSIntegratedStatusView(
                title: "Reference Capture Unavailable",
                message:
                    "The requested reference-capture mode is invalid.",
                showsProgress: false
            )
        case .productionPending, .productionStarting:
            AtlasIOSIntegratedStatusView(
                title: "Preparing AtlasVault",
                message: "The local service is starting.",
                showsProgress: true
            )
        case .productionReady:
            if let root = productionRootView() {
                root
            } else {
                AtlasIOSIntegratedStatusView(
                    title: "AtlasVault Unavailable",
                    message: "The production view is unavailable.",
                    showsProgress: false
                )
            }
        case .productionUnavailable:
            AtlasIOSIntegratedStatusView(
                title: "AtlasVault Unavailable",
                message: "The production service could not start.",
                showsProgress: false
            )
        case .productionStopping:
            AtlasIOSIntegratedStatusView(
                title: "Stopping AtlasVault",
                message: "The local service is stopping.",
                showsProgress: true
            )
        case .stopped:
            AtlasIOSIntegratedStatusView(
                title: "AtlasVault Stopped",
                message: "The local service has stopped.",
                showsProgress: false
            )
        }
    }

    private func productionRootView()
        -> AtlasVaultProductionRootView?
    {
        owner.productionRootView()
    }
}

private struct AtlasIOSIntegratedStatusView: View {
    let title: String
    let message: String
    let showsProgress: Bool

    var body: some View {
        VStack(spacing: 12) {
            if showsProgress {
                ProgressView()
            }
            Text(title)
                .font(.headline)
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .padding(20)
        .frame(minWidth: 320, minHeight: 240)
        .accessibilityElement(children: .combine)
    }
}
