#include "atlas_vault_windows_storage.h"

#include <windows.h>

#include <bcrypt.h>
#include <dpapi.h>
#include <flutter/standard_method_codec.h>
#include <knownfolders.h>
#include <roapi.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <UserConsentVerifierInterop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Security.Credentials.UI.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <limits>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <variant>
#include <vector>

namespace {

constexpr size_t kVaultKeyLength = 32;
constexpr size_t kVaultIdMaximumLength = 96;
constexpr size_t kProtectedBlobMaximumLength = 1024 * 1024;
constexpr size_t kStoreMaximumLength = 128 * 1024 * 1024;
constexpr size_t kEncryptedDocumentMaximumLength = 128 * 1024 * 1024;
constexpr size_t kKeyEnvelopeFixedLength = 8 + 4 + 2 + 4 + 32;
constexpr char kKeyEnvelopeMagic[] = "AVWKEY01";
constexpr uint32_t kKeyEnvelopeVersion = 1;
constexpr size_t kProtectedMetadataPlaintextMaximumLength = 16 * 1024 * 1024;
constexpr size_t kProtectedMetadataBlobMaximumLength =
    kProtectedMetadataPlaintextMaximumLength + 1024 * 1024;
constexpr size_t kSelectedVaultPlaintextMaximumLength = 256;
constexpr size_t kDeviceIdentityPlaintextMaximumLength = 16 * 1024;
constexpr size_t kPairingStatePlaintextMaximumLength = 2 * 1024 * 1024;
constexpr size_t kPairingTransactionPlaintextMaximumLength = 64 * 1024;
constexpr size_t kProtectedBlobEnvelopeFixedLength = 8 + 4 + 2 + 4 + 4 + 32;
constexpr char kProtectedBlobEnvelopeMagic[] = "AVWBLB01";
constexpr uint32_t kProtectedBlobEnvelopeVersion = 1;
constexpr char kMigrationJournalPurpose[] =
    "plaintext-private-state-migration";
constexpr char kRecoveryImportPurpose[] = "recovery-import";
constexpr char kSelectedVaultPurpose[] = "selected-vault";
constexpr char kDeviceIdentityPurpose[] = "device-identity";
constexpr char kTrustedDevicesPurpose[] = "trusted-devices";
constexpr char kPairingReplayPurpose[] = "pairing-replay";
constexpr char kPairingTransactionPurpose[] = "pairing-transaction";
constexpr char kCapabilityProbeVaultId[] = "capability_probe_v1";

enum class OperationResult {
  kSuccess,
  kNotFound,
  kAlreadyExists,
  kStale,
  kFailed,
};

enum class DocumentDialogResult {
  kSelected,
  kCancelled,
  kFailed,
};

class ScopedHandle {
 public:
  explicit ScopedHandle(HANDLE handle = INVALID_HANDLE_VALUE)
      : handle_(handle) {}
  ~ScopedHandle() { Close(); }

  ScopedHandle(const ScopedHandle&) = delete;
  ScopedHandle& operator=(const ScopedHandle&) = delete;
  ScopedHandle(ScopedHandle&& other) noexcept : handle_(other.handle_) {
    other.handle_ = INVALID_HANDLE_VALUE;
  }
  ScopedHandle& operator=(ScopedHandle&& other) noexcept {
    if (this != &other) {
      Close();
      handle_ = other.handle_;
      other.handle_ = INVALID_HANDLE_VALUE;
    }
    return *this;
  }

  HANDLE get() const { return handle_; }
  bool valid() const {
    return handle_ != INVALID_HANDLE_VALUE && handle_ != nullptr;
  }
  void Close() {
    if (valid()) {
      CloseHandle(handle_);
      handle_ = INVALID_HANDLE_VALUE;
    }
  }

 private:
  HANDLE handle_;
};

class ScopedRoInitialization {
 public:
  ScopedRoInitialization() : result_(RoInitialize(RO_INIT_MULTITHREADED)) {}
  ~ScopedRoInitialization() {
    if (SUCCEEDED(result_)) {
      RoUninitialize();
    }
  }

  ScopedRoInitialization(const ScopedRoInitialization&) = delete;
  ScopedRoInitialization& operator=(const ScopedRoInitialization&) = delete;

  bool available() const { return SUCCEEDED(result_); }

 private:
  HRESULT result_;
};

class ScopedVaultLock {
 public:
  ScopedVaultLock() = default;
  ~ScopedVaultLock() {
    if (locked_) {
      UnlockFileEx(handle_.get(), 0, 1, 0, &overlapped_);
    }
  }

  ScopedVaultLock(const ScopedVaultLock&) = delete;
  ScopedVaultLock& operator=(const ScopedVaultLock&) = delete;

  bool Acquire(const std::wstring& path);

 private:
  ScopedHandle handle_;
  OVERLAPPED overlapped_{};
  bool locked_ = false;
};

struct VaultPaths {
  std::wstring key_directory;
  std::wstring vault_directory;
  std::wstring lock_directory;
  std::wstring key_path;
  std::wstring store_path;
  std::wstring lock_path;
};

struct KeyEnvelope {
  std::array<uint8_t, 32> key_sha256{};
  std::string vault_id;
  std::vector<uint8_t> protected_blob;
};

struct ProtectedBlobEnvelope {
  std::array<uint8_t, 32> plaintext_sha256{};
  uint32_t plaintext_length = 0;
  std::string purpose;
  std::vector<uint8_t> protected_blob;
};

struct ProtectedMetadataPaths {
  std::wstring device_directory;
  std::wstring migrations_directory;
  std::wstring imports_directory;
  std::wstring selection_directory;
  std::wstring pairing_directory;
  std::wstring pairing_staging_directory;
  std::wstring lock_directory;
  std::wstring migration_journal_path;
  std::wstring migration_lock_path;
  std::wstring recovery_import_path;
  std::wstring recovery_import_lock_path;
  std::wstring selection_path;
  std::wstring selection_lock_path;
  std::wstring device_identity_path;
  std::wstring device_identity_lock_path;
  std::wstring trusted_devices_path;
  std::wstring trusted_devices_lock_path;
  std::wstring pairing_replay_path;
  std::wstring pairing_replay_lock_path;
  std::wstring pairing_transaction_path;
  std::wstring pairing_transaction_lock_path;
  std::wstring pairing_staging_lock_path;
};

struct StorageCapabilities {
  bool secure_boundary_available = false;
  bool dpapi_available = false;
  bool current_user_scope = false;
  bool local_app_data_available = false;
  bool atomic_replace_available = false;
  bool hardware_backed_guaranteed = false;
};

void Wipe(std::vector<uint8_t>* value) {
  if (value != nullptr && !value->empty()) {
    SecureZeroMemory(value->data(), value->size());
    value->clear();
  }
}

bool IsNtSuccess(NTSTATUS status) {
  return status >= 0;
}

bool Sha256(const uint8_t* data,
            size_t data_length,
            std::array<uint8_t, 32>* output) {
  if (output == nullptr ||
      data_length > static_cast<size_t>(std::numeric_limits<ULONG>::max())) {
    return false;
  }

  BCRYPT_ALG_HANDLE algorithm = nullptr;
  BCRYPT_HASH_HANDLE hash = nullptr;
  DWORD object_length = 0;
  DWORD hash_length = 0;
  DWORD returned = 0;
  std::vector<uint8_t> hash_object;
  bool succeeded = false;

  if (!IsNtSuccess(BCryptOpenAlgorithmProvider(
          &algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0)) ||
      !IsNtSuccess(BCryptGetProperty(
          algorithm, BCRYPT_OBJECT_LENGTH,
          reinterpret_cast<PUCHAR>(&object_length),
          static_cast<ULONG>(sizeof(object_length)), &returned, 0)) ||
      returned != static_cast<DWORD>(sizeof(object_length)) ||
      !IsNtSuccess(BCryptGetProperty(
          algorithm, BCRYPT_HASH_LENGTH,
          reinterpret_cast<PUCHAR>(&hash_length),
          static_cast<ULONG>(sizeof(hash_length)), &returned, 0)) ||
      returned != static_cast<DWORD>(sizeof(hash_length)) ||
      hash_length != static_cast<DWORD>(output->size()) ||
      object_length == 0) {
    if (algorithm != nullptr) {
      BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    return false;
  }

  hash_object.resize(object_length);
  if (IsNtSuccess(BCryptCreateHash(
          algorithm, &hash, hash_object.data(), object_length, nullptr, 0, 0)) &&
      (data_length == 0 ||
       IsNtSuccess(BCryptHashData(
           hash, const_cast<PUCHAR>(reinterpret_cast<const UCHAR*>(data)),
           static_cast<ULONG>(data_length), 0))) &&
      IsNtSuccess(BCryptFinishHash(hash, output->data(),
                                   static_cast<ULONG>(output->size()), 0))) {
    succeeded = true;
  }

  if (hash != nullptr) {
    BCryptDestroyHash(hash);
  }
  if (!hash_object.empty()) {
    SecureZeroMemory(hash_object.data(), hash_object.size());
  }
  BCryptCloseAlgorithmProvider(algorithm, 0);
  return succeeded;
}

bool Sha256(const std::vector<uint8_t>& data,
            std::array<uint8_t, 32>* output) {
  return Sha256(data.data(), data.size(), output);
}

bool ConstantTimeEquals(const uint8_t* left,
                        size_t left_length,
                        const uint8_t* right,
                        size_t right_length) {
  size_t difference = left_length ^ right_length;
  const size_t maximum =
      left_length > right_length ? left_length : right_length;
  for (size_t index = 0; index < maximum; ++index) {
    const uint8_t left_byte = index < left_length ? left[index] : 0;
    const uint8_t right_byte = index < right_length ? right[index] : 0;
    difference |= static_cast<size_t>(left_byte ^ right_byte);
  }
  return difference == 0;
}

std::string HexLower(const uint8_t* bytes, size_t length) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string result;
  result.reserve(length * 2);
  for (size_t index = 0; index < length; ++index) {
    result.push_back(kHex[(bytes[index] >> 4) & 0x0f]);
    result.push_back(kHex[bytes[index] & 0x0f]);
  }
  return result;
}

std::wstring WidenAscii(const std::string& value) {
  return std::wstring(value.begin(), value.end());
}

std::wstring JoinPath(const std::wstring& parent,
                      const std::wstring& child) {
  if (parent.empty() || parent.back() == L'\\') {
    return parent + child;
  }
  return parent + L"\\" + child;
}

bool IsLocalAbsolutePath(const std::wstring& path) {
  if (path.size() < 3 || path[1] != L':' ||
      (path[2] != L'\\' && path[2] != L'/')) {
    return false;
  }
  const wchar_t drive = path[0];
  return (drive >= L'A' && drive <= L'Z') ||
         (drive >= L'a' && drive <= L'z');
}

bool IsSafeDirectory(const std::wstring& path) {
  const DWORD attributes = GetFileAttributesW(path.c_str());
  return attributes != INVALID_FILE_ATTRIBUTES &&
         (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 &&
         (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

bool EnsureSafeDirectory(const std::wstring& path) {
  if (CreateDirectoryW(path.c_str(), nullptr) == FALSE) {
    const DWORD error = GetLastError();
    if (error != ERROR_ALREADY_EXISTS) {
      return false;
    }
  }
  return IsSafeDirectory(path);
}

bool IsSafeRegularHandle(HANDLE handle) {
  FILE_ATTRIBUTE_TAG_INFO information{};
  if (GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &information,
                                   static_cast<DWORD>(sizeof(information))) ==
      FALSE) {
    return false;
  }
  return (information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0 &&
         (information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

OperationResult InspectRegularFile(const std::wstring& path) {
  const DWORD attributes = GetFileAttributesW(path.c_str());
  if (attributes == INVALID_FILE_ATTRIBUTES) {
    const DWORD error = GetLastError();
    if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) {
      return OperationResult::kNotFound;
    }
    return OperationResult::kFailed;
  }
  if ((attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 ||
      (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
    return OperationResult::kFailed;
  }
  return OperationResult::kSuccess;
}

bool PrepareVaultPaths(const std::string& vault_id, VaultPaths* output) {
  if (output == nullptr) {
    return false;
  }

  PWSTR local_app_data = nullptr;
  if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT,
                                  nullptr, &local_app_data)) ||
      local_app_data == nullptr) {
    if (local_app_data != nullptr) {
      CoTaskMemFree(local_app_data);
    }
    return false;
  }
  const std::wstring local_root(local_app_data);
  CoTaskMemFree(local_app_data);
  if (!IsLocalAbsolutePath(local_root) || !IsSafeDirectory(local_root)) {
    return false;
  }

  std::array<uint8_t, 32> vault_hash{};
  const auto* vault_bytes =
      reinterpret_cast<const uint8_t*>(vault_id.data());
  if (!Sha256(vault_bytes, vault_id.size(), &vault_hash)) {
    return false;
  }
  const std::wstring hash_name =
      WidenAscii(HexLower(vault_hash.data(), vault_hash.size()));

  const std::wstring organization =
      JoinPath(local_root, L"UNApplications");
  const std::wstring atlas_vault = JoinPath(organization, L"AtlasVault");
  const std::wstring version = JoinPath(atlas_vault, L"v1");
  output->key_directory = JoinPath(version, L"keys");
  const std::wstring vaults = JoinPath(version, L"vaults");
  output->lock_directory = JoinPath(version, L"locks");
  output->vault_directory = JoinPath(vaults, hash_name);

  if (!EnsureSafeDirectory(organization) ||
      !EnsureSafeDirectory(atlas_vault) || !EnsureSafeDirectory(version) ||
      !EnsureSafeDirectory(output->key_directory) ||
      !EnsureSafeDirectory(vaults) ||
      !EnsureSafeDirectory(output->lock_directory) ||
      !EnsureSafeDirectory(output->vault_directory)) {
    return false;
  }

  output->key_path =
      JoinPath(output->key_directory, hash_name + L".bin");
  output->store_path =
      JoinPath(output->vault_directory, L"atlasvault-local-store.json");
  output->lock_path =
      JoinPath(output->lock_directory, hash_name + L".lock");
  return true;
}

bool PrepareProtectedMetadataPaths(ProtectedMetadataPaths* output) {
  if (output == nullptr) {
    return false;
  }

  PWSTR local_app_data = nullptr;
  if (FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT,
                                  nullptr, &local_app_data)) ||
      local_app_data == nullptr) {
    if (local_app_data != nullptr) {
      CoTaskMemFree(local_app_data);
    }
    return false;
  }
  const std::wstring local_root(local_app_data);
  CoTaskMemFree(local_app_data);
  if (!IsLocalAbsolutePath(local_root) || !IsSafeDirectory(local_root)) {
    return false;
  }

  const std::wstring organization =
      JoinPath(local_root, L"UNApplications");
  const std::wstring atlas_vault = JoinPath(organization, L"AtlasVault");
  const std::wstring version = JoinPath(atlas_vault, L"v1");
  output->device_directory = JoinPath(version, L"device");
  output->migrations_directory = JoinPath(version, L"migrations");
  output->imports_directory = JoinPath(version, L"imports");
  output->selection_directory = JoinPath(version, L"selection");
  output->pairing_directory = JoinPath(version, L"pairing");
  output->pairing_staging_directory =
      JoinPath(output->pairing_directory, L"staged");
  output->lock_directory = JoinPath(version, L"locks");
  if (!EnsureSafeDirectory(organization) ||
      !EnsureSafeDirectory(atlas_vault) || !EnsureSafeDirectory(version) ||
      !EnsureSafeDirectory(output->device_directory) ||
      !EnsureSafeDirectory(output->migrations_directory) ||
      !EnsureSafeDirectory(output->imports_directory) ||
      !EnsureSafeDirectory(output->selection_directory) ||
      !EnsureSafeDirectory(output->pairing_directory) ||
      !EnsureSafeDirectory(output->pairing_staging_directory) ||
      !EnsureSafeDirectory(output->lock_directory)) {
    return false;
  }

  output->migration_journal_path =
      JoinPath(output->migrations_directory,
               L"plaintext-private-state.bin");
  output->migration_lock_path =
      JoinPath(output->lock_directory,
               L"plaintext-private-state-migration.lock");
  output->recovery_import_path =
      JoinPath(output->imports_directory, L"recovery-import.bin");
  output->recovery_import_lock_path =
      JoinPath(output->lock_directory, L"recovery-import.lock");
  output->selection_path =
      JoinPath(output->selection_directory, L"selected-vault.bin");
  output->selection_lock_path =
      JoinPath(output->lock_directory, L"selected-vault.lock");
  output->device_identity_path =
      JoinPath(output->device_directory, L"device-identity.bin");
  output->device_identity_lock_path =
      JoinPath(output->lock_directory, L"device-identity.lock");
  output->trusted_devices_path =
      JoinPath(output->pairing_directory, L"trusted-devices.bin");
  output->trusted_devices_lock_path =
      JoinPath(output->lock_directory, L"trusted-devices.lock");
  output->pairing_replay_path =
      JoinPath(output->pairing_directory, L"pairing-replay.bin");
  output->pairing_replay_lock_path =
      JoinPath(output->lock_directory, L"pairing-replay.lock");
  output->pairing_transaction_path =
      JoinPath(output->pairing_directory, L"pairing-transaction.bin");
  output->pairing_transaction_lock_path =
      JoinPath(output->lock_directory, L"pairing-transaction.lock");
  output->pairing_staging_lock_path =
      JoinPath(output->lock_directory, L"pairing-staging.lock");
  return true;
}

bool ScopedVaultLock::Acquire(const std::wstring& path) {
  handle_ = ScopedHandle(CreateFileW(
      path.c_str(), GENERIC_READ | GENERIC_WRITE,
      FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
      FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
  if (!handle_.valid() || !IsSafeRegularHandle(handle_.get())) {
    return false;
  }
  if (LockFileEx(handle_.get(),
                 LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0,
                 &overlapped_) == FALSE) {
    return false;
  }
  locked_ = true;
  return true;
}

OperationResult ReadRegularFile(const std::wstring& path,
                                size_t maximum_length,
                                std::vector<uint8_t>* output) {
  if (output == nullptr) {
    return OperationResult::kFailed;
  }
  output->clear();

  ScopedHandle handle(CreateFileW(
      path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
      FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
  if (!handle.valid()) {
    const DWORD error = GetLastError();
    return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND
               ? OperationResult::kNotFound
               : OperationResult::kFailed;
  }
  if (!IsSafeRegularHandle(handle.get())) {
    return OperationResult::kFailed;
  }

  LARGE_INTEGER size{};
  if (GetFileSizeEx(handle.get(), &size) == FALSE || size.QuadPart <= 0 ||
      static_cast<unsigned long long>(size.QuadPart) >
          static_cast<unsigned long long>(maximum_length) ||
      size.QuadPart > static_cast<LONGLONG>(
                          std::numeric_limits<DWORD>::max())) {
    return OperationResult::kFailed;
  }

  output->resize(static_cast<size_t>(size.QuadPart));
  DWORD bytes_read = 0;
  if (ReadFile(handle.get(), output->data(),
               static_cast<DWORD>(output->size()), &bytes_read,
               nullptr) == FALSE ||
      bytes_read != static_cast<DWORD>(output->size())) {
    Wipe(output);
    return OperationResult::kFailed;
  }
  return OperationResult::kSuccess;
}

bool OpenTemporaryFile(const std::wstring& directory,
                       std::wstring* path,
                       ScopedHandle* handle) {
  if (path == nullptr || handle == nullptr ||
      !IsSafeDirectory(directory)) {
    return false;
  }
  for (int attempt = 0; attempt < 32; ++attempt) {
    std::array<uint8_t, 16> random{};
    if (!IsNtSuccess(BCryptGenRandom(
            nullptr, random.data(), static_cast<ULONG>(random.size()),
            BCRYPT_USE_SYSTEM_PREFERRED_RNG))) {
      return false;
    }
    const std::wstring name =
        L".atlasvault-" +
        WidenAscii(HexLower(random.data(), random.size())) + L".tmp";
    const std::wstring candidate = JoinPath(directory, name);
    ScopedHandle candidate_handle(CreateFileW(
        candidate.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    if (candidate_handle.valid()) {
      if (!IsSafeRegularHandle(candidate_handle.get())) {
        candidate_handle.Close();
        DeleteFileW(candidate.c_str());
        return false;
      }
      *path = candidate;
      *handle = std::move(candidate_handle);
      return true;
    }
    if (GetLastError() != ERROR_FILE_EXISTS &&
        GetLastError() != ERROR_ALREADY_EXISTS) {
      return false;
    }
  }
  return false;
}

bool WriteFlushedTemporary(const std::wstring& directory,
                           const std::vector<uint8_t>& bytes,
                           std::wstring* temporary_path) {
  if (bytes.empty() ||
      bytes.size() >
          static_cast<size_t>(std::numeric_limits<DWORD>::max())) {
    return false;
  }
  ScopedHandle handle;
  if (!OpenTemporaryFile(directory, temporary_path, &handle)) {
    return false;
  }
  DWORD written = 0;
  const bool succeeded =
      WriteFile(handle.get(), bytes.data(), static_cast<DWORD>(bytes.size()),
                &written, nullptr) != FALSE &&
      written == static_cast<DWORD>(bytes.size()) &&
      FlushFileBuffers(handle.get()) != FALSE;
  handle.Close();
  if (!succeeded) {
    DeleteFileW(temporary_path->c_str());
  }
  return succeeded;
}

OperationResult AtomicCreate(const std::wstring& directory,
                             const std::wstring& destination,
                             const std::vector<uint8_t>& bytes,
                             size_t maximum_length) {
  if (bytes.empty() || bytes.size() > maximum_length) {
    return OperationResult::kFailed;
  }
  const OperationResult existing = InspectRegularFile(destination);
  if (existing == OperationResult::kSuccess) {
    return OperationResult::kAlreadyExists;
  }
  if (existing != OperationResult::kNotFound) {
    return OperationResult::kFailed;
  }

  std::wstring temporary;
  if (!WriteFlushedTemporary(directory, bytes, &temporary)) {
    return OperationResult::kFailed;
  }
  if (MoveFileExW(temporary.c_str(), destination.c_str(),
                  MOVEFILE_WRITE_THROUGH) == FALSE) {
    const DWORD error = GetLastError();
    DeleteFileW(temporary.c_str());
    return error == ERROR_FILE_EXISTS || error == ERROR_ALREADY_EXISTS
               ? OperationResult::kAlreadyExists
               : OperationResult::kFailed;
  }

  std::vector<uint8_t> read_back;
  const OperationResult read_result =
      ReadRegularFile(destination, maximum_length, &read_back);
  const bool equal =
      read_result == OperationResult::kSuccess &&
      ConstantTimeEquals(bytes.data(), bytes.size(), read_back.data(),
                         read_back.size());
  Wipe(&read_back);
  return equal ? OperationResult::kSuccess : OperationResult::kFailed;
}

bool IsLowerSha256(const std::string& value) {
  if (value.size() != 64) {
    return false;
  }
  for (const char character : value) {
    if (!((character >= '0' && character <= '9') ||
          (character >= 'a' && character <= 'f'))) {
      return false;
    }
  }
  return true;
}

bool IsPairingArtifactKind(const std::string& value) {
  return value == "offer" || value == "acceptance" ||
         value == "delivery" || value == "acknowledgement";
}

std::wstring PairingArtifactPath(const ProtectedMetadataPaths& paths,
                                 const std::string& kind) {
  if (!IsPairingArtifactKind(kind)) {
    return std::wstring();
  }
  return JoinPath(paths.pairing_staging_directory,
                  WidenAscii(kind) + L".atlaspair");
}

OperationResult AtomicReplace(const std::wstring& directory,
                              const std::wstring& destination,
                              const std::vector<uint8_t>& bytes,
                              const std::string& expected_sha256,
                              size_t maximum_length) {
  if (bytes.empty() || bytes.size() > maximum_length ||
      !IsLowerSha256(expected_sha256)) {
    return OperationResult::kFailed;
  }

  std::vector<uint8_t> current;
  const OperationResult read_result =
      ReadRegularFile(destination, maximum_length, &current);
  if (read_result != OperationResult::kSuccess) {
    return read_result;
  }
  std::array<uint8_t, 32> digest{};
  if (!Sha256(current, &digest)) {
    Wipe(&current);
    return OperationResult::kFailed;
  }
  Wipe(&current);
  const std::string current_sha256 =
      HexLower(digest.data(), digest.size());
  if (!ConstantTimeEquals(
          reinterpret_cast<const uint8_t*>(current_sha256.data()),
          current_sha256.size(),
          reinterpret_cast<const uint8_t*>(expected_sha256.data()),
          expected_sha256.size())) {
    return OperationResult::kStale;
  }

  std::wstring temporary;
  if (!WriteFlushedTemporary(directory, bytes, &temporary)) {
    return OperationResult::kFailed;
  }
  if (MoveFileExW(temporary.c_str(), destination.c_str(),
                  MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) ==
      FALSE) {
    DeleteFileW(temporary.c_str());
    return OperationResult::kFailed;
  }

  std::vector<uint8_t> read_back;
  const OperationResult replacement_read =
      ReadRegularFile(destination, maximum_length, &read_back);
  const bool equal =
      replacement_read == OperationResult::kSuccess &&
      ConstantTimeEquals(bytes.data(), bytes.size(), read_back.data(),
                         read_back.size());
  Wipe(&read_back);
  return equal ? OperationResult::kSuccess : OperationResult::kFailed;
}

OperationResult DeleteRegularFile(const std::wstring& path) {
  const OperationResult existing = InspectRegularFile(path);
  if (existing == OperationResult::kNotFound) {
    return OperationResult::kSuccess;
  }
  if (existing != OperationResult::kSuccess) {
    return OperationResult::kFailed;
  }
  return DeleteFileW(path.c_str()) != FALSE ? OperationResult::kSuccess
                                            : OperationResult::kFailed;
}

DocumentDialogResult SelectDocumentSource(
    HWND owner_window,
    const wchar_t* filter_name,
    const wchar_t* filter_pattern,
    const wchar_t* default_extension,
    std::wstring* selected_path_output) {
  if (owner_window == nullptr || filter_name == nullptr ||
      filter_pattern == nullptr || default_extension == nullptr ||
      selected_path_output == nullptr) {
    return DocumentDialogResult::kFailed;
  }
  selected_path_output->clear();

  IFileOpenDialog* dialog = nullptr;
  HRESULT status = CoCreateInstance(CLSID_FileOpenDialog, nullptr,
                                    CLSCTX_INPROC_SERVER,
                                    IID_PPV_ARGS(&dialog));
  if (FAILED(status) || dialog == nullptr) {
    return DocumentDialogResult::kFailed;
  }

  DWORD options = 0;
  status = dialog->GetOptions(&options);
  if (SUCCEEDED(status)) {
    status = dialog->SetOptions(
        options | FOS_FORCEFILESYSTEM | FOS_FILEMUSTEXIST |
        FOS_PATHMUSTEXIST | FOS_DONTADDTORECENT | FOS_NOCHANGEDIR);
  }
  const COMDLG_FILTERSPEC filters[] = {
      {filter_name, filter_pattern},
  };
  if (SUCCEEDED(status)) {
    status = dialog->SetFileTypes(1, filters);
  }
  if (SUCCEEDED(status)) {
    status = dialog->SetFileTypeIndex(1);
  }
  if (SUCCEEDED(status)) {
    status = dialog->SetDefaultExtension(default_extension);
  }
  if (FAILED(status)) {
    dialog->Release();
    return DocumentDialogResult::kFailed;
  }

  status = dialog->Show(owner_window);
  if (status == HRESULT_FROM_WIN32(ERROR_CANCELLED)) {
    dialog->Release();
    return DocumentDialogResult::kCancelled;
  }
  if (FAILED(status)) {
    dialog->Release();
    return DocumentDialogResult::kFailed;
  }

  IShellItem* item = nullptr;
  status = dialog->GetResult(&item);
  if (FAILED(status) || item == nullptr) {
    dialog->Release();
    return DocumentDialogResult::kFailed;
  }
  PWSTR selected_path = nullptr;
  status = item->GetDisplayName(SIGDN_FILESYSPATH, &selected_path);
  if (SUCCEEDED(status) && selected_path != nullptr &&
      selected_path[0] != L'\0') {
    selected_path_output->assign(selected_path);
  }
  if (selected_path != nullptr) {
    CoTaskMemFree(selected_path);
  }
  item->Release();
  dialog->Release();
  return SUCCEEDED(status) && !selected_path_output->empty()
             ? DocumentDialogResult::kSelected
             : DocumentDialogResult::kFailed;
}

DocumentDialogResult SelectDocumentDestination(
    HWND owner_window,
    const wchar_t* filter_name,
    const wchar_t* filter_pattern,
    const wchar_t* default_extension,
    const wchar_t* suggested_filename,
    std::wstring* destination_path) {
  if (owner_window == nullptr || filter_name == nullptr ||
      filter_pattern == nullptr || default_extension == nullptr ||
      suggested_filename == nullptr || destination_path == nullptr) {
    return DocumentDialogResult::kFailed;
  }
  destination_path->clear();

  IFileSaveDialog* dialog = nullptr;
  HRESULT status = CoCreateInstance(CLSID_FileSaveDialog, nullptr,
                                    CLSCTX_INPROC_SERVER,
                                    IID_PPV_ARGS(&dialog));
  if (FAILED(status) || dialog == nullptr) {
    return DocumentDialogResult::kFailed;
  }

  DWORD options = 0;
  status = dialog->GetOptions(&options);
  if (SUCCEEDED(status)) {
    status = dialog->SetOptions(
        options | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST |
        FOS_DONTADDTORECENT | FOS_NOCHANGEDIR | FOS_OVERWRITEPROMPT);
  }
  const COMDLG_FILTERSPEC filters[] = {
      {filter_name, filter_pattern},
  };
  if (SUCCEEDED(status)) {
    status = dialog->SetFileTypes(1, filters);
  }
  if (SUCCEEDED(status)) {
    status = dialog->SetFileTypeIndex(1);
  }
  if (SUCCEEDED(status)) {
    status = dialog->SetDefaultExtension(default_extension);
  }
  if (SUCCEEDED(status)) {
    status = dialog->SetFileName(suggested_filename);
  }
  if (FAILED(status)) {
    dialog->Release();
    return DocumentDialogResult::kFailed;
  }

  status = dialog->Show(owner_window);
  if (status == HRESULT_FROM_WIN32(ERROR_CANCELLED)) {
    dialog->Release();
    return DocumentDialogResult::kCancelled;
  }
  if (FAILED(status)) {
    dialog->Release();
    return DocumentDialogResult::kFailed;
  }

  IShellItem* item = nullptr;
  status = dialog->GetResult(&item);
  if (FAILED(status) || item == nullptr) {
    dialog->Release();
    return DocumentDialogResult::kFailed;
  }
  PWSTR selected_path = nullptr;
  status = item->GetDisplayName(SIGDN_FILESYSPATH, &selected_path);
  if (SUCCEEDED(status) && selected_path != nullptr &&
      selected_path[0] != L'\0') {
    destination_path->assign(selected_path);
  }
  if (selected_path != nullptr) {
    CoTaskMemFree(selected_path);
  }
  item->Release();
  dialog->Release();
  return SUCCEEDED(status) && !destination_path->empty()
             ? DocumentDialogResult::kSelected
             : DocumentDialogResult::kFailed;
}

OperationResult SaveEncryptedExportAtomically(
    const std::wstring& destination,
    const std::vector<uint8_t>& encrypted_bytes) {
  if (destination.empty() || encrypted_bytes.empty() ||
      encrypted_bytes.size() > kEncryptedDocumentMaximumLength) {
    return OperationResult::kFailed;
  }
  const size_t separator = destination.find_last_of(L"\\/");
  if (separator == std::wstring::npos || separator == 0) {
    return OperationResult::kFailed;
  }
  const size_t directory_length =
      separator == 2 && destination.size() > 2 && destination[1] == L':'
          ? 3
          : separator;
  const std::wstring directory = destination.substr(0, directory_length);
  if (!IsSafeDirectory(directory)) {
    return OperationResult::kFailed;
  }
  const OperationResult existing = InspectRegularFile(destination);
  if (existing != OperationResult::kSuccess &&
      existing != OperationResult::kNotFound) {
    return OperationResult::kFailed;
  }

  std::wstring temporary;
  if (!WriteFlushedTemporary(directory, encrypted_bytes, &temporary)) {
    return OperationResult::kFailed;
  }
  if (MoveFileExW(temporary.c_str(), destination.c_str(),
                  MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) ==
      FALSE) {
    DeleteFileW(temporary.c_str());
    return OperationResult::kFailed;
  }

  std::vector<uint8_t> restored;
  const OperationResult read_result = ReadRegularFile(
      destination, kEncryptedDocumentMaximumLength, &restored);
  const bool equal =
      read_result == OperationResult::kSuccess &&
      ConstantTimeEquals(encrypted_bytes.data(), encrypted_bytes.size(),
                         restored.data(), restored.size());
  Wipe(&restored);
  return equal ? OperationResult::kSuccess : OperationResult::kFailed;
}

std::vector<uint8_t> DpapiEntropy(const std::string& vault_id,
                                  bool* succeeded) {
  const std::string input =
      std::string("atlasvault-windows-dpapi-v1:") + "UNApplications:" +
      "AtlasVault:" + vault_id;
  std::array<uint8_t, 32> hash{};
  *succeeded =
      Sha256(reinterpret_cast<const uint8_t*>(input.data()), input.size(),
             &hash);
  return *succeeded ? std::vector<uint8_t>(hash.begin(), hash.end())
                    : std::vector<uint8_t>();
}

bool ProtectVaultKey(const std::string& vault_id,
                     const std::vector<uint8_t>& vault_key,
                     std::array<uint8_t, 32>* key_sha256,
                     std::vector<uint8_t>* protected_blob) {
  if (vault_key.size() != kVaultKeyLength || key_sha256 == nullptr ||
      protected_blob == nullptr || !Sha256(vault_key, key_sha256)) {
    return false;
  }
  bool entropy_succeeded = false;
  std::vector<uint8_t> entropy =
      DpapiEntropy(vault_id, &entropy_succeeded);
  if (!entropy_succeeded) {
    return false;
  }

  std::vector<uint8_t> key_copy(vault_key);
  DATA_BLOB input{static_cast<DWORD>(key_copy.size()), key_copy.data()};
  DATA_BLOB optional_entropy{static_cast<DWORD>(entropy.size()),
                             entropy.data()};
  DATA_BLOB output{};
  const BOOL protected_result = CryptProtectData(
      &input, nullptr, &optional_entropy, nullptr, nullptr,
      CRYPTPROTECT_UI_FORBIDDEN, &output);
  if (protected_result != FALSE && output.pbData != nullptr &&
      output.cbData > 0 &&
      static_cast<size_t>(output.cbData) <=
          kProtectedBlobMaximumLength) {
    protected_blob->assign(output.pbData, output.pbData + output.cbData);
  }
  if (output.pbData != nullptr) {
    LocalFree(output.pbData);
  }
  Wipe(&key_copy);
  Wipe(&entropy);
  return protected_result != FALSE && !protected_blob->empty();
}

bool UnprotectVaultKey(const KeyEnvelope& envelope,
                       std::vector<uint8_t>* vault_key) {
  if (vault_key == nullptr || envelope.protected_blob.empty() ||
      envelope.protected_blob.size() > kProtectedBlobMaximumLength) {
    return false;
  }
  bool entropy_succeeded = false;
  std::vector<uint8_t> entropy =
      DpapiEntropy(envelope.vault_id, &entropy_succeeded);
  if (!entropy_succeeded) {
    return false;
  }
  DATA_BLOB input{
      static_cast<DWORD>(envelope.protected_blob.size()),
      const_cast<BYTE*>(envelope.protected_blob.data())};
  DATA_BLOB optional_entropy{static_cast<DWORD>(entropy.size()),
                             entropy.data()};
  DATA_BLOB output{};
  const BOOL unprotected = CryptUnprotectData(
      &input, nullptr, &optional_entropy, nullptr, nullptr,
      CRYPTPROTECT_UI_FORBIDDEN, &output);

  bool valid = false;
  if (unprotected != FALSE && output.pbData != nullptr &&
      output.cbData == static_cast<DWORD>(kVaultKeyLength)) {
    std::array<uint8_t, 32> recovered_hash{};
    valid =
        Sha256(output.pbData, output.cbData, &recovered_hash) &&
        ConstantTimeEquals(recovered_hash.data(), recovered_hash.size(),
                           envelope.key_sha256.data(),
                           envelope.key_sha256.size());
    if (valid) {
      vault_key->assign(output.pbData, output.pbData + output.cbData);
    }
  }
  if (output.pbData != nullptr) {
    SecureZeroMemory(output.pbData, output.cbData);
    LocalFree(output.pbData);
  }
  Wipe(&entropy);
  return valid;
}

std::vector<uint8_t> ProtectedBlobEntropy(const std::string& purpose,
                                          bool* succeeded) {
  const std::string input =
      std::string("atlasvault-windows-protected-blob-v1:") +
      "UNApplications:" + "AtlasVault:" + purpose;
  std::array<uint8_t, 32> hash{};
  *succeeded =
      Sha256(reinterpret_cast<const uint8_t*>(input.data()), input.size(),
             &hash);
  std::vector<uint8_t> result;
  if (*succeeded) {
    result.assign(hash.begin(), hash.end());
  }
  SecureZeroMemory(hash.data(), hash.size());
  return result;
}

bool ProtectMetadata(const std::string& purpose,
                     const std::vector<uint8_t>& plaintext,
                     size_t maximum_plaintext_length,
                     ProtectedBlobEnvelope* envelope) {
  if (envelope == nullptr || plaintext.empty() ||
      plaintext.size() > maximum_plaintext_length ||
      plaintext.size() > kProtectedMetadataPlaintextMaximumLength ||
      plaintext.size() >
          static_cast<size_t>(std::numeric_limits<DWORD>::max()) ||
      purpose.empty() || purpose.size() >
          static_cast<size_t>(std::numeric_limits<uint16_t>::max()) ||
      !Sha256(plaintext, &envelope->plaintext_sha256)) {
    return false;
  }

  bool entropy_succeeded = false;
  std::vector<uint8_t> entropy =
      ProtectedBlobEntropy(purpose, &entropy_succeeded);
  if (!entropy_succeeded) {
    return false;
  }
  std::vector<uint8_t> plaintext_copy(plaintext);
  DATA_BLOB input{static_cast<DWORD>(plaintext_copy.size()),
                  plaintext_copy.data()};
  DATA_BLOB optional_entropy{static_cast<DWORD>(entropy.size()),
                             entropy.data()};
  DATA_BLOB output{};
  const BOOL protected_result = CryptProtectData(
      &input, nullptr, &optional_entropy, nullptr, nullptr,
      CRYPTPROTECT_UI_FORBIDDEN, &output);
  if (protected_result != FALSE && output.pbData != nullptr &&
      output.cbData > 0 &&
      static_cast<size_t>(output.cbData) <=
          kProtectedMetadataBlobMaximumLength) {
    envelope->plaintext_length =
        static_cast<uint32_t>(plaintext_copy.size());
    envelope->purpose = purpose;
    envelope->protected_blob.assign(output.pbData,
                                    output.pbData + output.cbData);
  }
  if (output.pbData != nullptr) {
    LocalFree(output.pbData);
  }
  Wipe(&plaintext_copy);
  Wipe(&entropy);
  if (protected_result == FALSE || envelope->protected_blob.empty()) {
    SecureZeroMemory(envelope->plaintext_sha256.data(),
                     envelope->plaintext_sha256.size());
    envelope->plaintext_length = 0;
    envelope->purpose.clear();
    Wipe(&envelope->protected_blob);
    return false;
  }
  return true;
}

bool UnprotectMetadata(const ProtectedBlobEnvelope& envelope,
                       size_t maximum_plaintext_length,
                       std::vector<uint8_t>* plaintext) {
  if (plaintext == nullptr || envelope.purpose.empty() ||
      envelope.plaintext_length == 0 ||
      static_cast<size_t>(envelope.plaintext_length) >
          maximum_plaintext_length ||
      envelope.protected_blob.empty() ||
      envelope.protected_blob.size() >
          kProtectedMetadataBlobMaximumLength ||
      envelope.protected_blob.size() >
          static_cast<size_t>(std::numeric_limits<DWORD>::max())) {
    return false;
  }

  bool entropy_succeeded = false;
  std::vector<uint8_t> entropy =
      ProtectedBlobEntropy(envelope.purpose, &entropy_succeeded);
  if (!entropy_succeeded) {
    return false;
  }
  DATA_BLOB input{
      static_cast<DWORD>(envelope.protected_blob.size()),
      const_cast<BYTE*>(envelope.protected_blob.data())};
  DATA_BLOB optional_entropy{static_cast<DWORD>(entropy.size()),
                             entropy.data()};
  DATA_BLOB output{};
  const BOOL unprotected = CryptUnprotectData(
      &input, nullptr, &optional_entropy, nullptr, nullptr,
      CRYPTPROTECT_UI_FORBIDDEN, &output);

  bool valid = false;
  if (unprotected != FALSE && output.pbData != nullptr &&
      output.cbData == envelope.plaintext_length && output.cbData > 0 &&
      static_cast<size_t>(output.cbData) <= maximum_plaintext_length) {
    std::array<uint8_t, 32> recovered_hash{};
    valid =
        Sha256(output.pbData, output.cbData, &recovered_hash) &&
        ConstantTimeEquals(recovered_hash.data(), recovered_hash.size(),
                           envelope.plaintext_sha256.data(),
                           envelope.plaintext_sha256.size());
    SecureZeroMemory(recovered_hash.data(), recovered_hash.size());
    if (valid) {
      plaintext->assign(output.pbData, output.pbData + output.cbData);
    }
  }
  if (output.pbData != nullptr) {
    SecureZeroMemory(output.pbData, output.cbData);
    LocalFree(output.pbData);
  }
  Wipe(&entropy);
  return valid;
}

void AppendUint16(std::vector<uint8_t>* output, uint16_t value) {
  output->push_back(static_cast<uint8_t>(value & 0xff));
  output->push_back(static_cast<uint8_t>((value >> 8) & 0xff));
}

void AppendUint32(std::vector<uint8_t>* output, uint32_t value) {
  for (int shift = 0; shift < 32; shift += 8) {
    output->push_back(static_cast<uint8_t>((value >> shift) & 0xff));
  }
}

bool ReadUint16(const std::vector<uint8_t>& input,
                size_t* offset,
                uint16_t* output) {
  if (offset == nullptr || output == nullptr ||
      *offset > input.size() || input.size() - *offset < 2) {
    return false;
  }
  *output = static_cast<uint16_t>(input[*offset]) |
            static_cast<uint16_t>(input[*offset + 1] << 8);
  *offset += 2;
  return true;
}

bool ReadUint32(const std::vector<uint8_t>& input,
                size_t* offset,
                uint32_t* output) {
  if (offset == nullptr || output == nullptr ||
      *offset > input.size() || input.size() - *offset < 4) {
    return false;
  }
  *output = static_cast<uint32_t>(input[*offset]) |
            (static_cast<uint32_t>(input[*offset + 1]) << 8) |
            (static_cast<uint32_t>(input[*offset + 2]) << 16) |
            (static_cast<uint32_t>(input[*offset + 3]) << 24);
  *offset += 4;
  return true;
}

std::vector<uint8_t> SerializeProtectedBlobEnvelope(
    const ProtectedBlobEnvelope& envelope) {
  std::vector<uint8_t> output;
  output.reserve(kProtectedBlobEnvelopeFixedLength + envelope.purpose.size() +
                 envelope.protected_blob.size());
  output.insert(output.end(), kProtectedBlobEnvelopeMagic,
                kProtectedBlobEnvelopeMagic + 8);
  AppendUint32(&output, kProtectedBlobEnvelopeVersion);
  AppendUint16(&output, static_cast<uint16_t>(envelope.purpose.size()));
  AppendUint32(&output, envelope.plaintext_length);
  AppendUint32(&output,
               static_cast<uint32_t>(envelope.protected_blob.size()));
  output.insert(output.end(), envelope.plaintext_sha256.begin(),
                envelope.plaintext_sha256.end());
  output.insert(output.end(), envelope.purpose.begin(),
                envelope.purpose.end());
  output.insert(output.end(), envelope.protected_blob.begin(),
                envelope.protected_blob.end());
  return output;
}

bool ParseProtectedBlobEnvelope(const std::vector<uint8_t>& input,
                                const std::string& expected_purpose,
                                size_t maximum_plaintext_length,
                                ProtectedBlobEnvelope* output) {
  if (output == nullptr || expected_purpose.empty() ||
      input.size() <
          kProtectedBlobEnvelopeFixedLength + expected_purpose.size() + 1 ||
      input.size() >
          kProtectedBlobEnvelopeFixedLength + expected_purpose.size() +
              kProtectedMetadataBlobMaximumLength ||
      !ConstantTimeEquals(
          input.data(), 8,
          reinterpret_cast<const uint8_t*>(kProtectedBlobEnvelopeMagic), 8)) {
    return false;
  }

  size_t offset = 8;
  uint32_t version = 0;
  uint16_t purpose_length = 0;
  uint32_t plaintext_length = 0;
  uint32_t protected_blob_length = 0;
  if (!ReadUint32(input, &offset, &version) ||
      !ReadUint16(input, &offset, &purpose_length) ||
      !ReadUint32(input, &offset, &plaintext_length) ||
      !ReadUint32(input, &offset, &protected_blob_length) ||
      version != kProtectedBlobEnvelopeVersion || purpose_length == 0 ||
      static_cast<size_t>(purpose_length) != expected_purpose.size() ||
      plaintext_length == 0 ||
      static_cast<size_t>(plaintext_length) > maximum_plaintext_length ||
      protected_blob_length == 0 ||
      static_cast<size_t>(protected_blob_length) >
          kProtectedMetadataBlobMaximumLength ||
      input.size() - offset < output->plaintext_sha256.size()) {
    return false;
  }
  std::copy_n(input.data() + offset, output->plaintext_sha256.size(),
              output->plaintext_sha256.data());
  offset += output->plaintext_sha256.size();
  const size_t expected_remaining =
      static_cast<size_t>(purpose_length) +
      static_cast<size_t>(protected_blob_length);
  if (input.size() - offset != expected_remaining) {
    return false;
  }
  output->purpose.assign(
      reinterpret_cast<const char*>(input.data() + offset), purpose_length);
  offset += purpose_length;
  if (!ConstantTimeEquals(
          reinterpret_cast<const uint8_t*>(output->purpose.data()),
          output->purpose.size(),
          reinterpret_cast<const uint8_t*>(expected_purpose.data()),
          expected_purpose.size())) {
    return false;
  }
  output->plaintext_length = plaintext_length;
  output->protected_blob.assign(input.data() + offset, input.data() + input.size());
  return true;
}

size_t ProtectedMetadataEnvelopeMaximum(const std::string& purpose) {
  return kProtectedBlobEnvelopeFixedLength + purpose.size() +
         kProtectedMetadataBlobMaximumLength;
}

OperationResult ReadProtectedMetadata(const std::wstring& path,
                                      const std::string& purpose,
                                      size_t maximum_plaintext_length,
                                      std::vector<uint8_t>* plaintext) {
  std::vector<uint8_t> envelope_bytes;
  const OperationResult read_result =
      ReadRegularFile(path, ProtectedMetadataEnvelopeMaximum(purpose),
                      &envelope_bytes);
  if (read_result != OperationResult::kSuccess) {
    return read_result;
  }
  ProtectedBlobEnvelope envelope;
  const bool valid =
      ParseProtectedBlobEnvelope(envelope_bytes, purpose,
                                 maximum_plaintext_length, &envelope) &&
      UnprotectMetadata(envelope, maximum_plaintext_length, plaintext);
  Wipe(&envelope_bytes);
  Wipe(&envelope.protected_blob);
  SecureZeroMemory(envelope.plaintext_sha256.data(),
                   envelope.plaintext_sha256.size());
  return valid ? OperationResult::kSuccess : OperationResult::kFailed;
}

OperationResult CreateProtectedMetadata(
    const std::wstring& directory,
    const std::wstring& path,
    const std::string& purpose,
    const std::vector<uint8_t>& plaintext,
    size_t maximum_plaintext_length) {
  ProtectedBlobEnvelope envelope;
  if (!ProtectMetadata(purpose, plaintext, maximum_plaintext_length,
                       &envelope)) {
    return OperationResult::kFailed;
  }
  std::vector<uint8_t> envelope_bytes =
      SerializeProtectedBlobEnvelope(envelope);
  Wipe(&envelope.protected_blob);
  SecureZeroMemory(envelope.plaintext_sha256.data(),
                   envelope.plaintext_sha256.size());
  const OperationResult create_result = AtomicCreate(
      directory, path, envelope_bytes,
      ProtectedMetadataEnvelopeMaximum(purpose));
  Wipe(&envelope_bytes);
  if (create_result != OperationResult::kSuccess) {
    return create_result;
  }

  std::vector<uint8_t> restored;
  const OperationResult restore_result =
      ReadProtectedMetadata(path, purpose, maximum_plaintext_length,
                            &restored);
  const bool equal =
      restore_result == OperationResult::kSuccess &&
      ConstantTimeEquals(plaintext.data(), plaintext.size(), restored.data(),
                         restored.size());
  Wipe(&restored);
  return equal ? OperationResult::kSuccess : OperationResult::kFailed;
}

OperationResult ReplaceProtectedMetadata(
    const std::wstring& directory,
    const std::wstring& path,
    const std::string& purpose,
    const std::vector<uint8_t>& plaintext,
    const std::string& expected_plaintext_sha256,
    size_t maximum_plaintext_length) {
  if (!IsLowerSha256(expected_plaintext_sha256)) {
    return OperationResult::kFailed;
  }

  std::vector<uint8_t> current_envelope;
  const OperationResult current_read = ReadRegularFile(
      path, ProtectedMetadataEnvelopeMaximum(purpose), &current_envelope);
  if (current_read != OperationResult::kSuccess) {
    return current_read;
  }
  ProtectedBlobEnvelope current;
  std::vector<uint8_t> current_plaintext;
  const bool current_valid =
      ParseProtectedBlobEnvelope(current_envelope, purpose,
                                 maximum_plaintext_length, &current) &&
      UnprotectMetadata(current, maximum_plaintext_length,
                        &current_plaintext);
  Wipe(&current.protected_blob);
  SecureZeroMemory(current.plaintext_sha256.data(),
                   current.plaintext_sha256.size());
  std::array<uint8_t, 32> current_plaintext_digest{};
  std::array<uint8_t, 32> current_envelope_digest{};
  const bool digest_valid =
      current_valid && Sha256(current_plaintext, &current_plaintext_digest) &&
      Sha256(current_envelope, &current_envelope_digest);
  Wipe(&current_plaintext);
  if (!digest_valid) {
    Wipe(&current_envelope);
    return OperationResult::kFailed;
  }
  const std::string current_plaintext_sha256 = HexLower(
      current_plaintext_digest.data(), current_plaintext_digest.size());
  const bool expected_matches = ConstantTimeEquals(
      reinterpret_cast<const uint8_t*>(current_plaintext_sha256.data()),
      current_plaintext_sha256.size(),
      reinterpret_cast<const uint8_t*>(expected_plaintext_sha256.data()),
      expected_plaintext_sha256.size());
  SecureZeroMemory(current_plaintext_digest.data(),
                   current_plaintext_digest.size());
  if (!expected_matches) {
    Wipe(&current_envelope);
    SecureZeroMemory(current_envelope_digest.data(),
                     current_envelope_digest.size());
    return OperationResult::kStale;
  }

  ProtectedBlobEnvelope replacement;
  if (!ProtectMetadata(purpose, plaintext, maximum_plaintext_length,
                       &replacement)) {
    Wipe(&current_envelope);
    SecureZeroMemory(current_envelope_digest.data(),
                     current_envelope_digest.size());
    return OperationResult::kFailed;
  }
  std::vector<uint8_t> replacement_bytes =
      SerializeProtectedBlobEnvelope(replacement);
  Wipe(&replacement.protected_blob);
  SecureZeroMemory(replacement.plaintext_sha256.data(),
                   replacement.plaintext_sha256.size());
  const std::string expected_envelope_sha256 =
      HexLower(current_envelope_digest.data(), current_envelope_digest.size());
  Wipe(&current_envelope);
  SecureZeroMemory(current_envelope_digest.data(),
                   current_envelope_digest.size());
  const OperationResult replace_result = AtomicReplace(
      directory, path, replacement_bytes, expected_envelope_sha256,
      ProtectedMetadataEnvelopeMaximum(purpose));
  Wipe(&replacement_bytes);
  if (replace_result != OperationResult::kSuccess) {
    return replace_result;
  }

  std::vector<uint8_t> restored;
  const OperationResult restore_result =
      ReadProtectedMetadata(path, purpose, maximum_plaintext_length,
                            &restored);
  const bool equal =
      restore_result == OperationResult::kSuccess &&
      ConstantTimeEquals(plaintext.data(), plaintext.size(), restored.data(),
                         restored.size());
  Wipe(&restored);
  return equal ? OperationResult::kSuccess : OperationResult::kFailed;
}

OperationResult DeleteProtectedMetadata(
    const std::wstring& path,
    const std::string& purpose,
    const std::string& expected_plaintext_sha256,
    bool allow_absent,
    size_t maximum_plaintext_length) {
  if (!IsLowerSha256(expected_plaintext_sha256)) {
    return OperationResult::kFailed;
  }
  std::vector<uint8_t> plaintext;
  const OperationResult read_result =
      ReadProtectedMetadata(path, purpose, maximum_plaintext_length,
                            &plaintext);
  if (read_result == OperationResult::kNotFound) {
    return allow_absent ? OperationResult::kSuccess
                        : OperationResult::kFailed;
  }
  if (read_result != OperationResult::kSuccess) {
    return read_result;
  }
  std::array<uint8_t, 32> digest{};
  const bool digest_ok = Sha256(plaintext, &digest);
  Wipe(&plaintext);
  if (!digest_ok) {
    return OperationResult::kFailed;
  }
  const std::string actual_sha256 = HexLower(digest.data(), digest.size());
  SecureZeroMemory(digest.data(), digest.size());
  if (!ConstantTimeEquals(
          reinterpret_cast<const uint8_t*>(actual_sha256.data()),
          actual_sha256.size(),
          reinterpret_cast<const uint8_t*>(expected_plaintext_sha256.data()),
          expected_plaintext_sha256.size())) {
    return OperationResult::kStale;
  }
  const OperationResult delete_result = DeleteRegularFile(path);
  return delete_result == OperationResult::kSuccess &&
                 InspectRegularFile(path) == OperationResult::kNotFound
             ? OperationResult::kSuccess
             : OperationResult::kFailed;
}

std::vector<uint8_t> SerializeKeyEnvelope(
    const std::string& vault_id,
    const std::array<uint8_t, 32>& key_sha256,
    const std::vector<uint8_t>& protected_blob) {
  std::vector<uint8_t> output;
  output.reserve(kKeyEnvelopeFixedLength + vault_id.size() +
                 protected_blob.size());
  output.insert(output.end(), kKeyEnvelopeMagic,
                kKeyEnvelopeMagic + 8);
  AppendUint32(&output, kKeyEnvelopeVersion);
  AppendUint16(&output, static_cast<uint16_t>(vault_id.size()));
  AppendUint32(&output, static_cast<uint32_t>(protected_blob.size()));
  output.insert(output.end(), key_sha256.begin(), key_sha256.end());
  output.insert(output.end(), vault_id.begin(), vault_id.end());
  output.insert(output.end(), protected_blob.begin(), protected_blob.end());
  return output;
}

bool ParseKeyEnvelope(const std::vector<uint8_t>& input,
                      const std::string& expected_vault_id,
                      KeyEnvelope* output) {
  if (output == nullptr ||
      input.size() < kKeyEnvelopeFixedLength + 1 ||
      input.size() >
          kKeyEnvelopeFixedLength + kVaultIdMaximumLength +
              kProtectedBlobMaximumLength ||
      !ConstantTimeEquals(input.data(), 8,
                          reinterpret_cast<const uint8_t*>(
                              kKeyEnvelopeMagic),
                          8)) {
    return false;
  }
  size_t offset = 8;
  uint32_t version = 0;
  uint16_t vault_id_length = 0;
  uint32_t protected_blob_length = 0;
  if (!ReadUint32(input, &offset, &version) ||
      !ReadUint16(input, &offset, &vault_id_length) ||
      !ReadUint32(input, &offset, &protected_blob_length) ||
      version != kKeyEnvelopeVersion || vault_id_length == 0 ||
      static_cast<size_t>(vault_id_length) > kVaultIdMaximumLength ||
      protected_blob_length == 0 ||
      static_cast<size_t>(protected_blob_length) >
          kProtectedBlobMaximumLength ||
      input.size() - offset < output->key_sha256.size()) {
    return false;
  }
  std::copy_n(input.data() + offset, output->key_sha256.size(),
              output->key_sha256.data());
  offset += output->key_sha256.size();
  const size_t expected_remaining =
      static_cast<size_t>(vault_id_length) +
      static_cast<size_t>(protected_blob_length);
  if (input.size() - offset != expected_remaining) {
    return false;
  }
  output->vault_id.assign(
      reinterpret_cast<const char*>(input.data() + offset),
      vault_id_length);
  offset += vault_id_length;
  if (output->vault_id != expected_vault_id) {
    return false;
  }
  output->protected_blob.assign(input.data() + offset, input.data() + input.size());
  return true;
}

bool IsValidVaultId(const std::string& value) {
  if (value.empty() || value.size() > kVaultIdMaximumLength) {
    return false;
  }
  for (const unsigned char character : value) {
    if (!((character >= 'A' && character <= 'Z') ||
          (character >= 'a' && character <= 'z') ||
          (character >= '0' && character <= '9') || character == '_' ||
          character == '-')) {
      return false;
    }
  }
  std::string lower;
  lower.reserve(value.size());
  for (const unsigned char character : value) {
    lower.push_back(
        static_cast<char>(std::tolower(static_cast<int>(character))));
  }
  return lower != "saved_search" && lower != "saved_job" &&
         lower != "application_note" && lower != "profile_snippet" &&
         lower != "draft_metadata";
}

std::vector<uint8_t> SelectedVaultPlaintext(const std::string& vault_id) {
  const std::string value =
      std::string("{\"format\":\"atlasvault-windows-selected-vault\",") +
      "\"vault_id\":\"" + vault_id + "\",\"version\":1}";
  return std::vector<uint8_t>(value.begin(), value.end());
}

bool ParseSelectedVaultPlaintext(const std::vector<uint8_t>& plaintext,
                                 std::string* vault_id) {
  if (vault_id == nullptr || plaintext.empty() ||
      plaintext.size() > kSelectedVaultPlaintextMaximumLength) {
    return false;
  }
  constexpr char kPrefix[] =
      "{\"format\":\"atlasvault-windows-selected-vault\","
      "\"vault_id\":\"";
  constexpr char kSuffix[] = "\",\"version\":1}";
  constexpr size_t kPrefixLength = sizeof(kPrefix) - 1;
  constexpr size_t kSuffixLength = sizeof(kSuffix) - 1;
  if (plaintext.size() <= kPrefixLength + kSuffixLength ||
      !ConstantTimeEquals(
          plaintext.data(), kPrefixLength,
          reinterpret_cast<const uint8_t*>(kPrefix), kPrefixLength) ||
      !ConstantTimeEquals(
          plaintext.data() + plaintext.size() - kSuffixLength,
          kSuffixLength, reinterpret_cast<const uint8_t*>(kSuffix),
          kSuffixLength)) {
    return false;
  }
  const size_t vault_id_length =
      plaintext.size() - kPrefixLength - kSuffixLength;
  vault_id->assign(
      reinterpret_cast<const char*>(plaintext.data() + kPrefixLength),
      vault_id_length);
  if (!IsValidVaultId(*vault_id)) {
    vault_id->clear();
    return false;
  }
  const std::vector<uint8_t> canonical = SelectedVaultPlaintext(*vault_id);
  const bool matches = ConstantTimeEquals(
      plaintext.data(), plaintext.size(), canonical.data(), canonical.size());
  if (!matches) {
    vault_id->clear();
  }
  return matches;
}

bool ProbeCurrentUserDpapi() {
  std::vector<uint8_t> probe_key(kVaultKeyLength);
  for (size_t index = 0; index < probe_key.size(); ++index) {
    probe_key[index] = static_cast<uint8_t>(index + 1);
  }
  std::array<uint8_t, 32> key_hash{};
  std::vector<uint8_t> protected_blob;
  std::vector<uint8_t> recovered_key;
  const bool protected_ok =
      ProtectVaultKey(kCapabilityProbeVaultId, probe_key, &key_hash,
                      &protected_blob);
  KeyEnvelope envelope;
  envelope.key_sha256 = key_hash;
  envelope.vault_id = kCapabilityProbeVaultId;
  envelope.protected_blob = std::move(protected_blob);
  const bool unprotected_ok =
      protected_ok && UnprotectVaultKey(envelope, &recovered_key);
  const bool matched =
      unprotected_ok &&
      ConstantTimeEquals(probe_key.data(), probe_key.size(),
                         recovered_key.data(), recovered_key.size());
  Wipe(&probe_key);
  Wipe(&envelope.protected_blob);
  Wipe(&recovered_key);
  SecureZeroMemory(key_hash.data(), key_hash.size());
  return matched;
}

bool ProbeAtomicReplacement(const std::wstring& directory) {
  std::array<uint8_t, 16> random{};
  if (!IsNtSuccess(BCryptGenRandom(
          nullptr, random.data(), static_cast<ULONG>(random.size()),
          BCRYPT_USE_SYSTEM_PREFERRED_RNG))) {
    return false;
  }
  const std::wstring destination =
      JoinPath(directory,
               L".atlasvault-capability-" +
                   WidenAscii(HexLower(random.data(), random.size())) +
                   L".probe");
  std::vector<uint8_t> initial{'A', 'V', 'W', '1'};
  std::vector<uint8_t> replacement{'A', 'V', 'W', '2'};
  const OperationResult create_result =
      AtomicCreate(directory, destination, initial, 64);
  if (create_result != OperationResult::kSuccess) {
    if (create_result != OperationResult::kAlreadyExists) {
      DeleteRegularFile(destination);
    }
    Wipe(&initial);
    Wipe(&replacement);
    return false;
  }
  std::array<uint8_t, 32> digest{};
  const bool digest_ok = Sha256(initial, &digest);
  const OperationResult replace_result =
      digest_ok
          ? AtomicReplace(directory, destination, replacement,
                          HexLower(digest.data(), digest.size()), 64)
          : OperationResult::kFailed;
  const OperationResult delete_result = DeleteRegularFile(destination);
  Wipe(&initial);
  Wipe(&replacement);
  SecureZeroMemory(digest.data(), digest.size());
  return replace_result == OperationResult::kSuccess &&
         delete_result == OperationResult::kSuccess;
}

StorageCapabilities ProbeStorageCapabilities() {
  StorageCapabilities capabilities;
  capabilities.dpapi_available = ProbeCurrentUserDpapi();
  capabilities.current_user_scope = capabilities.dpapi_available;

  VaultPaths paths;
  capabilities.local_app_data_available =
      PrepareVaultPaths(kCapabilityProbeVaultId, &paths);
  bool lock_available = false;
  if (capabilities.local_app_data_available) {
    ScopedVaultLock lock;
    lock_available = lock.Acquire(paths.lock_path);
  }
  capabilities.atomic_replace_available =
      capabilities.local_app_data_available &&
      ProbeAtomicReplacement(paths.vault_directory);
  capabilities.secure_boundary_available =
      capabilities.dpapi_available && capabilities.current_user_scope &&
      capabilities.local_app_data_available &&
      capabilities.atomic_replace_available && lock_available;
  return capabilities;
}

bool AuthorizeFreshUserConsent(HWND owner_window, const wchar_t* prompt) {
  try {
    if (owner_window == nullptr || !IsWindow(owner_window) || prompt == nullptr) {
      return false;
    }
    ScopedRoInitialization apartment;
    if (!apartment.available()) {
      return false;
    }
    using winrt::Windows::Security::Credentials::UI::UserConsentVerifier;
    using winrt::Windows::Security::Credentials::UI::
        UserConsentVerifierAvailability;
    using winrt::Windows::Security::Credentials::UI::
        UserConsentVerificationResult;
    if (UserConsentVerifier::CheckAvailabilityAsync().get() !=
        UserConsentVerifierAvailability::Available) {
      return false;
    }
    const winrt::hstring message(prompt);
    auto interop = winrt::get_activation_factory<
        UserConsentVerifier, IUserConsentVerifierInterop>();
    winrt::Windows::Foundation::IAsyncOperation<
        UserConsentVerificationResult>
        verification{nullptr};
    winrt::check_hresult(interop->RequestVerificationForWindowAsync(
        owner_window, static_cast<HSTRING>(winrt::get_abi(message)),
        winrt::guid_of<decltype(verification)>(), winrt::put_abi(verification)));
    return verification.get() == UserConsentVerificationResult::Verified;
  } catch (...) {
    return false;
  }
}

bool AuthorizePairingKeyRelease(HWND owner_window) {
  return AuthorizeFreshUserConsent(
      owner_window, L"Authorize AtlasVault vault-key delivery");
}

bool AuthorizeDeviceRemoval(HWND owner_window) {
  return AuthorizeFreshUserConsent(
      owner_window, L"Authorize AtlasVault device removal");
}

const flutter::EncodableMap* ExactArguments(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    const std::vector<std::string>& expected_keys) {
  if (call.arguments() == nullptr) {
    return nullptr;
  }
  const auto* arguments =
      std::get_if<flutter::EncodableMap>(call.arguments());
  if (arguments == nullptr || arguments->size() != expected_keys.size()) {
    return nullptr;
  }
  for (const std::string& key : expected_keys) {
    if (arguments->find(flutter::EncodableValue(key)) == arguments->end()) {
      return nullptr;
    }
  }
  return arguments;
}

bool HasNoArguments(
    const flutter::MethodCall<flutter::EncodableValue>& call) {
  return call.arguments() == nullptr ||
         std::holds_alternative<std::monostate>(*call.arguments());
}

const std::string* StringArgument(const flutter::EncodableMap& arguments,
                                  const std::string& key) {
  const auto iterator = arguments.find(flutter::EncodableValue(key));
  return iterator == arguments.end()
             ? nullptr
             : std::get_if<std::string>(&iterator->second);
}

const std::vector<uint8_t>* BytesArgument(
    const flutter::EncodableMap& arguments,
    const std::string& key) {
  const auto iterator = arguments.find(flutter::EncodableValue(key));
  return iterator == arguments.end()
             ? nullptr
             : std::get_if<std::vector<uint8_t>>(&iterator->second);
}

const bool* BooleanArgument(const flutter::EncodableMap& arguments,
                            const std::string& key) {
  const auto iterator = arguments.find(flutter::EncodableValue(key));
  return iterator == arguments.end()
             ? nullptr
             : std::get_if<bool>(&iterator->second);
}

void ReturnFailure(
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result,
    OperationResult failure = OperationResult::kFailed) {
  std::string code = "storage_failed";
  if (failure == OperationResult::kAlreadyExists) {
    code = "already_exists";
  } else if (failure == OperationResult::kStale) {
    code = "stale_digest";
  }
  result->Error(code, "Windows AtlasVault storage operation failed.");
}

bool BeginOperation(const std::string& vault_id,
                    VaultPaths* paths,
                    ScopedVaultLock* lock) {
  return IsValidVaultId(vault_id) && PrepareVaultPaths(vault_id, paths) &&
         lock->Acquire(paths->lock_path);
}

void ReturnBytes(
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result,
    std::vector<uint8_t>* bytes,
    bool sensitive) {
  flutter::EncodableValue value(std::move(*bytes));
  result->Success(value);
  if (sensitive) {
    auto* encoded = std::get_if<std::vector<uint8_t>>(&value);
    if (encoded != nullptr && !encoded->empty()) {
      SecureZeroMemory(encoded->data(), encoded->size());
    }
  }
}

void WipeSensitiveArgument(const std::string& method,
                           flutter::EncodableValue* arguments) {
  if (arguments == nullptr) {
    return;
  }
  auto* values = std::get_if<flutter::EncodableMap>(arguments);
  if (values == nullptr) {
    return;
  }
  const char* key = nullptr;
  if (method == "createVaultKey") {
    key = "vault_key";
  } else if (method == "createDeviceIdentitySecret") {
    key = "secret_bytes";
  } else if (method == "saveEncryptedExport") {
    key = "export_bytes";
  } else if (method == "savePairingArtifact" ||
             method == "createStagedPairingArtifact") {
    key = "artifact_bytes";
  } else if (method == "createTrustedDeviceRegistry" ||
             method == "replaceTrustedDeviceRegistry" ||
             method == "createPairingReplayStore" ||
             method == "replacePairingReplayStore") {
    key = "state_bytes";
  } else if (method == "createPairingTransaction" ||
             method == "replacePairingTransaction") {
    key = "transaction_bytes";
  } else if (method == "createPlaintextMigrationJournal" ||
             method == "replacePlaintextMigrationJournal" ||
             method == "createRecoveryImportJournal" ||
             method == "replaceRecoveryImportJournal") {
    key = "journal_bytes";
  }
  if (key == nullptr) {
    return;
  }
  const auto iterator = values->find(flutter::EncodableValue(key));
  if (iterator == values->end()) {
    return;
  }
  auto* bytes = std::get_if<std::vector<uint8_t>>(&iterator->second);
  Wipe(bytes);
}

class ScopedSensitiveArgumentWiper {
 public:
  ScopedSensitiveArgumentWiper(const std::string& method,
                               flutter::EncodableValue* arguments)
      : method_(method), arguments_(arguments) {}
  ~ScopedSensitiveArgumentWiper() {
    WipeSensitiveArgument(method_, arguments_);
  }

  ScopedSensitiveArgumentWiper(const ScopedSensitiveArgumentWiper&) = delete;
  ScopedSensitiveArgumentWiper& operator=(
      const ScopedSensitiveArgumentWiper&) = delete;

 private:
  const std::string& method_;
  flutter::EncodableValue* arguments_;
};

}  // namespace

class AtlasVaultWindowsStorageWorker {
 public:
  using Result = flutter::MethodResult<flutter::EncodableValue>;
  using ExecuteCallback = std::function<void(
      std::string,
      std::unique_ptr<flutter::EncodableValue>,
      std::unique_ptr<Result>)>;
  using PickCallback = std::function<void(
      std::wstring,
      std::unique_ptr<Result>)>;
  using SaveCallback = std::function<void(
      std::wstring,
      std::vector<uint8_t>,
      std::unique_ptr<Result>)>;

  AtlasVaultWindowsStorageWorker(ExecuteCallback execute,
                                 PickCallback pick,
                                 SaveCallback save)
      : execute_(std::move(execute)),
        pick_(std::move(pick)),
        save_(std::move(save)),
        worker_(&AtlasVaultWindowsStorageWorker::Run, this) {}

  ~AtlasVaultWindowsStorageWorker() { StopAndDrain(); }

  AtlasVaultWindowsStorageWorker(const AtlasVaultWindowsStorageWorker&) =
      delete;
  AtlasVaultWindowsStorageWorker& operator=(
      const AtlasVaultWindowsStorageWorker&) = delete;

  void Enqueue(
      std::string method,
      std::unique_ptr<flutter::EncodableValue> arguments,
      std::unique_ptr<Result> result) {
    PendingOperation operation;
    operation.kind = PendingOperation::Kind::kMethod;
    operation.method = std::move(method);
    operation.arguments = std::move(arguments);
    operation.result = std::move(result);
    bool rejected = false;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (stopping_) {
        rejected = true;
      } else {
        pending_.push_back(std::move(operation));
      }
    }
    if (rejected) {
      WipeSensitiveArgument(operation.method, operation.arguments.get());
      ReturnFailure(std::move(operation.result));
      return;
    }
    condition_.notify_one();
  }

  bool EnqueueSave(std::wstring destination_path,
                   std::vector<uint8_t> encrypted_bytes,
                   std::unique_ptr<Result> result) {
    PendingOperation operation;
    operation.kind = PendingOperation::Kind::kSaveEncryptedExport;
    operation.destination_path = std::move(destination_path);
    operation.encrypted_bytes = std::move(encrypted_bytes);
    operation.result = std::move(result);
    bool rejected = false;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (stopping_) {
        rejected = true;
      } else {
        pending_.push_back(std::move(operation));
      }
    }
    if (rejected) {
      Wipe(&operation.encrypted_bytes);
      ReturnFailure(std::move(operation.result));
      return false;
    }
    condition_.notify_one();
    return true;
  }

  bool EnqueuePick(std::wstring selected_path,
                   std::unique_ptr<Result> result) {
    PendingOperation operation;
    operation.kind = PendingOperation::Kind::kPickEncryptedExport;
    operation.destination_path = std::move(selected_path);
    operation.result = std::move(result);
    bool rejected = false;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (stopping_) {
        rejected = true;
      } else {
        pending_.push_back(std::move(operation));
      }
    }
    if (rejected) {
      if (!operation.destination_path.empty()) {
        SecureZeroMemory(operation.destination_path.data(),
                         operation.destination_path.size() *
                             sizeof(wchar_t));
        operation.destination_path.clear();
      }
      ReturnFailure(std::move(operation.result));
      return false;
    }
    condition_.notify_one();
    return true;
  }

  void StopAndDrain() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      stopping_ = true;
    }
    condition_.notify_all();
    if (worker_.joinable()) {
      worker_.join();
    }
  }

 private:
  struct PendingOperation {
    enum class Kind {
      kMethod,
      kPickEncryptedExport,
      kSaveEncryptedExport,
    };

    Kind kind = Kind::kMethod;
    std::string method;
    std::unique_ptr<flutter::EncodableValue> arguments;
    std::wstring destination_path;
    std::vector<uint8_t> encrypted_bytes;
    std::unique_ptr<Result> result;
  };

  void Run() {
    while (true) {
      PendingOperation operation;
      {
        std::unique_lock<std::mutex> lock(mutex_);
        condition_.wait(lock,
                        [this] { return stopping_ || !pending_.empty(); });
        if (pending_.empty()) {
          if (stopping_) {
            return;
          }
          continue;
        }
        operation = std::move(pending_.front());
        pending_.pop_front();
      }
      if (operation.kind == PendingOperation::Kind::kPickEncryptedExport) {
        pick_(std::move(operation.destination_path),
              std::move(operation.result));
      } else if (operation.kind ==
                 PendingOperation::Kind::kSaveEncryptedExport) {
        save_(std::move(operation.destination_path),
              std::move(operation.encrypted_bytes),
              std::move(operation.result));
      } else {
        execute_(std::move(operation.method),
                 std::move(operation.arguments),
                 std::move(operation.result));
      }
    }
  }

  ExecuteCallback execute_;
  PickCallback pick_;
  SaveCallback save_;
  std::mutex mutex_;
  std::condition_variable condition_;
  std::deque<PendingOperation> pending_;
  bool stopping_ = false;
  std::thread worker_;
};

AtlasVaultWindowsStorage::AtlasVaultWindowsStorage(
    flutter::BinaryMessenger* messenger,
    const std::string& channel_name,
    HWND owner_window)
    : owner_window_(owner_window),
      channel_(std::make_unique<
               flutter::MethodChannel<flutter::EncodableValue>>(
          messenger, channel_name,
          &flutter::StandardMethodCodec::GetInstance())),
      worker_(std::make_unique<AtlasVaultWindowsStorageWorker>(
          [this](
              std::string method,
              std::unique_ptr<flutter::EncodableValue> arguments,
              std::unique_ptr<
                  flutter::MethodResult<flutter::EncodableValue>> result) {
            ExecuteMethodCall(std::move(method), std::move(arguments),
                              std::move(result));
          },
          [this](
              std::wstring selected_path,
              std::unique_ptr<
                  flutter::MethodResult<flutter::EncodableValue>> result) {
            ExecutePickEncryptedExport(std::move(selected_path),
                                       std::move(result));
          },
          [this](
              std::wstring destination_path,
              std::vector<uint8_t> encrypted_bytes,
              std::unique_ptr<
                  flutter::MethodResult<flutter::EncodableValue>> result) {
            ExecuteSaveEncryptedExport(std::move(destination_path),
                                       std::move(encrypted_bytes),
                                       std::move(result));
          })) {
  channel_->SetMethodCallHandler(
      [this](
          const flutter::MethodCall<flutter::EncodableValue>& call,
          std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>>
              result) {
        HandleMethodCall(call, std::move(result));
      });
}

AtlasVaultWindowsStorage::~AtlasVaultWindowsStorage() {
  channel_->SetMethodCallHandler(nullptr);
  worker_->StopAndDrain();
  document_operation_pending_.store(false);
  owner_window_ = nullptr;
}

void AtlasVaultWindowsStorage::HandleMethodCall(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  if (call.method_name() == "pickEncryptedExport") {
    HandlePickEncryptedExport(call, std::move(result));
    return;
  }
  if (call.method_name() == "saveEncryptedExport") {
    HandleSaveEncryptedExport(call, std::move(result));
    return;
  }
  if (call.method_name() == "pickPairingArtifact") {
    HandlePickPairingArtifact(call, std::move(result));
    return;
  }
  if (call.method_name() == "savePairingArtifact") {
    HandleSavePairingArtifact(call, std::move(result));
    return;
  }
  std::unique_ptr<flutter::EncodableValue> arguments;
  if (call.arguments() != nullptr) {
    arguments =
        std::make_unique<flutter::EncodableValue>(*call.arguments());
  }
  worker_->Enqueue(call.method_name(), std::move(arguments),
                   std::move(result));
}

void AtlasVaultWindowsStorage::HandlePickEncryptedExport(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  if (!HasNoArguments(call) || document_operation_pending_.exchange(true)) {
    ReturnFailure(std::move(result));
    return;
  }

  std::wstring selected_path;
  const DocumentDialogResult dialog_result =
      SelectDocumentSource(
          owner_window_, L"AtlasVault encrypted backup (*.atlasvault)",
          L"*.atlasvault", L"atlasvault", &selected_path);
  if (dialog_result == DocumentDialogResult::kCancelled) {
    document_operation_pending_.store(false);
    result->Success(flutter::EncodableValue());
    return;
  }
  if (dialog_result != DocumentDialogResult::kSelected) {
    document_operation_pending_.store(false);
    ReturnFailure(std::move(result));
    return;
  }

  if (!worker_->EnqueuePick(std::move(selected_path), std::move(result))) {
    document_operation_pending_.store(false);
  }
}

void AtlasVaultWindowsStorage::ExecutePickEncryptedExport(
    std::wstring selected_path,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  std::vector<uint8_t> encrypted_bytes;
  const OperationResult read_result = ReadRegularFile(
      selected_path, kEncryptedDocumentMaximumLength, &encrypted_bytes);
  if (!selected_path.empty()) {
    SecureZeroMemory(selected_path.data(),
                     selected_path.size() * sizeof(wchar_t));
    selected_path.clear();
  }
  document_operation_pending_.store(false);
  if (read_result != OperationResult::kSuccess || encrypted_bytes.empty()) {
    Wipe(&encrypted_bytes);
    ReturnFailure(std::move(result), read_result);
    return;
  }
  ReturnBytes(std::move(result), &encrypted_bytes, true);
}

void AtlasVaultWindowsStorage::HandleSaveEncryptedExport(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  const flutter::EncodableMap* arguments =
      ExactArguments(call, {"export_bytes"});
  const std::vector<uint8_t>* supplied =
      arguments == nullptr ? nullptr
                           : BytesArgument(*arguments, "export_bytes");
  if (supplied == nullptr || supplied->empty() ||
      supplied->size() > kEncryptedDocumentMaximumLength ||
      document_operation_pending_.exchange(true)) {
    ReturnFailure(std::move(result));
    return;
  }

  std::vector<uint8_t> encrypted_bytes(*supplied);
  std::wstring destination_path;
  const DocumentDialogResult dialog_result =
      SelectDocumentDestination(
          owner_window_, L"AtlasVault encrypted backup (*.atlasvault)",
          L"*.atlasvault", L"atlasvault",
          L"AtlasVault-Encrypted-Backup.atlasvault", &destination_path);
  if (dialog_result == DocumentDialogResult::kCancelled) {
    Wipe(&encrypted_bytes);
    document_operation_pending_.store(false);
    result->Success(flutter::EncodableValue(false));
    return;
  }
  if (dialog_result != DocumentDialogResult::kSelected) {
    Wipe(&encrypted_bytes);
    document_operation_pending_.store(false);
    ReturnFailure(std::move(result));
    return;
  }

  if (!worker_->EnqueueSave(std::move(destination_path),
                            std::move(encrypted_bytes),
                            std::move(result))) {
    document_operation_pending_.store(false);
  }
}

void AtlasVaultWindowsStorage::ExecuteSaveEncryptedExport(
    std::wstring destination_path,
    std::vector<uint8_t> encrypted_bytes,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  const OperationResult save_result =
      SaveEncryptedExportAtomically(destination_path, encrypted_bytes);
  Wipe(&encrypted_bytes);
  if (!destination_path.empty()) {
    SecureZeroMemory(destination_path.data(),
                     destination_path.size() * sizeof(wchar_t));
    destination_path.clear();
  }
  document_operation_pending_.store(false);
  if (save_result != OperationResult::kSuccess) {
    ReturnFailure(std::move(result), save_result);
    return;
  }
  result->Success(flutter::EncodableValue(true));
}

void AtlasVaultWindowsStorage::HandlePickPairingArtifact(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  if (!HasNoArguments(call) || document_operation_pending_.exchange(true)) {
    ReturnFailure(std::move(result));
    return;
  }
  std::wstring selected_path;
  const DocumentDialogResult dialog_result = SelectDocumentSource(
      owner_window_, L"AtlasVault pairing artifact (*.atlaspair)",
      L"*.atlaspair", L"atlaspair", &selected_path);
  if (dialog_result == DocumentDialogResult::kCancelled) {
    document_operation_pending_.store(false);
    result->Success(flutter::EncodableValue());
    return;
  }
  if (dialog_result != DocumentDialogResult::kSelected) {
    document_operation_pending_.store(false);
    ReturnFailure(std::move(result));
    return;
  }
  if (!worker_->EnqueuePick(std::move(selected_path), std::move(result))) {
    document_operation_pending_.store(false);
  }
}

void AtlasVaultWindowsStorage::HandleSavePairingArtifact(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  const flutter::EncodableMap* arguments =
      ExactArguments(call, {"artifact_bytes"});
  const std::vector<uint8_t>* supplied =
      arguments == nullptr ? nullptr
                           : BytesArgument(*arguments, "artifact_bytes");
  if (supplied == nullptr || supplied->empty() ||
      supplied->size() > kEncryptedDocumentMaximumLength ||
      document_operation_pending_.exchange(true)) {
    ReturnFailure(std::move(result));
    return;
  }
  std::vector<uint8_t> artifact_bytes(*supplied);
  std::wstring destination_path;
  const DocumentDialogResult dialog_result = SelectDocumentDestination(
      owner_window_, L"AtlasVault pairing artifact (*.atlaspair)",
      L"*.atlaspair", L"atlaspair", L"AtlasVault-Pairing.atlaspair",
      &destination_path);
  if (dialog_result == DocumentDialogResult::kCancelled) {
    Wipe(&artifact_bytes);
    document_operation_pending_.store(false);
    result->Success(flutter::EncodableValue(false));
    return;
  }
  if (dialog_result != DocumentDialogResult::kSelected) {
    Wipe(&artifact_bytes);
    document_operation_pending_.store(false);
    ReturnFailure(std::move(result));
    return;
  }
  if (!worker_->EnqueueSave(std::move(destination_path),
                            std::move(artifact_bytes),
                            std::move(result))) {
    document_operation_pending_.store(false);
  }
}

void AtlasVaultWindowsStorage::ExecuteMethodCall(
    std::string method_name,
    std::unique_ptr<flutter::EncodableValue> encoded_arguments,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  flutter::EncodableValue* argument_value = encoded_arguments.get();
  flutter::MethodCall<flutter::EncodableValue> call(method_name,
                                                    std::move(encoded_arguments));
  ScopedSensitiveArgumentWiper argument_wiper(method_name, argument_value);
  const std::string& method = call.method_name();
  if (method == "capabilities") {
    if (!HasNoArguments(call)) {
      ReturnFailure(std::move(result));
      return;
    }
    const StorageCapabilities probed = ProbeStorageCapabilities();
    flutter::EncodableMap capabilities{
        {flutter::EncodableValue("secure_boundary_available"),
         flutter::EncodableValue(probed.secure_boundary_available)},
        {flutter::EncodableValue("dpapi_available"),
         flutter::EncodableValue(probed.dpapi_available)},
        {flutter::EncodableValue("current_user_scope"),
         flutter::EncodableValue(probed.current_user_scope)},
        {flutter::EncodableValue("local_app_data_available"),
         flutter::EncodableValue(probed.local_app_data_available)},
        {flutter::EncodableValue("atomic_replace_available"),
         flutter::EncodableValue(probed.atomic_replace_available)},
        {flutter::EncodableValue("hardware_backed_guaranteed"),
         flutter::EncodableValue(probed.hardware_backed_guaranteed)},
    };
    result->Success(flutter::EncodableValue(capabilities));
    return;
  }

  if (method == "authorizePairingKeyRelease" || method == "authorizeDeviceRemoval") {
    if (!HasNoArguments(call)) {
      result->Success(flutter::EncodableValue(false));
      return;
    }
    const bool authorized = method == "authorizePairingKeyRelease"
                                ? AuthorizePairingKeyRelease(owner_window_)
                                : AuthorizeDeviceRemoval(owner_window_);
    result->Success(flutter::EncodableValue(authorized));
    return;
  }

  const bool migration_metadata_method =
      method == "readPlaintextMigrationJournal" ||
      method == "createPlaintextMigrationJournal" ||
      method == "replacePlaintextMigrationJournal" ||
      method == "deletePlaintextMigrationJournal";
  const bool recovery_import_metadata_method =
      method == "readRecoveryImportJournal" ||
      method == "createRecoveryImportJournal" ||
      method == "replaceRecoveryImportJournal" ||
      method == "deleteRecoveryImportJournal";
  const bool selected_vault_method =
      method == "readSelectedVault" || method == "createSelectedVault" ||
      method == "clearSelectedVault";
  const bool device_identity_method =
      method == "createDeviceIdentitySecret" ||
      method == "loadDeviceIdentitySecret" ||
      method == "containsDeviceIdentitySecret" ||
      method == "deleteDeviceIdentitySecret";
  const bool trusted_devices_method =
      method == "readTrustedDeviceRegistry" ||
      method == "createTrustedDeviceRegistry" ||
      method == "replaceTrustedDeviceRegistry";
  const bool pairing_replay_method =
      method == "readPairingReplayStore" ||
      method == "createPairingReplayStore" ||
      method == "replacePairingReplayStore";
  const bool pairing_transaction_method =
      method == "readPairingTransaction" ||
      method == "createPairingTransaction" ||
      method == "replacePairingTransaction" ||
      method == "deletePairingTransaction";
  const bool pairing_staging_method =
      method == "readStagedPairingArtifact" ||
      method == "createStagedPairingArtifact" ||
      method == "deleteStagedPairingArtifact";
  if (migration_metadata_method || recovery_import_metadata_method ||
      selected_vault_method || device_identity_method ||
      trusted_devices_method || pairing_replay_method ||
      pairing_transaction_method || pairing_staging_method) {
    ProtectedMetadataPaths paths;
    if (!PrepareProtectedMetadataPaths(&paths)) {
      ReturnFailure(std::move(result));
      return;
    }
    ScopedVaultLock lock;
    const std::wstring& lock_path =
        migration_metadata_method
            ? paths.migration_lock_path
        : recovery_import_metadata_method
            ? paths.recovery_import_lock_path
        : selected_vault_method
            ? paths.selection_lock_path
        : device_identity_method
            ? paths.device_identity_lock_path
        : trusted_devices_method
            ? paths.trusted_devices_lock_path
        : pairing_replay_method
            ? paths.pairing_replay_lock_path
        : pairing_transaction_method
            ? paths.pairing_transaction_lock_path
            : paths.pairing_staging_lock_path;
    if (!lock.Acquire(lock_path)) {
      ReturnFailure(std::move(result));
      return;
    }

    if (method == "createDeviceIdentitySecret") {
      const flutter::EncodableMap* arguments =
          ExactArguments(call, {"secret_bytes"});
      const std::vector<uint8_t>* supplied =
          arguments == nullptr
              ? nullptr
              : BytesArgument(*arguments, "secret_bytes");
      if (supplied == nullptr || supplied->empty() ||
          supplied->size() > kDeviceIdentityPlaintextMaximumLength) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> secret(*supplied);
      const OperationResult create_result = CreateProtectedMetadata(
          paths.device_directory, paths.device_identity_path,
          kDeviceIdentityPurpose, secret,
          kDeviceIdentityPlaintextMaximumLength);
      Wipe(&secret);
      if (create_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), create_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "loadDeviceIdentitySecret") {
      if (!HasNoArguments(call)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> secret;
      const OperationResult read_result = ReadProtectedMetadata(
          paths.device_identity_path, kDeviceIdentityPurpose,
          kDeviceIdentityPlaintextMaximumLength, &secret);
      if (read_result == OperationResult::kNotFound) {
        result->Success(flutter::EncodableValue());
        return;
      }
      if (read_result != OperationResult::kSuccess) {
        Wipe(&secret);
        ReturnFailure(std::move(result));
        return;
      }
      ReturnBytes(std::move(result), &secret, true);
      return;
    }

    if (method == "containsDeviceIdentitySecret") {
      if (!HasNoArguments(call)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> secret;
      const OperationResult read_result = ReadProtectedMetadata(
          paths.device_identity_path, kDeviceIdentityPurpose,
          kDeviceIdentityPlaintextMaximumLength, &secret);
      Wipe(&secret);
      if (read_result != OperationResult::kSuccess &&
          read_result != OperationResult::kNotFound) {
        ReturnFailure(std::move(result));
        return;
      }
      result->Success(flutter::EncodableValue(
          read_result == OperationResult::kSuccess));
      return;
    }

    if (method == "deleteDeviceIdentitySecret") {
      if (!HasNoArguments(call)) {
        ReturnFailure(std::move(result));
        return;
      }
      const OperationResult delete_result =
          DeleteRegularFile(paths.device_identity_path);
      if (delete_result != OperationResult::kSuccess ||
          InspectRegularFile(paths.device_identity_path) !=
              OperationResult::kNotFound) {
        ReturnFailure(std::move(result), delete_result);
        return;
      }
      result->Success();
      return;
    }

    if (trusted_devices_method || pairing_replay_method ||
        pairing_transaction_method) {
      const std::wstring& path =
          trusted_devices_method
              ? paths.trusted_devices_path
          : pairing_replay_method
              ? paths.pairing_replay_path
              : paths.pairing_transaction_path;
      const char* purpose =
          trusted_devices_method
              ? kTrustedDevicesPurpose
          : pairing_replay_method
              ? kPairingReplayPurpose
              : kPairingTransactionPurpose;
      const size_t maximum_length = pairing_transaction_method
                                        ? kPairingTransactionPlaintextMaximumLength
                                        : kPairingStatePlaintextMaximumLength;
      const bool read_method =
          method == "readTrustedDeviceRegistry" ||
          method == "readPairingReplayStore" ||
          method == "readPairingTransaction";
      const bool create_method =
          method == "createTrustedDeviceRegistry" ||
          method == "createPairingReplayStore" ||
          method == "createPairingTransaction";
      const bool replace_method =
          method == "replaceTrustedDeviceRegistry" ||
          method == "replacePairingReplayStore" ||
          method == "replacePairingTransaction";
      if (read_method) {
        if (!HasNoArguments(call)) {
          ReturnFailure(std::move(result));
          return;
        }
        std::vector<uint8_t> plaintext;
        const OperationResult read_result = ReadProtectedMetadata(
            path, purpose, maximum_length, &plaintext);
        if (read_result == OperationResult::kNotFound) {
          result->Success(flutter::EncodableValue());
          return;
        }
        if (read_result != OperationResult::kSuccess) {
          Wipe(&plaintext);
          ReturnFailure(std::move(result), read_result);
          return;
        }
        ReturnBytes(std::move(result), &plaintext, true);
        return;
      }
      if (create_method || replace_method) {
        const std::string bytes_key = pairing_transaction_method
                                          ? "transaction_bytes"
                                          : "state_bytes";
        const flutter::EncodableMap* arguments = ExactArguments(
            call, replace_method
                      ? std::vector<std::string>{bytes_key,
                                                 "expected_sha256"}
                      : std::vector<std::string>{bytes_key});
        const std::vector<uint8_t>* supplied =
            arguments == nullptr
                ? nullptr
                : BytesArgument(*arguments, bytes_key);
        const std::string* expected_sha256 =
            !replace_method || arguments == nullptr
                ? nullptr
                : StringArgument(*arguments, "expected_sha256");
        if (supplied == nullptr || supplied->empty() ||
            supplied->size() > maximum_length ||
            (replace_method &&
             (expected_sha256 == nullptr ||
              !IsLowerSha256(*expected_sha256)))) {
          ReturnFailure(std::move(result));
          return;
        }
        std::vector<uint8_t> plaintext(*supplied);
        const OperationResult mutation_result =
            create_method
                ? CreateProtectedMetadata(paths.pairing_directory, path,
                                          purpose, plaintext, maximum_length)
                : ReplaceProtectedMetadata(
                      paths.pairing_directory, path, purpose, plaintext,
                      *expected_sha256, maximum_length);
        Wipe(&plaintext);
        if (mutation_result != OperationResult::kSuccess) {
          ReturnFailure(std::move(result), mutation_result);
          return;
        }
        result->Success();
        return;
      }
      if (method == "deletePairingTransaction") {
        const flutter::EncodableMap* arguments =
            ExactArguments(call, {"expected_sha256"});
        const std::string* expected_sha256 =
            arguments == nullptr
                ? nullptr
                : StringArgument(*arguments, "expected_sha256");
        if (expected_sha256 == nullptr ||
            !IsLowerSha256(*expected_sha256)) {
          ReturnFailure(std::move(result));
          return;
        }
        const OperationResult delete_result = DeleteProtectedMetadata(
            path, purpose, *expected_sha256, false, maximum_length);
        if (delete_result != OperationResult::kSuccess) {
          ReturnFailure(std::move(result), delete_result);
          return;
        }
        result->Success();
        return;
      }
    }

    if (pairing_staging_method) {
      const bool read_method = method == "readStagedPairingArtifact";
      const bool create_method = method == "createStagedPairingArtifact";
      const flutter::EncodableMap* arguments = ExactArguments(
          call, read_method
                    ? std::vector<std::string>{"kind"}
                : create_method
                    ? std::vector<std::string>{"kind", "artifact_bytes"}
                    : std::vector<std::string>{"kind", "expected_sha256"});
      const std::string* kind =
          arguments == nullptr ? nullptr : StringArgument(*arguments, "kind");
      if (kind == nullptr || !IsPairingArtifactKind(*kind)) {
        ReturnFailure(std::move(result));
        return;
      }
      const std::wstring artifact_path = PairingArtifactPath(paths, *kind);
      if (artifact_path.empty()) {
        ReturnFailure(std::move(result));
        return;
      }
      if (read_method) {
        std::vector<uint8_t> artifact;
        const OperationResult read_result = ReadRegularFile(
            artifact_path, kEncryptedDocumentMaximumLength, &artifact);
        if (read_result == OperationResult::kNotFound) {
          result->Success(flutter::EncodableValue());
          return;
        }
        if (read_result != OperationResult::kSuccess || artifact.empty()) {
          Wipe(&artifact);
          ReturnFailure(std::move(result), read_result);
          return;
        }
        ReturnBytes(std::move(result), &artifact, true);
        return;
      }
      if (create_method) {
        const std::vector<uint8_t>* supplied =
            BytesArgument(*arguments, "artifact_bytes");
        if (supplied == nullptr || supplied->empty() ||
            supplied->size() > kEncryptedDocumentMaximumLength) {
          ReturnFailure(std::move(result));
          return;
        }
        std::vector<uint8_t> artifact(*supplied);
        const OperationResult create_result = AtomicCreate(
            paths.pairing_staging_directory, artifact_path, artifact,
            kEncryptedDocumentMaximumLength);
        Wipe(&artifact);
        if (create_result != OperationResult::kSuccess) {
          ReturnFailure(std::move(result), create_result);
          return;
        }
        result->Success();
        return;
      }
      const std::string* expected_sha256 =
          StringArgument(*arguments, "expected_sha256");
      if (expected_sha256 == nullptr ||
          !IsLowerSha256(*expected_sha256)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> current;
      const OperationResult read_result = ReadRegularFile(
          artifact_path, kEncryptedDocumentMaximumLength, &current);
      std::array<uint8_t, 32> digest{};
      const bool digest_ok =
          read_result == OperationResult::kSuccess && Sha256(current, &digest);
      const std::string actual_sha256 =
          digest_ok ? HexLower(digest.data(), digest.size()) : std::string();
      const bool matches =
          digest_ok &&
          ConstantTimeEquals(
              reinterpret_cast<const uint8_t*>(actual_sha256.data()),
              actual_sha256.size(),
              reinterpret_cast<const uint8_t*>(expected_sha256->data()),
              expected_sha256->size());
      Wipe(&current);
      SecureZeroMemory(digest.data(), digest.size());
      if (!matches) {
        ReturnFailure(std::move(result),
                      read_result == OperationResult::kSuccess
                          ? OperationResult::kStale
                          : read_result);
        return;
      }
      const OperationResult delete_result = DeleteRegularFile(artifact_path);
      if (delete_result != OperationResult::kSuccess ||
          InspectRegularFile(artifact_path) != OperationResult::kNotFound) {
        ReturnFailure(std::move(result), delete_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "readPlaintextMigrationJournal") {
      if (!HasNoArguments(call)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> journal;
      const OperationResult read_result = ReadProtectedMetadata(
          paths.migration_journal_path, kMigrationJournalPurpose,
          kProtectedMetadataPlaintextMaximumLength, &journal);
      if (read_result == OperationResult::kNotFound) {
        result->Success(flutter::EncodableValue());
        return;
      }
      if (read_result != OperationResult::kSuccess) {
        Wipe(&journal);
        ReturnFailure(std::move(result));
        return;
      }
      ReturnBytes(std::move(result), &journal, true);
      return;
    }

    if (method == "createPlaintextMigrationJournal") {
      const flutter::EncodableMap* arguments =
          ExactArguments(call, {"journal_bytes"});
      const std::vector<uint8_t>* supplied =
          arguments == nullptr
              ? nullptr
              : BytesArgument(*arguments, "journal_bytes");
      if (supplied == nullptr || supplied->empty() ||
          supplied->size() > kProtectedMetadataPlaintextMaximumLength) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> journal(*supplied);
      const OperationResult create_result = CreateProtectedMetadata(
          paths.migrations_directory, paths.migration_journal_path,
          kMigrationJournalPurpose, journal,
          kProtectedMetadataPlaintextMaximumLength);
      Wipe(&journal);
      if (create_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), create_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "replacePlaintextMigrationJournal") {
      const flutter::EncodableMap* arguments = ExactArguments(
          call, {"journal_bytes", "expected_sha256"});
      const std::vector<uint8_t>* supplied =
          arguments == nullptr
              ? nullptr
              : BytesArgument(*arguments, "journal_bytes");
      const std::string* expected_sha256 =
          arguments == nullptr
              ? nullptr
              : StringArgument(*arguments, "expected_sha256");
      if (supplied == nullptr || expected_sha256 == nullptr ||
          supplied->empty() ||
          supplied->size() > kProtectedMetadataPlaintextMaximumLength ||
          !IsLowerSha256(*expected_sha256)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> journal(*supplied);
      const OperationResult replace_result = ReplaceProtectedMetadata(
          paths.migrations_directory, paths.migration_journal_path,
          kMigrationJournalPurpose, journal, *expected_sha256,
          kProtectedMetadataPlaintextMaximumLength);
      Wipe(&journal);
      if (replace_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), replace_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "deletePlaintextMigrationJournal") {
      const flutter::EncodableMap* arguments =
          ExactArguments(call, {"expected_sha256", "allow_absent"});
      const std::string* expected_sha256 =
          arguments == nullptr
              ? nullptr
              : StringArgument(*arguments, "expected_sha256");
      const bool* allow_absent =
          arguments == nullptr
              ? nullptr
              : BooleanArgument(*arguments, "allow_absent");
      if (expected_sha256 == nullptr || allow_absent == nullptr ||
          !IsLowerSha256(*expected_sha256)) {
        ReturnFailure(std::move(result));
        return;
      }
      const OperationResult delete_result = DeleteProtectedMetadata(
          paths.migration_journal_path, kMigrationJournalPurpose,
          *expected_sha256, *allow_absent,
          kProtectedMetadataPlaintextMaximumLength);
      if (delete_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), delete_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "readRecoveryImportJournal") {
      if (!HasNoArguments(call)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> journal;
      const OperationResult read_result = ReadProtectedMetadata(
          paths.recovery_import_path, kRecoveryImportPurpose,
          kProtectedMetadataPlaintextMaximumLength, &journal);
      if (read_result == OperationResult::kNotFound) {
        result->Success(flutter::EncodableValue());
        return;
      }
      if (read_result != OperationResult::kSuccess) {
        Wipe(&journal);
        ReturnFailure(std::move(result));
        return;
      }
      ReturnBytes(std::move(result), &journal, true);
      return;
    }

    if (method == "createRecoveryImportJournal") {
      const flutter::EncodableMap* arguments =
          ExactArguments(call, {"journal_bytes"});
      const std::vector<uint8_t>* supplied =
          arguments == nullptr
              ? nullptr
              : BytesArgument(*arguments, "journal_bytes");
      if (supplied == nullptr || supplied->empty() ||
          supplied->size() > kProtectedMetadataPlaintextMaximumLength) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> journal(*supplied);
      const OperationResult create_result = CreateProtectedMetadata(
          paths.imports_directory, paths.recovery_import_path,
          kRecoveryImportPurpose, journal,
          kProtectedMetadataPlaintextMaximumLength);
      Wipe(&journal);
      if (create_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), create_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "replaceRecoveryImportJournal") {
      const flutter::EncodableMap* arguments = ExactArguments(
          call, {"journal_bytes", "expected_sha256"});
      const std::vector<uint8_t>* supplied =
          arguments == nullptr
              ? nullptr
              : BytesArgument(*arguments, "journal_bytes");
      const std::string* expected_sha256 =
          arguments == nullptr
              ? nullptr
              : StringArgument(*arguments, "expected_sha256");
      if (supplied == nullptr || expected_sha256 == nullptr ||
          supplied->empty() ||
          supplied->size() > kProtectedMetadataPlaintextMaximumLength ||
          !IsLowerSha256(*expected_sha256)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> journal(*supplied);
      const OperationResult replace_result = ReplaceProtectedMetadata(
          paths.imports_directory, paths.recovery_import_path,
          kRecoveryImportPurpose, journal, *expected_sha256,
          kProtectedMetadataPlaintextMaximumLength);
      Wipe(&journal);
      if (replace_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), replace_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "deleteRecoveryImportJournal") {
      const flutter::EncodableMap* arguments =
          ExactArguments(call, {"expected_sha256", "allow_absent"});
      const std::string* expected_sha256 =
          arguments == nullptr
              ? nullptr
              : StringArgument(*arguments, "expected_sha256");
      const bool* allow_absent =
          arguments == nullptr
              ? nullptr
              : BooleanArgument(*arguments, "allow_absent");
      if (expected_sha256 == nullptr || allow_absent == nullptr ||
          !IsLowerSha256(*expected_sha256)) {
        ReturnFailure(std::move(result));
        return;
      }
      const OperationResult delete_result = DeleteProtectedMetadata(
          paths.recovery_import_path, kRecoveryImportPurpose,
          *expected_sha256, *allow_absent,
          kProtectedMetadataPlaintextMaximumLength);
      if (delete_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), delete_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "readSelectedVault") {
      if (!HasNoArguments(call)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> plaintext;
      const OperationResult read_result = ReadProtectedMetadata(
          paths.selection_path, kSelectedVaultPurpose,
          kSelectedVaultPlaintextMaximumLength, &plaintext);
      if (read_result == OperationResult::kNotFound) {
        result->Success(flutter::EncodableValue());
        return;
      }
      std::string vault_id;
      const bool valid = read_result == OperationResult::kSuccess &&
                         ParseSelectedVaultPlaintext(plaintext, &vault_id);
      Wipe(&plaintext);
      if (!valid) {
        ReturnFailure(std::move(result));
        return;
      }
      result->Success(flutter::EncodableValue(vault_id));
      return;
    }

    if (method == "createSelectedVault") {
      const flutter::EncodableMap* arguments =
          ExactArguments(call, {"vault_id"});
      const std::string* vault_id =
          arguments == nullptr ? nullptr
                               : StringArgument(*arguments, "vault_id");
      if (vault_id == nullptr || !IsValidVaultId(*vault_id)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> plaintext = SelectedVaultPlaintext(*vault_id);
      const OperationResult create_result = CreateProtectedMetadata(
          paths.selection_directory, paths.selection_path,
          kSelectedVaultPurpose, plaintext,
          kSelectedVaultPlaintextMaximumLength);
      Wipe(&plaintext);
      if (create_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), create_result);
        return;
      }
      result->Success();
      return;
    }

    if (method == "clearSelectedVault") {
      const flutter::EncodableMap* arguments =
          ExactArguments(call, {"expected_vault_id"});
      const std::string* expected_vault_id =
          arguments == nullptr
              ? nullptr
              : StringArgument(*arguments, "expected_vault_id");
      if (expected_vault_id == nullptr ||
          !IsValidVaultId(*expected_vault_id)) {
        ReturnFailure(std::move(result));
        return;
      }
      std::vector<uint8_t> plaintext;
      const OperationResult read_result = ReadProtectedMetadata(
          paths.selection_path, kSelectedVaultPurpose,
          kSelectedVaultPlaintextMaximumLength, &plaintext);
      std::string current_vault_id;
      const bool valid =
          read_result == OperationResult::kSuccess &&
          ParseSelectedVaultPlaintext(plaintext, &current_vault_id) &&
          ConstantTimeEquals(
              reinterpret_cast<const uint8_t*>(current_vault_id.data()),
              current_vault_id.size(),
              reinterpret_cast<const uint8_t*>(expected_vault_id->data()),
              expected_vault_id->size());
      if (!valid) {
        Wipe(&plaintext);
        ReturnFailure(std::move(result));
        return;
      }
      std::array<uint8_t, 32> digest{};
      const bool digest_ok = Sha256(plaintext, &digest);
      Wipe(&plaintext);
      if (!digest_ok) {
        ReturnFailure(std::move(result));
        return;
      }
      const std::string expected_sha256 =
          HexLower(digest.data(), digest.size());
      SecureZeroMemory(digest.data(), digest.size());
      const OperationResult delete_result = DeleteProtectedMetadata(
          paths.selection_path, kSelectedVaultPurpose, expected_sha256,
          false, kSelectedVaultPlaintextMaximumLength);
      if (delete_result != OperationResult::kSuccess) {
        ReturnFailure(std::move(result), delete_result);
        return;
      }
      result->Success();
      return;
    }
  }

  const bool recognized =
      method == "createVaultKey" || method == "loadVaultKey" ||
      method == "containsVaultKey" || method == "deleteVaultKey" ||
      method == "readLocalStore" || method == "createLocalStore" ||
      method == "replaceLocalStore" || method == "deleteLocalStore";
  if (!recognized) {
    result->NotImplemented();
    return;
  }

  const std::vector<std::string> keys =
      method == "createVaultKey"
          ? std::vector<std::string>{"vault_id", "vault_key"}
      : method == "createLocalStore"
          ? std::vector<std::string>{"vault_id", "store_bytes"}
      : method == "replaceLocalStore"
          ? std::vector<std::string>{"vault_id", "store_bytes",
                                     "expected_sha256"}
          : std::vector<std::string>{"vault_id"};
  const flutter::EncodableMap* arguments = ExactArguments(call, keys);
  const std::string* vault_id =
      arguments == nullptr ? nullptr : StringArgument(*arguments, "vault_id");
  if (vault_id == nullptr || !IsValidVaultId(*vault_id)) {
    ReturnFailure(std::move(result));
    return;
  }

  VaultPaths paths;
  ScopedVaultLock lock;
  if (!BeginOperation(*vault_id, &paths, &lock)) {
    ReturnFailure(std::move(result));
    return;
  }

  if (method == "createVaultKey") {
    const std::vector<uint8_t>* argument =
        BytesArgument(*arguments, "vault_key");
    if (argument == nullptr || argument->size() != kVaultKeyLength) {
      ReturnFailure(std::move(result));
      return;
    }
    std::vector<uint8_t> key_copy(*argument);
    std::array<uint8_t, 32> key_hash{};
    std::vector<uint8_t> protected_blob;
    const bool protected_ok =
        ProtectVaultKey(*vault_id, key_copy, &key_hash, &protected_blob);
    Wipe(&key_copy);
    if (!protected_ok) {
      ReturnFailure(std::move(result));
      return;
    }
    const std::vector<uint8_t> envelope =
        SerializeKeyEnvelope(*vault_id, key_hash, protected_blob);
    const OperationResult create_result =
        AtomicCreate(paths.key_directory, paths.key_path, envelope,
                     kKeyEnvelopeFixedLength + kVaultIdMaximumLength +
                         kProtectedBlobMaximumLength);
    if (create_result != OperationResult::kSuccess) {
      ReturnFailure(std::move(result), create_result);
      return;
    }
    result->Success();
    return;
  }

  if (method == "loadVaultKey") {
    std::vector<uint8_t> envelope_bytes;
    const OperationResult read_result =
        ReadRegularFile(paths.key_path,
                        kKeyEnvelopeFixedLength + kVaultIdMaximumLength +
                            kProtectedBlobMaximumLength,
                        &envelope_bytes);
    if (read_result == OperationResult::kNotFound) {
      result->Success(flutter::EncodableValue());
      return;
    }
    KeyEnvelope envelope;
    std::vector<uint8_t> vault_key;
    const bool valid =
        read_result == OperationResult::kSuccess &&
        ParseKeyEnvelope(envelope_bytes, *vault_id, &envelope) &&
        UnprotectVaultKey(envelope, &vault_key);
    Wipe(&envelope_bytes);
    if (!valid) {
      Wipe(&vault_key);
      ReturnFailure(std::move(result));
      return;
    }
    ReturnBytes(std::move(result), &vault_key, true);
    return;
  }

  if (method == "containsVaultKey") {
    const OperationResult inspected = InspectRegularFile(paths.key_path);
    if (inspected == OperationResult::kFailed) {
      ReturnFailure(std::move(result));
      return;
    }
    result->Success(
        flutter::EncodableValue(inspected == OperationResult::kSuccess));
    return;
  }

  if (method == "deleteVaultKey") {
    const OperationResult delete_result =
        DeleteRegularFile(paths.key_path);
    if (delete_result != OperationResult::kSuccess) {
      ReturnFailure(std::move(result));
      return;
    }
    result->Success();
    return;
  }

  if (method == "readLocalStore") {
    std::vector<uint8_t> store_bytes;
    const OperationResult read_result =
        ReadRegularFile(paths.store_path, kStoreMaximumLength, &store_bytes);
    if (read_result == OperationResult::kNotFound) {
      result->Success(flutter::EncodableValue());
      return;
    }
    if (read_result != OperationResult::kSuccess) {
      ReturnFailure(std::move(result));
      return;
    }
    ReturnBytes(std::move(result), &store_bytes, false);
    return;
  }

  if (method == "createLocalStore") {
    const std::vector<uint8_t>* store_bytes =
        BytesArgument(*arguments, "store_bytes");
    if (store_bytes == nullptr || store_bytes->empty() ||
        store_bytes->size() > kStoreMaximumLength) {
      ReturnFailure(std::move(result));
      return;
    }
    const OperationResult create_result =
        AtomicCreate(paths.vault_directory, paths.store_path, *store_bytes,
                     kStoreMaximumLength);
    if (create_result != OperationResult::kSuccess) {
      ReturnFailure(std::move(result), create_result);
      return;
    }
    result->Success();
    return;
  }

  if (method == "replaceLocalStore") {
    const std::vector<uint8_t>* store_bytes =
        BytesArgument(*arguments, "store_bytes");
    const std::string* expected_sha256 =
        StringArgument(*arguments, "expected_sha256");
    if (store_bytes == nullptr || expected_sha256 == nullptr ||
        store_bytes->empty() || store_bytes->size() > kStoreMaximumLength ||
        !IsLowerSha256(*expected_sha256)) {
      ReturnFailure(std::move(result));
      return;
    }
    const OperationResult replace_result =
        AtomicReplace(paths.vault_directory, paths.store_path, *store_bytes,
                      *expected_sha256, kStoreMaximumLength);
    if (replace_result != OperationResult::kSuccess) {
      ReturnFailure(std::move(result), replace_result);
      return;
    }
    result->Success();
    return;
  }

  if (method == "deleteLocalStore") {
    const OperationResult delete_result =
        DeleteRegularFile(paths.store_path);
    if (delete_result != OperationResult::kSuccess) {
      ReturnFailure(std::move(result));
      return;
    }
    result->Success();
    return;
  }

  result->NotImplemented();
}
