import 'dart:convert';
import 'dart:typed_data';

final class AtlasVaultFormatException implements Exception {
  const AtlasVaultFormatException(this.message);

  final String message;

  @override
  String toString() => 'AtlasVault format error: $message';
}

Map<String, Object?> decodeAtlasVaultJsonObject(
  String source, {
  required String context,
}) {
  final Object? value;
  try {
    value = jsonDecode(source);
  } on FormatException {
    throw AtlasVaultFormatException('$context must be valid JSON.');
  }
  return requireAtlasVaultObject(value, context: context);
}

Map<String, Object?> requireAtlasVaultObject(
  Object? value, {
  required String context,
}) {
  if (value is! Map) {
    throw AtlasVaultFormatException('$context must be an object.');
  }
  final output = <String, Object?>{};
  for (final entry in value.entries) {
    final key = entry.key;
    if (key is! String) {
      throw AtlasVaultFormatException('$context keys must be strings.');
    }
    output[key] = entry.value;
  }
  return output;
}

void requireAtlasVaultExactKeys(
  Map<String, Object?> value, {
  required Set<String> requiredKeys,
  Set<String> optionalKeys = const <String>{},
  required String context,
}) {
  final allowed = <String>{...requiredKeys, ...optionalKeys};
  if (!value.keys.toSet().containsAll(requiredKeys) ||
      !allowed.containsAll(value.keys)) {
    throw AtlasVaultFormatException('$context contains invalid fields.');
  }
}

String requireAtlasVaultString(
  Object? value, {
  required String field,
  bool allowEmpty = true,
}) {
  if (value is! String || (!allowEmpty && value.isEmpty)) {
    throw AtlasVaultFormatException('$field must be text.');
  }
  requireAtlasVaultWellFormedUtf16(value, field: field);
  return value;
}

void requireAtlasVaultWellFormedUtf16(String value, {required String field}) {
  final codeUnits = value.codeUnits;
  for (var index = 0; index < codeUnits.length; index++) {
    final current = codeUnits[index];
    if (current >= 0xd800 && current <= 0xdbff) {
      if (index + 1 >= codeUnits.length) {
        throw AtlasVaultFormatException('$field must contain valid Unicode.');
      }
      final next = codeUnits[index + 1];
      if (next < 0xdc00 || next > 0xdfff) {
        throw AtlasVaultFormatException('$field must contain valid Unicode.');
      }
      index += 1;
      continue;
    }
    if (current >= 0xdc00 && current <= 0xdfff) {
      throw AtlasVaultFormatException('$field must contain valid Unicode.');
    }
  }
}

String? requireAtlasVaultOptionalString(
  Map<String, Object?> value, {
  required String key,
  required String field,
  bool allowEmpty = true,
}) {
  if (!value.containsKey(key)) {
    return null;
  }
  return requireAtlasVaultString(
    value[key],
    field: field,
    allowEmpty: allowEmpty,
  );
}

int requireAtlasVaultInt(Object? value, {required String field}) {
  if (value is! int) {
    throw AtlasVaultFormatException('$field must be an integer.');
  }
  return value;
}

bool requireAtlasVaultBool(Object? value, {required String field}) {
  if (value is! bool) {
    throw AtlasVaultFormatException('$field must be a Boolean.');
  }
  return value;
}

List<Object?> requireAtlasVaultList(Object? value, {required String field}) {
  if (value is! List) {
    throw AtlasVaultFormatException('$field must be a list.');
  }
  return List<Object?>.unmodifiable(value.cast<Object?>());
}

List<String> requireAtlasVaultStringList(
  Object? value, {
  required String field,
}) {
  final source = requireAtlasVaultList(value, field: field);
  final output = <String>[];
  for (final item in source) {
    output.add(requireAtlasVaultString(item, field: '$field item'));
  }
  return List<String>.unmodifiable(output);
}

Uint8List requireAtlasVaultCanonicalBase64(
  Object? value, {
  required String field,
  int? exactLength,
  int? minimumLength,
}) {
  final text = requireAtlasVaultString(value, field: field, allowEmpty: false);
  final List<int> decoded;
  try {
    decoded = base64Decode(text);
  } on FormatException {
    throw AtlasVaultFormatException('$field must be canonical Base64.');
  }
  if (base64Encode(decoded) != text) {
    throw AtlasVaultFormatException('$field must be canonical Base64.');
  }
  if (exactLength != null && decoded.length != exactLength) {
    throw AtlasVaultFormatException('$field has an invalid length.');
  }
  if (minimumLength != null && decoded.length < minimumLength) {
    throw AtlasVaultFormatException('$field has an invalid length.');
  }
  return Uint8List.fromList(decoded);
}

String requireAtlasVaultCanonicalUuid(Object? value, {required String field}) {
  final text = requireAtlasVaultString(value, field: field, allowEmpty: false);
  final expression = RegExp(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
  );
  if (!expression.hasMatch(text)) {
    throw AtlasVaultFormatException(
      '$field must be a canonical lowercase UUID.',
    );
  }
  return text;
}

String requireAtlasVaultVaultId(Object? value) {
  final text = requireAtlasVaultString(
    value,
    field: 'vault.vault_id',
    allowEmpty: false,
  );
  const reserved = <String>{
    'application_note',
    'draft_metadata',
    'profile_snippet',
    'saved_job',
    'saved_search',
  };
  if (text.length > 96 ||
      text == '.' ||
      text == '..' ||
      !RegExp(r'^[A-Za-z0-9_-]+$').hasMatch(text) ||
      reserved.contains(text.toLowerCase())) {
    throw const AtlasVaultFormatException(
      'vault.vault_id must be a valid identifier.',
    );
  }
  return text;
}

String requireAtlasVaultUtcSeconds(Object? value, {required String field}) {
  final text = requireAtlasVaultString(value, field: field, allowEmpty: false);
  final match = RegExp(
    r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$',
  ).firstMatch(text);
  if (match == null) {
    throw AtlasVaultFormatException('$field must use UTC ISO-8601 seconds.');
  }
  final values = <int>[
    for (var index = 1; index <= 6; index++) int.parse(match.group(index)!),
  ];
  final date = DateTime.utc(
    values[0],
    values[1],
    values[2],
    values[3],
    values[4],
    values[5],
  );
  if (values[0] == 0 ||
      date.year != values[0] ||
      date.month != values[1] ||
      date.day != values[2] ||
      date.hour != values[3] ||
      date.minute != values[4] ||
      date.second != values[5]) {
    throw AtlasVaultFormatException('$field must use UTC ISO-8601 seconds.');
  }
  return text;
}

String requireAtlasVaultDate(Object? value, {required String field}) {
  final text = requireAtlasVaultString(value, field: field, allowEmpty: false);
  final match = RegExp(r'^(\d{4})-(\d{2})-(\d{2})$').firstMatch(text);
  if (match == null) {
    throw AtlasVaultFormatException('$field must use YYYY-MM-DD.');
  }
  final year = int.parse(match.group(1)!);
  final month = int.parse(match.group(2)!);
  final day = int.parse(match.group(3)!);
  final date = DateTime.utc(year, month, day);
  if (year == 0 ||
      date.year != year ||
      date.month != month ||
      date.day != day) {
    throw AtlasVaultFormatException('$field must use YYYY-MM-DD.');
  }
  return text;
}

String? requireAtlasVaultOptionalUtcSeconds(
  Map<String, Object?> value, {
  required String key,
  required String field,
}) {
  if (!value.containsKey(key)) {
    return null;
  }
  return requireAtlasVaultUtcSeconds(value[key], field: field);
}

String? requireAtlasVaultOptionalDate(
  Map<String, Object?> value, {
  required String key,
  required String field,
}) {
  if (!value.containsKey(key)) {
    return null;
  }
  return requireAtlasVaultDate(value[key], field: field);
}
