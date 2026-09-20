import Foundation

public protocol AtlasVaultProductionHostBuilding: Sendable {
    /// Returns an inactive host without invoking any dependency.
    func makeHost(
        dependencies: AtlasVaultProductionHostDependencies
    ) -> any AtlasVaultProductionHosting
}

public struct AtlasVaultProductionHostFactory:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    let dependencies: AtlasVaultProductionHostDependencies
    let builder: any AtlasVaultProductionHostBuilding

    public init(
        dependencies: AtlasVaultProductionHostDependencies,
        builder: any AtlasVaultProductionHostBuilding
    ) {
        self.dependencies = dependencies
        self.builder = builder
    }

    public func makeHost() -> any AtlasVaultProductionHosting {
        builder.makeHost(dependencies: dependencies)
    }

    public var description: String {
        "AtlasVaultProductionHostFactory(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}
