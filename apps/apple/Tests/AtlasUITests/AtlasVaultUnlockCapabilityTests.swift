import Foundation
import XCTest
@testable import AtlasUI

final class AtlasVaultUnlockCapabilityTests: XCTestCase {
    func testCurrentProductionCapabilitiesAdvertiseOnlyLocalKey() {
        let capabilities = AtlasVaultUnlockCapabilities.currentProduction

        XCTAssertEqual(capabilities.status(for: .localKey), .available)
        XCTAssertEqual(capabilities.status(for: .passphrase), .unavailable)
        XCTAssertEqual(capabilities.status(for: .recoveryKey), .unavailable)
        XCTAssertEqual(capabilities.availableMethods, [.localKey])
    }

    func testProviderPresenceControlsWrappedKeyCapabilitiesWithoutInvokingProvider() async {
        let passphraseProvider = CapabilityNeverCalledUnwrapper()
        let recoveryProvider = CapabilityNeverCalledUnwrapper()

        let capabilities = AtlasVaultUnlockCapabilities(
            localKeyAvailable: false,
            passphraseProvider: passphraseProvider,
            recoveryKeyProvider: recoveryProvider
        )
        let passphraseCalls = await passphraseProvider.callCount
        let recoveryCalls = await recoveryProvider.callCount

        XCTAssertEqual(capabilities.status(for: .localKey), .unavailable)
        XCTAssertEqual(capabilities.status(for: .passphrase), .available)
        XCTAssertEqual(capabilities.status(for: .recoveryKey), .available)
        XCTAssertEqual(passphraseCalls, 0)
        XCTAssertEqual(recoveryCalls, 0)
    }

    func testAbsentProviderKeepsProductionWrappedKeyMethodsUnavailable() {
        let capabilities = AtlasVaultUnlockCapabilities(
            localKeyAvailable: true,
            passphraseProvider: nil,
            recoveryKeyProvider: nil
        )

        XCTAssertEqual(capabilities.availableMethods, [.localKey])
    }

    func testProductionMethodModelHasNoRawKeyCapability() {
        XCTAssertEqual(
            Set(AtlasVaultUnlockMethod.allCases.map(\.rawValue)),
            ["local_key", "passphrase", "recovery_key"]
        )
        XCTAssertFalse(AtlasVaultUnlockMethod.allCases.map(\.rawValue).contains("supplied_key"))
        XCTAssertFalse(AtlasVaultUnlockMethod.allCases.map(\.rawValue).contains("raw_key"))
    }

    func testCapabilityAndErrorDescriptionsAreNonSensitive() {
        let sentinel = "FAKE_SECRET_CAPABILITY_SENTINEL_DO_NOT_LEAK"
        let capabilities = AtlasVaultUnlockCapabilities.currentProduction
        let errors: [AtlasVaultKeyUnwrapError] = [
            .invalidContext,
            .unsupportedFormat,
            .unsupportedMethod,
            .providerUnavailable,
            .invalidSecret,
            .invalidKeyLength,
            .unwrapFailed,
        ]

        XCTAssertFalse(String(describing: capabilities).contains(sentinel))
        XCTAssertEqual(String(reflecting: capabilities), "AtlasVaultUnlockCapabilities(<redacted>)")
        for error in errors {
            XCTAssertFalse(error.description.contains(sentinel))
            XCTAssertEqual(error.debugDescription, error.description)
        }
    }

    func testValidatedProviderResultRequiresThirtyTwoByteKey() async throws {
        let context = try await capabilityContext()
        let valid = CapabilityResultUnwrapper(result: .success(Data(repeating: 7, count: 32)))
        let invalid = CapabilityResultUnwrapper(result: .success(Data(repeating: 7, count: 31)))
        let validBuffer = AtlasVaultInMemorySecretBuffer(bytes: Data("fake".utf8))
        let invalidBuffer = AtlasVaultInMemorySecretBuffer(bytes: Data("fake".utf8))

        let key = try await valid.validatedVaultKey(
            context: context,
            secret: validBuffer
        )
        XCTAssertEqual(key.count, 32)
        await assertEventuallyCleared(validBuffer)

        do {
            _ = try await invalid.validatedVaultKey(
                context: context,
                secret: invalidBuffer
            )
            XCTFail("Expected invalid key length")
        } catch {
            XCTAssertEqual(error as? AtlasVaultKeyUnwrapError, .invalidKeyLength)
        }
        await assertEventuallyCleared(invalidBuffer)
    }

    func testValidatedProviderWipesRejectedKeyMaterialBeforeThrowing() async throws {
        let context = try await capabilityContext()
        let storage = CapabilityTrackedInvalidKeyStorage(byteCount: 31)
        let provider = CapabilityTrackedInvalidKeyUnwrapper(storage: storage)
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Data("fake".utf8))

        do {
            _ = try await provider.validatedVaultKey(
                context: context,
                secret: buffer
            )
            XCTFail("Expected invalid key length")
        } catch {
            XCTAssertEqual(error as? AtlasVaultKeyUnwrapError, .invalidKeyLength)
        }

        XCTAssertTrue(storage.allBytesAreZero)
        let isCleared = await buffer.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testValidatedProviderResultRedactsUnknownProviderFailure() async throws {
        let context = try await capabilityContext()
        let provider = CapabilityResultUnwrapper(
            result: .failure(CapabilityPrivateProviderError.secret("FAKE_PROVIDER_SECRET"))
        )
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Data("fake".utf8))

        do {
            _ = try await provider.validatedVaultKey(
                context: context,
                secret: buffer
            )
            XCTFail("Expected unwrap failure")
        } catch {
            XCTAssertEqual(error as? AtlasVaultKeyUnwrapError, .unwrapFailed)
            XCTAssertFalse(String(describing: error).contains("FAKE_PROVIDER_SECRET"))
        }
        await assertEventuallyCleared(buffer)
    }

    func testValidatedProviderWaitsForSecretCleanupBeforeReturning() async throws {
        let context = try await capabilityContext()
        let provider = CapabilityResultUnwrapper(
            result: .success(Data(repeating: 7, count: 32))
        )
        let clearGate = CapabilityClearGate()
        let buffer = CapabilityGatedClearSecretBuffer(
            bytes: Data("fake".utf8),
            clearGate: clearGate
        )
        let completion = CapabilityCompletionFlag()

        let operation = Task {
            do {
                let key = try await provider.validatedVaultKey(
                    context: context,
                    secret: buffer
                )
                await completion.markCompleted()
                return key
            } catch {
                await completion.markCompleted()
                throw error
            }
        }

        await buffer.waitUntilClearStarted()
        let completedBeforeClear = await completion.isCompleted
        XCTAssertFalse(completedBeforeClear)

        await clearGate.open()
        let key = try await operation.value
        XCTAssertEqual(key.count, 32)
        let isCleared = await buffer.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testValidatedProviderPreservesCancellationAfterSecretCleanup() async throws {
        let context = try await capabilityContext()
        let provider = CapabilityCancellationUnwrapper()
        let clearGate = CapabilityClearGate()
        let buffer = CapabilityGatedClearSecretBuffer(
            bytes: Data("fake".utf8),
            clearGate: clearGate
        )
        let completion = CapabilityCompletionFlag()

        let operation = Task {
            do {
                _ = try await provider.validatedVaultKey(
                    context: context,
                    secret: buffer
                )
                await completion.markCompleted()
                XCTFail("Expected cancellation")
            } catch {
                await completion.markCompleted()
                guard error is CancellationError else {
                    XCTFail("Expected CancellationError, got \(error)")
                    return
                }
            }
        }

        await buffer.waitUntilClearStarted()
        let completedBeforeClear = await completion.isCompleted
        XCTAssertFalse(completedBeforeClear)

        await clearGate.open()
        await operation.value
        let isCleared = await buffer.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testValidatedProviderMapsUnavailableSecretBufferToInvalidSecret() async throws {
        let context = try await capabilityContext()
        let provider = CapabilitySecretTakingUnwrapper()
        let buffer = AtlasVaultInMemorySecretBuffer(bytes: Data("fake".utf8))
        await buffer.clear()

        do {
            _ = try await provider.validatedVaultKey(
                context: context,
                secret: buffer
            )
            XCTFail("Expected invalid secret")
        } catch {
            XCTAssertEqual(error as? AtlasVaultKeyUnwrapError, .invalidSecret)
        }
        let isCleared = await buffer.isClearedForTesting
        XCTAssertTrue(isCleared)
    }

    func testCapabilityTypesAreSendableAndNotCodable() {
        assertSendable(AtlasVaultUnlockMethod.self)
        assertSendable(AtlasVaultUnlockCapabilityStatus.self)
        assertSendable(AtlasVaultUnlockCapabilities.self)
        XCTAssertFalse(AtlasVaultUnlockCapabilities.self is any Encodable.Type)
        XCTAssertFalse(AtlasVaultUnlockCapabilities.self is any Decodable.Type)
    }

    private func assertSendable<T: Sendable>(_ type: T.Type) {}

    private func assertEventuallyCleared(
        _ buffer: AtlasVaultInMemorySecretBuffer,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        for _ in 0..<100 {
            if await buffer.isClearedForTesting {
                return
            }
            await Task.yield()
        }
        XCTFail("Expected secret buffer cleanup", file: file, line: line)
    }

    private func capabilityContext() async throws -> AtlasVaultKeyUnwrapContext {
        let json = Data("""
        {
          "id": "primary-passphrase",
          "type": "passphrase",
          "kdf": {
            "algorithm": "Argon2id",
            "salt": "IiIiIiIiIiIiIiIiIiIiIg==",
            "memory_kib": 1024,
            "iterations": 2,
            "parallelism": 1
          },
          "nonce": "MzMzMzMzMzMzMzMz",
          "ciphertext": "JJzE300uvWP/iqioMFTRANtsnhXearJAsujEbtWYY1SyRNBUfZu+5bhYcHZvX87L"
        }
        """.utf8)
        let wrapped = try JSONDecoder().decode(AtlasVaultWrappedKeyEnvelope.self, from: json)
        return try AtlasVaultKeyUnwrapContext(
            vaultID: "00000000-0000-4000-8000-000000000500",
            wrappedKey: wrapped
        )
    }
}

private actor CapabilityNeverCalledUnwrapper: AtlasVaultKeyUnwrapping {
    private(set) var callCount = 0

    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        callCount += 1
        return Data(repeating: 0, count: 32)
    }
}

private struct CapabilityResultUnwrapper: AtlasVaultKeyUnwrapping {
    let result: Result<Data, CapabilityPrivateProviderError>

    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        try result.get()
    }
}

private enum CapabilityPrivateProviderError: Error, Sendable {
    case secret(String)
}

private struct CapabilityCancellationUnwrapper: AtlasVaultKeyUnwrapping {
    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        throw CancellationError()
    }
}

private struct CapabilitySecretTakingUnwrapper: AtlasVaultKeyUnwrapping {
    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        _ = try await secret.takeSecretBytes()
        return Data(repeating: 7, count: 32)
    }
}

private struct CapabilityTrackedInvalidKeyUnwrapper: AtlasVaultKeyUnwrapping {
    let storage: CapabilityTrackedInvalidKeyStorage

    func unwrapVaultKey(
        context: AtlasVaultKeyUnwrapContext,
        secret: any AtlasVaultSecretBuffer
    ) async throws -> Data {
        storage.makeData()
    }
}

private final class CapabilityTrackedInvalidKeyStorage: @unchecked Sendable {
    private let pointer: UnsafeMutableRawPointer
    private let byteCount: Int

    init(byteCount: Int) {
        self.byteCount = byteCount
        pointer = UnsafeMutableRawPointer.allocate(
            byteCount: byteCount,
            alignment: MemoryLayout<UInt8>.alignment
        )
        pointer.initializeMemory(as: UInt8.self, repeating: 0xA7, count: byteCount)
    }

    deinit {
        pointer.deallocate()
    }

    func makeData() -> Data {
        Data(bytesNoCopy: pointer, count: byteCount, deallocator: .none)
    }

    var allBytesAreZero: Bool {
        let bytes = pointer.bindMemory(to: UInt8.self, capacity: byteCount)
        return (0..<byteCount).allSatisfy { bytes[$0] == 0 }
    }
}

private actor CapabilityCompletionFlag {
    private(set) var isCompleted = false

    func markCompleted() {
        isCompleted = true
    }
}

private actor CapabilityClearGate {
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        guard !isOpen else { return }
        await withCheckedContinuation { continuation in
            waiters.append(continuation)
        }
    }

    func open() {
        guard !isOpen else { return }
        isOpen = true
        let pending = waiters
        waiters.removeAll()
        for waiter in pending {
            waiter.resume()
        }
    }
}

private actor CapabilityGatedClearSecretBuffer: AtlasVaultSecretBuffer {
    private var bytes: [UInt8]?
    private let clearGate: CapabilityClearGate
    private var didStartClear = false
    private var clearStartWaiters: [CheckedContinuation<Void, Never>] = []

    init(bytes: Data, clearGate: CapabilityClearGate) {
        self.bytes = Array(bytes)
        self.clearGate = clearGate
    }

    func takeSecretBytes() throws -> Data {
        guard let bytes else {
            throw AtlasVaultSecretBufferError.unavailable
        }
        return Data(bytes)
    }

    func clear() async {
        didStartClear = true
        let waiters = clearStartWaiters
        clearStartWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
        await clearGate.wait()
        guard var retainedBytes = bytes else { return }
        bytes = nil
        for index in retainedBytes.indices {
            retainedBytes[index] = 0
        }
    }

    func waitUntilClearStarted() async {
        guard !didStartClear else { return }
        await withCheckedContinuation { continuation in
            clearStartWaiters.append(continuation)
        }
    }

    var isClearedForTesting: Bool {
        bytes == nil
    }
}
