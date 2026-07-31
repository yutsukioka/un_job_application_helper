#include "atlas_vault_windows_storage.h"

#include <windows.h>

#include <bcrypt.h>
#include <dpapi.h>
#include <flutter/standard_method_codec.h>
#include <knownfolders.h>
#include <shlobj.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace {

constexpr size_t kVaultKeyLength = 32;
constexpr size_t kVaultIdMaximumLength = 96;
constexpr size_t kProtectedBlobMaximumLength = 1024 * 1024;
constexpr size_t kStoreMaximumLength = 128 * 1024 * 1024;
constexpr size_t kKeyEnvelopeFixedLength = 8 + 4 + 2 + 4 + 32;
constexpr char kKeyEnvelopeMagic[] = "AVWKEY01";
constexpr uint32_t kKeyEnvelopeVersion = 1;

enum class OperationResult {
  kSuccess,
  kNotFound,
  kAlreadyExists,
  kStale,
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
          reinterpret_cast<PUCHAR>(&object_length), sizeof(object_length),
          &returned, 0)) ||
      returned != sizeof(object_length) ||
      !IsNtSuccess(BCryptGetProperty(
          algorithm, BCRYPT_HASH_LENGTH,
          reinterpret_cast<PUCHAR>(&hash_length), sizeof(hash_length),
          &returned, 0)) ||
      returned != sizeof(hash_length) || hash_length != output->size() ||
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
                                   sizeof(information)) == FALSE) {
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

bool ScopedVaultLock::Acquire(const std::wstring& path) {
  handle_ = ScopedHandle(CreateFileW(
      path.c_str(), GENERIC_READ | GENERIC_WRITE,
      FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
      FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
  if (!handle_.valid() || !IsSafeRegularHandle(handle_.get())) {
    return false;
  }
  if (LockFileEx(handle_.get(), LOCKFILE_EXCLUSIVE_LOCK, 0, 1, 0,
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
      output.cbData == kVaultKeyLength) {
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
      vault_id_length > kVaultIdMaximumLength ||
      protected_blob_length == 0 ||
      protected_blob_length > kProtectedBlobMaximumLength ||
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

}  // namespace

AtlasVaultWindowsStorage::AtlasVaultWindowsStorage(
    flutter::BinaryMessenger* messenger,
    const std::string& channel_name)
    : channel_(std::make_unique<
               flutter::MethodChannel<flutter::EncodableValue>>(
          messenger, channel_name,
          &flutter::StandardMethodCodec::GetInstance())) {
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
}

void AtlasVaultWindowsStorage::HandleMethodCall(
    const flutter::MethodCall<flutter::EncodableValue>& call,
    std::unique_ptr<flutter::MethodResult<flutter::EncodableValue>> result) {
  const std::string& method = call.method_name();
  if (method == "capabilities") {
    if (call.arguments() != nullptr) {
      ReturnFailure(std::move(result));
      return;
    }
    flutter::EncodableMap capabilities{
        {flutter::EncodableValue("secure_boundary_available"),
         flutter::EncodableValue(true)},
        {flutter::EncodableValue("dpapi_available"),
         flutter::EncodableValue(true)},
        {flutter::EncodableValue("current_user_scope"),
         flutter::EncodableValue(true)},
        {flutter::EncodableValue("local_app_data_available"),
         flutter::EncodableValue(true)},
        {flutter::EncodableValue("atomic_replace_available"),
         flutter::EncodableValue(true)},
        {flutter::EncodableValue("hardware_backed_guaranteed"),
         flutter::EncodableValue(false)},
    };
    result->Success(flutter::EncodableValue(capabilities));
    return;
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
