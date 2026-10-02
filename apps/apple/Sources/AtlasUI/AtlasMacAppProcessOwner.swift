import Combine
import Foundation

@MainActor
protocol AtlasMacAppProcessHarness: AnyObject {
    func start() async throws -> AtlasLockedShellUnlockFlowState
    func stop() async -> AtlasLockedShellUnlockFlowState
    func makeRootView() -> AtlasVaultProductionRootView
}

extension AtlasVaultProductionCompositionHarness: AtlasMacAppProcessHarness {}

public enum AtlasMacAppProcessPresentation: Equatable, Sendable {
    case productionPending
    case productionStarting
    case productionReady
    case productionUnavailable
    case productionStopping
    case stopped
}

@MainActor
public final class AtlasMacAppProcessOwner: ObservableObject {
    private struct StartOperation {
        let id: UUID
        let task: Task<AtlasMacAppProcessPresentation, Never>
    }

    private struct StopOperation {
        let id: UUID
        let task: Task<AtlasMacAppProcessPresentation, Never>
    }

    @Published
    public private(set) var presentation: AtlasMacAppProcessPresentation =
        .productionPending

    private var productionFactory:
        (@MainActor () throws -> any AtlasMacAppProcessHarness)?
    private var harness: (any AtlasMacAppProcessHarness)?
    private var startOperation: StartOperation?
    private var stopOperation: StopOperation?
    private var terminalStopRequested = false

    init(
        productionFactory:
            @escaping @MainActor () throws -> any AtlasMacAppProcessHarness
    ) {
        self.productionFactory = productionFactory
    }

    #if canImport(AppKit)
    public convenience init(environment: [String: String]) {
        self.init {
            let configuration = try Self.productionConfiguration(
                environment: environment
            )
            return try AtlasVaultProductionCompositionFactory
                .makeUnwiredProductionLike(
                    configuration: configuration,
                    lifecycleEvents: AtlasMacProcessLifecycleEventSource()
                )
        }
    }
    #endif

    public func beginStart() {
        guard !terminalStopRequested else { return }
        _ = installStartOperation()
    }

    public func start() async -> AtlasMacAppProcessPresentation {
        if let startOperation {
            return await startOperation.task.value
        }
        guard !terminalStopRequested else { return presentation }
        return await installStartOperation().task.value
    }

    public func beginTerminalStop() {
        guard stopOperation == nil else { return }
        terminalStopRequested = true
        productionFactory = nil
        if presentation != .stopped {
            presentation = .productionStopping
        }
        let id = UUID()
        let task = Task<AtlasMacAppProcessPresentation, Never> {
            @MainActor [weak self] in
            guard let self else {
                return AtlasMacAppProcessPresentation.stopped
            }
            return await self.performStop(id: id)
        }
        stopOperation = StopOperation(id: id, task: task)
    }

    public func stop() async -> AtlasMacAppProcessPresentation {
        if let stopOperation {
            return await stopOperation.task.value
        }
        beginTerminalStop()
        return await stopOperation?.task.value ?? .stopped
    }

    public func productionRootView() -> AtlasVaultProductionRootView? {
        guard presentation == .productionReady else { return nil }
        return harness?.makeRootView()
    }

    private func installStartOperation() -> StartOperation {
        if let startOperation { return startOperation }
        let id = UUID()
        let task = Task<AtlasMacAppProcessPresentation, Never> {
            @MainActor [weak self] in
            guard let self else {
                return AtlasMacAppProcessPresentation.stopped
            }
            return await self.performStart(id: id)
        }
        let operation = StartOperation(id: id, task: task)
        startOperation = operation
        return operation
    }

    private func performStart(id: UUID) async -> AtlasMacAppProcessPresentation {
        guard startOperation?.id == id, !terminalStopRequested else {
            return presentation
        }
        presentation = .productionStarting
        do {
            guard let factory = productionFactory else {
                throw AtlasVaultProductionCompositionError.startUnavailable
            }
            productionFactory = nil
            let candidate = try factory()
            harness = candidate
            _ = try await candidate.start()
            guard startOperation?.id == id, !terminalStopRequested else {
                return presentation
            }
            presentation = .productionReady
        } catch {
            if !terminalStopRequested {
                presentation = .productionUnavailable
            }
        }
        return presentation
    }

    private func performStop(id: UUID) async -> AtlasMacAppProcessPresentation {
        if let startOperation {
            _ = await startOperation.task.value
        }
        if let harness {
            _ = await harness.stop()
            self.harness = nil
        }
        guard stopOperation?.id == id else { return presentation }
        presentation = .stopped
        return presentation
    }

    private static func productionConfiguration(
        environment: [String: String]
    ) throws -> AtlasVaultProductionCompositionConfiguration {
        let rawURL = environment["ATLAS_API_BASE_URL"]
            ?? "http://127.0.0.1:8765"
        guard let apiBaseURL = URL(string: rawURL) else {
            throw AtlasVaultProductionCompositionError.invalidAPIBaseURL
        }
        return try AtlasVaultProductionCompositionConfiguration(
            apiBaseURL: apiBaseURL,
            publicSearchLimit: 50,
            unlockTimeout: .seconds(30),
            lifecycleLockPolicy: .immediate,
            lockOnInactive: true
        )
    }
}
