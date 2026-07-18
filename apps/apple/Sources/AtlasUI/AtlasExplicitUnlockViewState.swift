import Foundation

public struct AtlasExplicitUnlockViewState:
    Equatable,
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    public let availableMethods: [AtlasVaultUnlockMethod]
    public let selectedMethod: AtlasVaultUnlockMethod?
    public let status: AtlasVaultUnlockPresentationStatus
    public let message: String
    public let controlsDisabled: Bool
    public let requiresInputClear: Bool

    public init(presentationState: AtlasVaultUnlockPresentationState) {
        let availableMethods = presentationState.capabilities.availableMethods
        self.availableMethods = availableMethods
        self.selectedMethod = presentationState.selectedMethod.flatMap {
            availableMethods.contains($0) ? $0 : nil
        }
        self.status = presentationState.status

        switch presentationState.status {
        case .locked:
            message = "Choose an unlock method."
            controlsDisabled = false
            requiresInputClear = true
        case .ready:
            message = "Ready to unlock."
            controlsDisabled = false
            requiresInputClear = false
        case .methodUnavailable:
            message = "This unlock method is unavailable."
            controlsDisabled = false
            requiresInputClear = true
        case .activating:
            message = "Unlocking vault."
            controlsDisabled = true
            requiresInputClear = false
        case .unlocked:
            message = "Vault unlocked."
            controlsDisabled = true
            requiresInputClear = true
        case .failed:
            message = "Unable to unlock the vault."
            controlsDisabled = false
            requiresInputClear = true
        case .cancelled:
            message = "Unlock cancelled."
            controlsDisabled = false
            requiresInputClear = true
        case .timedOut:
            message = "Unlock request timed out."
            controlsDisabled = false
            requiresInputClear = true
        case .hostReconciliationRequired:
            message = "Host reconciliation is required."
            controlsDisabled = true
            requiresInputClear = true
        }
    }

    public var showsLocalKeyAction: Bool {
        availableMethods.contains(.localKey)
    }

    public var showsPassphraseInput: Bool {
        selectedMethod == .passphrase
            && availableMethods.contains(.passphrase)
    }

    public var showsRecoveryKeyInput: Bool {
        selectedMethod == .recoveryKey
            && availableMethods.contains(.recoveryKey)
    }

    public var permitsSubmission: Bool {
        statusPermitsSubmission
            && selectedMethod.map(availableMethods.contains) == true
            && !controlsDisabled
    }

    public var shouldNotifyDisappearance: Bool {
        status != .unlocked
    }

    public var description: String {
        "AtlasExplicitUnlockViewState(status: \(status), content: <redacted>)"
    }

    public var debugDescription: String {
        description
    }

    private var statusPermitsSubmission: Bool {
        switch status {
        case .ready, .failed, .cancelled, .timedOut:
            true
        case .locked,
             .methodUnavailable,
             .activating,
             .unlocked,
             .hostReconciliationRequired:
            false
        }
    }
}

struct AtlasExplicitUnlockInputDraft:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    var passphrase: String
    var recoveryKey: String

    init(
        passphrase: String = "",
        recoveryKey: String = ""
    ) {
        self.passphrase = passphrase
        self.recoveryKey = recoveryKey
    }

    var isEmpty: Bool {
        passphrase.isEmpty && recoveryKey.isEmpty
    }

    mutating func clear() {
        passphrase = ""
        recoveryKey = ""
    }

    mutating func consume(
        for method: AtlasVaultUnlockMethod,
        state: AtlasExplicitUnlockViewState
    ) -> AtlasVaultUnlockSubmission? {
        guard
            state.availableMethods.contains(method),
            !state.controlsDisabled
        else {
            clear()
            return nil
        }

        switch method {
        case .localKey:
            clear()
            return .localKey
        case .passphrase:
            guard
                state.selectedMethod == .passphrase,
                state.permitsSubmission,
                !passphrase.isEmpty
            else {
                clear()
                return nil
            }
            let bytes = Data(passphrase.utf8)
            clear()
            return .passphrase(
                AtlasVaultInMemorySecretBuffer(bytes: bytes)
            )
        case .recoveryKey:
            guard
                state.selectedMethod == .recoveryKey,
                state.permitsSubmission,
                !recoveryKey.isEmpty
            else {
                clear()
                return nil
            }
            let bytes = Data(recoveryKey.utf8)
            clear()
            return .recoveryKey(
                AtlasVaultInMemorySecretBuffer(bytes: bytes)
            )
        }
    }

    var description: String {
        "AtlasExplicitUnlockInputDraft(<redacted>)"
    }

    var debugDescription: String {
        description
    }
}

public struct AtlasExplicitUnlockViewActions:
    Sendable,
    CustomStringConvertible,
    CustomDebugStringConvertible
{
    private let selectAction:
        @Sendable (AtlasVaultUnlockMethod?) async -> Void
    private let submitAction:
        @Sendable (
            AtlasVaultUnlockSubmission
        ) async -> AtlasVaultUnlockPresentationStatus
    private let cancelAction: @Sendable () async -> Void
    private let disappearanceAction: @Sendable () async -> Void
    private let disappearanceAuthorization =
        AtlasExplicitUnlockDisappearanceAuthorization()

    public init(
        select: @escaping @Sendable (
            AtlasVaultUnlockMethod?
        ) async -> Void,
        submit: @escaping @Sendable (
            AtlasVaultUnlockSubmission
        ) async -> AtlasVaultUnlockPresentationStatus,
        cancel: @escaping @Sendable () async -> Void,
        didDisappear: @escaping @Sendable () async -> Void
    ) {
        selectAction = select
        submitAction = submit
        cancelAction = cancel
        disappearanceAction = didDisappear
    }

    public func select(_ method: AtlasVaultUnlockMethod?) async {
        await selectAction(method)
    }

    public func submit(
        _ submission: AtlasVaultUnlockSubmission
    ) async -> AtlasVaultUnlockPresentationStatus {
        let identifier = await disappearanceAuthorization.beginSubmission()
        let status = await submitAction(submission)
        await disappearanceAuthorization.finishSubmission(
            identifier,
            status: status
        )
        return status
    }

    public func cancel() async {
        await cancelAction()
    }

    public func didDisappear() async {
        guard await disappearanceAuthorization.shouldNotifyDisappearance() else {
            return
        }
        await disappearanceAction()
    }

    public var description: String {
        "AtlasExplicitUnlockViewActions(<redacted>)"
    }

    public var debugDescription: String {
        description
    }
}

private actor AtlasExplicitUnlockDisappearanceAuthorization {
    private var activeSubmissions: Set<UUID> = []
    private var committedUnlock = false
    private var suppressNextDisappearance = false
    private var disappearanceWaiters: [
        CheckedContinuation<Bool, Never>
    ] = []

    func beginSubmission() -> UUID {
        let identifier = UUID()
        if activeSubmissions.isEmpty {
            committedUnlock = false
        }
        activeSubmissions.insert(identifier)
        suppressNextDisappearance = false
        return identifier
    }

    func finishSubmission(
        _ identifier: UUID,
        status: AtlasVaultUnlockPresentationStatus
    ) {
        guard activeSubmissions.remove(identifier) != nil else {
            return
        }
        committedUnlock = committedUnlock || status == .unlocked
        guard activeSubmissions.isEmpty else {
            return
        }

        let shouldNotify = !committedUnlock
        let waiters = disappearanceWaiters
        disappearanceWaiters.removeAll()
        suppressNextDisappearance = committedUnlock && waiters.isEmpty
        committedUnlock = false
        for waiter in waiters {
            waiter.resume(returning: shouldNotify)
        }
    }

    func shouldNotifyDisappearance() async -> Bool {
        if !activeSubmissions.isEmpty {
            return await withCheckedContinuation { continuation in
                disappearanceWaiters.append(continuation)
            }
        }
        if suppressNextDisappearance {
            suppressNextDisappearance = false
            return false
        }
        return true
    }
}

struct AtlasExplicitUnlockSubmissionGate: Sendable {
    private var activeID: UUID?

    var isActive: Bool {
        activeID != nil
    }

    mutating func begin() -> UUID? {
        guard activeID == nil else {
            return nil
        }
        let identifier = UUID()
        activeID = identifier
        return identifier
    }

    mutating func finish(_ identifier: UUID) {
        guard activeID == identifier else {
            return
        }
        activeID = nil
    }

    mutating func cancel() {
        activeID = nil
    }
}

extension AtlasVaultUnlockSubmission {
    func clearExplicitUnlockSecret() async {
        switch self {
        case .localKey:
            return
        case let .passphrase(buffer), let .recoveryKey(buffer):
            await buffer.clear()
        }
    }
}
