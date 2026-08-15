#ifndef RUNNER_ATLAS_VAULT_WINDOWS_STORAGE_H_
#define RUNNER_ATLAS_VAULT_WINDOWS_STORAGE_H_

#include <flutter/binary_messenger.h>
#include <flutter/encodable_value.h>
#include <flutter/method_channel.h>

#include <windows.h>

#include <atomic>
#include <memory>
#include <string>
#include <vector>

class AtlasVaultWindowsStorageWorker;

// Owns serialized access to Windows DPAPI metadata, device identity, vault
// keys, and encrypted local stores for the dedicated AtlasVault channel.
class AtlasVaultWindowsStorage {
 public:
  AtlasVaultWindowsStorage(flutter::BinaryMessenger* messenger,
                           const std::string& channel_name,
                           HWND owner_window);
  ~AtlasVaultWindowsStorage();

  AtlasVaultWindowsStorage(const AtlasVaultWindowsStorage&) = delete;
  AtlasVaultWindowsStorage& operator=(const AtlasVaultWindowsStorage&) = delete;

 private:
  void HandleMethodCall(
      const flutter::MethodCall<flutter::EncodableValue>& call,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
  void ExecuteMethodCall(
      std::string method,
      std::unique_ptr<flutter::EncodableValue> encoded_arguments,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
  void HandlePickEncryptedExport(
      const flutter::MethodCall<flutter::EncodableValue>& call,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
  void ExecutePickEncryptedExport(
      std::wstring selected_path,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
  void HandleSaveEncryptedExport(
      const flutter::MethodCall<flutter::EncodableValue>& call,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
  void ExecuteSaveEncryptedExport(
      std::wstring destination_path,
      std::vector<uint8_t> encrypted_bytes,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
  void HandlePickPairingArtifact(
      const flutter::MethodCall<flutter::EncodableValue>& call,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);
  void HandleSavePairingArtifact(
      const flutter::MethodCall<flutter::EncodableValue>& call,
      std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result);

  HWND owner_window_;
  std::atomic_bool document_operation_pending_{false};
  std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>> channel_;
  std::unique_ptr<AtlasVaultWindowsStorageWorker> worker_;
};

#endif  // RUNNER_ATLAS_VAULT_WINDOWS_STORAGE_H_
