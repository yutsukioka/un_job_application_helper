#if canImport(AppKit)
import AppKit
import Foundation

private final class AtlasMacLifecycleObservation: @unchecked Sendable {
    private let token: NSObjectProtocol

    init(_ token: NSObjectProtocol) {
        self.token = token
    }

    deinit {
        NotificationCenter.default.removeObserver(token)
    }
}

@MainActor
public final class AtlasMacProcessLifecycleEventSource:
    AtlasVaultPlatformLifecycleEventSourcing,
    @unchecked Sendable
{
    private var continuation:
        AsyncStream<AtlasVaultPlatformLifecycleEventDelivery>.Continuation?
    private var observers: [AtlasMacLifecycleObservation] = []
    private var subscribed = false

    public init() {}

    public func subscription() async
        -> AtlasVaultPlatformLifecycleEventSubscription
    {
        guard !subscribed else {
            return AtlasVaultPlatformLifecycleEventSubscription(
                bootstrapEvents: [],
                events: AsyncStream { $0.finish() },
                requestReadinessBoundary: { _ in }
            )
        }
        subscribed = true
        let stream = AsyncStream<AtlasVaultPlatformLifecycleEventDelivery> {
            continuation = $0
        }
        observe(NSApplication.didBecomeActiveNotification, as: .didBecomeActive)
        observe(NSApplication.willResignActiveNotification, as: .willResignActive)
        observe(NSApplication.willTerminateNotification, as: .willTerminate)
        let bootstrap: [AtlasVaultLifecycleEvent] = NSApp.isActive
            ? [.didBecomeActive]
            : [.willResignActive]
        return AtlasVaultPlatformLifecycleEventSubscription(
            bootstrapEvents: bootstrap,
            events: stream,
            requestReadinessBoundary: { [weak self] identifier in
                await MainActor.run {
                    _ = self?.continuation?.yield(
                        .readinessBoundary(identifier)
                    )
                }
            }
        )
    }

    private func observe(
        _ name: Notification.Name,
        as event: AtlasVaultLifecycleEvent
    ) {
        let token = NotificationCenter.default.addObserver(
            forName: name,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                _ = self?.continuation?.yield(.event(event))
            }
        }
        observers.append(AtlasMacLifecycleObservation(token))
    }
}
#endif
