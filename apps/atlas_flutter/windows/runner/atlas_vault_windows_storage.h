#ifndef RUNNER_ATLAS_VAULT_WINDOWS_STORAGE_H_
#define RUNNER_ATLAS_VAULT_WINDOWS_STORAGE_H_

#include <flutter/binary_messenger.h>
#include <flutter/encodable_value.h>
#include <flutter/method_channel.h>

#include <memory>
#include <string>

class AtlasVaultWindowsStorageWorker;

// Owns serialized access to Windows DPAPI metadata, vault keys, and encrypted
// local stores for the dedicated AtlasVault channel.
class AtlasVaultWindowsStorage {
 public:
  AtlasVaultWindowsStorage(flutter::BinaryMessenger* messenger,
                           const std::string& channel_name);
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

  std::unique_ptr<flutter::MethodChannel<flutter::EncodableValue>> channel_;
  std::unique_ptr<AtlasVaultWindowsStorageWorker> worker_;
};

#endif  // RUNNER_ATLAS_VAULT_WINDOWS_STORAGE_H_
