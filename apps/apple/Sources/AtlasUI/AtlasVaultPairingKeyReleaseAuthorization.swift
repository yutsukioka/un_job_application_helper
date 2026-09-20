import Foundation
import LocalAuthentication

public struct AtlasApplePairingKeyReleaseAuthorizer:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let evaluate: @Sendable () async -> Bool

    public init() {
        evaluate = { await Self.evaluateFresh() }
    }

    init(evaluate: @escaping @Sendable () async -> Bool) {
        self.evaluate = evaluate
    }

    public func authorize() async -> Bool {
        await evaluate()
    }

    public var description: String {
        "AtlasApplePairingKeyReleaseAuthorizer(<redacted>)"
    }

    public var debugDescription: String { description }

    @MainActor
    private static func evaluateFresh() async -> Bool {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthentication,
            error: &error
        ) else {
            return false
        }
        do {
            return try await context.evaluatePolicy(
                .deviceOwnerAuthentication,
                localizedReason:
                    "Authorize AtlasVault vault-key delivery"
            )
        } catch {
            return false
        }
    }
}
