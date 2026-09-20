import Foundation

public enum AtlasIOSAppEntryRoute:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case referenceCapture(AtlasReferenceCaptureMode)
    case invalidReferenceCapture
    case production

    public var description: String {
        "AtlasIOSAppEntryRoute(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

public enum AtlasIOSAppEntryIntegrationError:
    Error,
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    case productionUnavailable

    public var description: String {
        "productionUnavailable"
    }

    public var debugDescription: String {
        description
    }
}

@MainActor
public final class AtlasIOSAppEntryIntegrationPlan:
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private enum ProductionState {
        case notRequested
        case ready(AtlasVaultProductionCompositionHarness)
        case failed
    }

    public let route: AtlasIOSAppEntryRoute

    private let productionFactory:
        @MainActor () throws -> AtlasVaultProductionCompositionHarness
    private var productionState: ProductionState = .notRequested

    public init(
        environment: [String: String],
        productionFactory:
            @escaping @MainActor () throws
                -> AtlasVaultProductionCompositionHarness
    ) {
        route = Self.route(for: environment)
        self.productionFactory = productionFactory
    }

    init(
        environment: [String: String],
        lifecycleSourceFactory:
            @escaping @MainActor ()
                -> any AtlasVaultPlatformLifecycleEventSourcing,
        compositionFactory:
            @escaping @MainActor (
                any AtlasVaultPlatformLifecycleEventSourcing
            ) throws -> AtlasVaultProductionCompositionHarness
    ) {
        route = Self.route(for: environment)
        productionFactory = {
            let lifecycleSource = lifecycleSourceFactory()
            return try compositionFactory(lifecycleSource)
        }
    }

    #if canImport(UIKit)
    public convenience init(
        environment: [String: String],
        configuration: AtlasVaultProductionCompositionConfiguration
    ) {
        self.init(
            environment: environment,
            lifecycleSourceFactory: {
                AtlasIOSProcessLifecycleEventSource()
            },
            compositionFactory: { lifecycleSource in
                try AtlasVaultProductionCompositionFactory
                    .makeUnwiredProductionLike(
                        configuration: configuration,
                        lifecycleEvents: lifecycleSource
                    )
            }
        )
    }
    #endif

    public func productionHarnessIfNeeded()
        throws -> AtlasVaultProductionCompositionHarness?
    {
        guard route == .production else {
            return nil
        }

        switch productionState {
        case let .ready(harness):
            return harness
        case .failed:
            throw AtlasIOSAppEntryIntegrationError.productionUnavailable
        case .notRequested:
            do {
                let harness = try productionFactory()
                productionState = .ready(harness)
                return harness
            } catch {
                productionState = .failed
                throw AtlasIOSAppEntryIntegrationError.productionUnavailable
            }
        }
    }

    public nonisolated var description: String {
        "AtlasIOSAppEntryIntegrationPlan(<redacted>)"
    }

    public nonisolated var debugDescription: String {
        description
    }

    private static func route(
        for environment: [String: String]
    ) -> AtlasIOSAppEntryRoute {
        guard let rawCaptureMode = environment["ATLAS_REFERENCE_CAPTURE"] else {
            return .production
        }
        guard let mode = AtlasReferenceCaptureMode(rawValue: rawCaptureMode) else {
            return .invalidReferenceCapture
        }
        return .referenceCapture(mode)
    }
}
