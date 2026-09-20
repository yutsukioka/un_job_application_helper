import Combine
import Foundation

@MainActor
protocol AtlasIOSAppProcessHarness: AnyObject {
    func start() async throws -> AtlasLockedShellUnlockFlowState
    func stop() async -> AtlasLockedShellUnlockFlowState
    func makeRootView() -> AtlasVaultProductionRootView
}

extension AtlasVaultProductionCompositionHarness:
    AtlasIOSAppProcessHarness
{}

public enum AtlasIOSAppProcessPresentation:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case referenceCapture(AtlasReferenceCaptureMode)
    case invalidReferenceCapture
    case productionPending
    case productionStarting
    case productionReady
    case productionUnavailable
    case productionStopping
    case stopped

    public var description: String {
        "AtlasIOSAppProcessPresentation(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

enum AtlasIOSAppProcessOwnerError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case productionUnavailable

    var description: String {
        "productionUnavailable"
    }

    var debugDescription: String {
        description
    }
}

@MainActor
public final class AtlasIOSAppProcessOwner:
    ObservableObject,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum Lifetime {
        case available
        case unavailable
        case terminal
    }

    private struct StartOperation {
        let identifier: UUID
        let task: Task<AtlasIOSAppProcessPresentation, Never>
    }

    private struct StopOperation {
        let identifier: UUID
        let task: Task<AtlasIOSAppProcessPresentation, Never>
    }

    @Published
    public private(set) var presentation:
        AtlasIOSAppProcessPresentation

    private let route: AtlasIOSAppEntryRoute
    private var productionFactory:
        (@MainActor () throws -> any AtlasIOSAppProcessHarness)?
    private var retainedHarness: (any AtlasIOSAppProcessHarness)?
    private var startOperation: StartOperation?
    private var stopOperation: StopOperation?
    private var terminalStopRequested = false
    private var lifetime: Lifetime = .available

    init(
        plan: AtlasIOSAppEntryIntegrationPlan,
        productionFactory:
            @escaping @MainActor () throws
                -> any AtlasIOSAppProcessHarness
    ) {
        route = plan.route
        presentation = Self.initialPresentation(for: plan.route)
        if plan.route == .production {
            self.productionFactory = productionFactory
        } else {
            self.productionFactory = nil
        }
    }

    init(
        route: AtlasIOSAppEntryRoute,
        productionFactory:
            @escaping @MainActor () throws
                -> any AtlasIOSAppProcessHarness
    ) {
        self.route = route
        presentation = Self.initialPresentation(for: route)
        self.productionFactory = route == .production
            ? productionFactory
            : nil
    }

    #if canImport(UIKit)
    public convenience init(environment: [String: String]) {
        let productionFactory:
            @MainActor () throws
                -> AtlasVaultProductionCompositionHarness = {
            let configuration = try Self.productionConfiguration(
                environment: environment
            )
            let lifecycleSource =
                AtlasIOSProcessLifecycleEventSource()
            return try AtlasVaultProductionCompositionFactory
                .makeUnwiredProductionLike(
                    configuration: configuration,
                    lifecycleEvents: lifecycleSource
                )
        }
        let plan = AtlasIOSAppEntryIntegrationPlan(
            environment: environment,
            productionFactory: productionFactory
        )
        self.init(
            plan: plan,
            productionFactory: {
                try productionFactory()
            }
        )
    }
    #endif

    public func beginStart() {
        guard route == .production,
              !terminalStopRequested,
              lifetime == .available else {
            return
        }
        _ = installStartOperationIfNeeded()
    }

    public func start() async -> AtlasIOSAppProcessPresentation {
        guard route == .production else {
            return presentation
        }
        guard !terminalStopRequested,
              lifetime != .terminal else {
            return presentation
        }
        if let startOperation {
            return await startOperation.task.value
        }
        guard lifetime == .available,
              let operation = installStartOperationIfNeeded() else {
            return presentation
        }
        return await operation.task.value
    }

    public func beginTerminalStop() {
        guard stopOperation == nil else {
            return
        }

        terminalStopRequested = true
        productionFactory = nil
        switch presentation {
        case .productionPending,
             .productionStarting,
             .productionReady,
             .productionUnavailable:
            presentation = .productionStopping
        case .referenceCapture,
             .invalidReferenceCapture,
             .productionStopping,
             .stopped:
            break
        }

        let identifier = UUID()
        let task = Task { @MainActor [weak self] in
            guard let self else {
                return AtlasIOSAppProcessPresentation.stopped
            }
            return await self.performStop(identifier: identifier)
        }
        stopOperation = StopOperation(
            identifier: identifier,
            task: task
        )
    }

    public func stop() async -> AtlasIOSAppProcessPresentation {
        if let stopOperation {
            return await stopOperation.task.value
        }
        beginTerminalStop()
        if let stopOperation {
            return await stopOperation.task.value
        }
        presentation = .stopped
        lifetime = .terminal
        return presentation
    }

    public func productionRootView()
        -> AtlasVaultProductionRootView?
    {
        guard presentation == .productionReady else {
            return nil
        }
        return retainedHarness?.makeRootView()
    }

    public nonisolated var description: String {
        "AtlasIOSAppProcessOwner(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    static func productionConfiguration(
        environment: [String: String]
    ) throws -> AtlasVaultProductionCompositionConfiguration {
        let rawURL = environment["ATLAS_API_BASE_URL"]
            ?? "http://127.0.0.1:8765"
        guard let apiBaseURL = URL(string: rawURL) else {
            throw AtlasIOSAppProcessOwnerError.productionUnavailable
        }
        do {
            return try AtlasVaultProductionCompositionConfiguration(
                apiBaseURL: apiBaseURL,
                publicSearchLimit: 50,
                unlockTimeout: .seconds(30),
                lifecycleLockPolicy: .immediate,
                lockOnInactive: true
            )
        } catch {
            throw AtlasIOSAppProcessOwnerError.productionUnavailable
        }
    }

    private static func initialPresentation(
        for route: AtlasIOSAppEntryRoute
    ) -> AtlasIOSAppProcessPresentation {
        switch route {
        case let .referenceCapture(mode):
            .referenceCapture(mode)
        case .invalidReferenceCapture:
            .invalidReferenceCapture
        case .production:
            .productionPending
        }
    }

    private func installStartOperationIfNeeded()
        -> StartOperation?
    {
        if let startOperation {
            return startOperation
        }
        guard route == .production,
              !terminalStopRequested,
              lifetime == .available else {
            return nil
        }

        let identifier = UUID()
        let task = Task { @MainActor [weak self] in
            guard let self else {
                return AtlasIOSAppProcessPresentation.stopped
            }
            return await self.performStart(identifier: identifier)
        }
        let operation = StartOperation(
            identifier: identifier,
            task: task
        )
        startOperation = operation
        return operation
    }

    private func performStart(
        identifier: UUID
    ) async -> AtlasIOSAppProcessPresentation {
        guard startOperation?.identifier == identifier,
              !terminalStopRequested,
              lifetime == .available else {
            return presentation
        }

        presentation = .productionStarting

        let harness: any AtlasIOSAppProcessHarness
        do {
            guard let factory = productionFactory else {
                throw AtlasIOSAppProcessOwnerError.productionUnavailable
            }
            productionFactory = nil
            harness = try factory()
        } catch {
            guard !terminalStopRequested,
                  startOperation?.identifier == identifier else {
                return presentation
            }
            lifetime = .unavailable
            presentation = .productionUnavailable
            return presentation
        }

        retainedHarness = harness
        guard !terminalStopRequested,
              startOperation?.identifier == identifier else {
            return presentation
        }

        do {
            _ = try await harness.start()
        } catch {
            guard !terminalStopRequested,
                  startOperation?.identifier == identifier else {
                return presentation
            }
            lifetime = .unavailable
            presentation = .productionUnavailable
            return presentation
        }

        guard !terminalStopRequested,
              startOperation?.identifier == identifier,
              lifetime == .available else {
            return presentation
        }
        presentation = .productionReady
        return presentation
    }

    private func performStop(
        identifier: UUID
    ) async -> AtlasIOSAppProcessPresentation {
        if let retainedHarness {
            _ = await retainedHarness.stop()
            self.retainedHarness = nil
        }
        if let startOperation {
            _ = await startOperation.task.value
        }

        guard stopOperation?.identifier == identifier else {
            return presentation
        }
        lifetime = .terminal
        presentation = .stopped
        return presentation
    }
}
