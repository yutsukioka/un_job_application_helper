import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

const _deviceInteropVectorBase64 = String.fromEnvironment(
  'ATLAS_INTEROP_VECTOR_B64',
);
const _deviceWindowsInteropVectorBase64 = String.fromEnvironment(
  'ATLAS_WINDOWS_INTEROP_VECTOR_B64',
);

Directory atlasVaultRepositoryRoot() {
  var current = Directory.current.absolute;
  while (true) {
    final vectors = Directory('${current.path}/contracts/sync/test_vectors');
    if (vectors.existsSync()) {
      return current;
    }
    final parent = current.parent;
    if (parent.path == current.path) {
      throw StateError('AtlasVault vector repository root was not found.');
    }
    current = parent;
  }
}

Uint8List loadAtlasVaultVectorBytes(String fileName) {
  Uint8List bytes;
  if (fileName == 'atlasvault_ios_flutter_interop_vectors_v1.json' &&
      _deviceInteropVectorBase64.isNotEmpty) {
    try {
      bytes = base64Decode(_deviceInteropVectorBase64);
    } on FormatException {
      throw StateError('AtlasVault device vector input is invalid.');
    }
  } else if (fileName == 'atlasvault_windows_interop_vectors_v1.json' &&
      _deviceWindowsInteropVectorBase64.isNotEmpty) {
    try {
      bytes = base64Decode(_deviceWindowsInteropVectorBase64);
    } on FormatException {
      throw StateError('AtlasVault device vector input is invalid.');
    }
  } else {
    final root = atlasVaultRepositoryRoot();
    final file = File('${root.path}/contracts/sync/test_vectors/$fileName');
    if (!file.existsSync()) {
      throw StateError('AtlasVault vector file was not found.');
    }
    bytes = file.readAsBytesSync();
  }
  final object = jsonDecode(utf8.decode(bytes));
  if (object is! Map<String, dynamic>) {
    throw StateError('AtlasVault vector file is invalid.');
  }
  final warning =
      object['_warning'] ?? object['warning'] ?? object['description'];
  final normalizedWarning = warning is String
      ? warning.toLowerCase().replaceAll('-', ' ')
      : '';
  if (!normalizedWarning.contains('test only') &&
      !normalizedWarning.contains('fake test data only')) {
    throw StateError('AtlasVault vector test-only warning is missing.');
  }
  return bytes;
}

Map<String, Object?> loadAtlasVaultVector(String fileName) {
  final object = jsonDecode(utf8.decode(loadAtlasVaultVectorBytes(fileName)));
  if (object is! Map<String, dynamic>) {
    throw StateError('AtlasVault vector file is invalid.');
  }
  return object.cast<String, Object?>();
}

Map<String, Object?> atlasVaultObject(Object? value) {
  if (value is! Map<String, dynamic>) {
    throw StateError('AtlasVault vector value is not an object.');
  }
  return value.cast<String, Object?>();
}

List<Object?> atlasVaultList(Object? value) {
  if (value is! List<dynamic>) {
    throw StateError('AtlasVault vector value is not a list.');
  }
  return value.cast<Object?>();
}
