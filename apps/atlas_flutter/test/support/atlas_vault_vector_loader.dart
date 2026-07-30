import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

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
  final root = atlasVaultRepositoryRoot();
  final file = File('${root.path}/contracts/sync/test_vectors/$fileName');
  if (!file.existsSync()) {
    throw StateError('AtlasVault vector file was not found.');
  }
  final bytes = file.readAsBytesSync();
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
