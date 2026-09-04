import Foundation
import LocalAuthentication
import SwiftUI

extension AtlasVaultRemovalController {
  public static func platform(
    registry: AtlasVaultRevocationRegistry, identity: AtlasVaultDeviceIdentity,
    history: AtlasVaultGuardedSyncState
  ) -> AtlasVaultRemovalController {
    AtlasVaultRemovalController(
      registry: registry, initiator: identity.descriptor.deviceID, history: history,
      authorize: { await AtlasAppleDeviceRemovalAuthorizer().authorize() },
      sign: { try identity.sign($0) })
  }
}

@MainActor private final class RemovalAuthorizationRequest {
  let context = LAContext()
  func cancel() { context.invalidate() }
}

struct AtlasVaultRemovalTaskID: Hashable {
  private let controller: ObjectIdentifier
  private let target: String
  private let epoch: Int64
  init(controller: AtlasVaultRemovalController, target: String, epoch: Int64) {
    self.controller = ObjectIdentifier(controller)
    self.target = target
    self.epoch = epoch
  }
}

@MainActor final class AtlasVaultRemovalViewBinding {
  private var controller: AtlasVaultRemovalController?
  func rebind(_ controller: AtlasVaultRemovalController) {
    self.controller?.cancel()
    self.controller = controller
  }
  func cancel() {
    controller?.cancel()
    controller = nil
  }
}

@MainActor public struct AtlasAppleDeviceRemovalAuthorizer: CustomStringConvertible {
  private let evaluate: () async -> Bool
  public init() { evaluate = Self.evaluateFresh }
  init(evaluate: @escaping () async -> Bool) { self.evaluate = evaluate }
  public func authorize() async -> Bool { await evaluate() }
  nonisolated public var description: String { "AtlasAppleDeviceRemovalAuthorizer(<redacted>)" }
  private static func evaluateFresh() async -> Bool {
    let request = RemovalAuthorizationRequest()
    let context = request.context
    var error: NSError?
    guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else { return false }
    let timeout = Task {
      try? await Task.sleep(for: .seconds(60))
      if !Task.isCancelled { context.invalidate() }
    }
    defer {
      timeout.cancel()
      context.invalidate()
    }
    do {
      return try await withTaskCancellationHandler {
        try await context.evaluatePolicy(
          .deviceOwnerAuthentication, localizedReason: "Authorize AtlasVault device removal")
      } onCancel: {
        Task { @MainActor in request.cancel() }
      }
    } catch { return false }
  }
}

@MainActor public struct AtlasVaultDeviceRemovalView: View {
  private let controller: AtlasVaultRemovalController
  private let target: String
  private let epoch: Int64
  @State private var confirmed = false
  @State private var busy = false
  @State private var status = "Loading"
  @State private var root = ""
  @State private var binding = AtlasVaultRemovalViewBinding()
  public init(controller: AtlasVaultRemovalController, targetDevice: String, keyEpoch: Int64) {
    self.controller = controller
    self.target = targetDevice
    self.epoch = keyEpoch
  }
  public var body: some View {
    Form {
      Text(
        target.range(of: "^avd1-[0-9a-f]{64}$", options: .regularExpression) != nil
          ? target : "Invalid device"
      ).textSelection(.enabled)
      Text("Registry: \(status)")
      if !root.isEmpty { Text("Authenticated root: \(root)").textSelection(.enabled) }
      Text(
        "Current epoch: \(epoch). Required next epoch: \(epoch < Int64.max ? epoch + 1 : epoch).")
      Text(
        "Removal is terminal. Future access is blocked only after rotation completes. Previously held data cannot be erased remotely. A remaining authorized device is required for recovery."
      )
      if status == "RECOVERY_PENDING" {
        Text("Resolve the authenticated-history conflict before removing a device.")
      }
      Toggle("Confirm removal of this exact device", isOn: $confirmed).disabled(
        busy || status != "ACTIVE")
      Button {
        busy = true
        Task { @MainActor in
          do {
            _ = try await controller.remove(confirmedTarget: target)
            status = "REVOCATION_PENDING"
          } catch { status = "Removal not authorized" }
          busy = false
          confirmed = false
        }
      } label: {
        Label(busy ? "Authorizing" : "Remove Device", systemImage: "person.badge.minus")
      }
      .disabled(!confirmed || busy || status != "ACTIVE")
    }
    .navigationTitle("Remove Device")
    .task(id: AtlasVaultRemovalTaskID(controller: controller, target: target, epoch: epoch)) {
      binding.rebind(controller)
      confirmed = false
      do {
        guard target.range(of: "^avd1-[0-9a-f]{64}$", options: .regularExpression) != nil,
          epoch > 0, epoch < 9_007_199_254_740_991
        else { throw AtlasVaultRevocationError.rejected }
        controller.select(target)
        let state = try controller.registry.snapshot()
        status = state["status"] as! String
        root = state["root"] as! String
      } catch { status = "RECOVERY_PENDING" }
    }
    .onDisappear { binding.cancel() }
  }
}
