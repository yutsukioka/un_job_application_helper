import 'dart:convert';
import 'dart:typed_data';

import 'strict_values.dart';

Uint8List encodeCanonicalJson(Object? value) {
  return Uint8List.fromList(utf8.encode(canonicalJsonString(value)));
}

String canonicalJsonString(Object? value) {
  final output = StringBuffer();
  _writeCanonicalJson(output, value);
  return output.toString();
}

void _writeCanonicalJson(StringBuffer output, Object? value) {
  if (value == null) {
    output.write('null');
    return;
  }
  if (value is bool) {
    output.write(value ? 'true' : 'false');
    return;
  }
  if (value is int) {
    output.write(value);
    return;
  }
  if (value is String) {
    _writeCanonicalString(output, value);
    return;
  }
  if (value is List) {
    output.write('[');
    for (var index = 0; index < value.length; index++) {
      if (index > 0) {
        output.write(',');
      }
      _writeCanonicalJson(output, value[index]);
    }
    output.write(']');
    return;
  }
  if (value is Map) {
    final entries = <MapEntry<String, Object?>>[];
    for (final entry in value.entries) {
      if (entry.key is! String) {
        throw const AtlasVaultFormatException(
          'Canonical JSON object keys must be strings.',
        );
      }
      final key = entry.key! as String;
      requireAtlasVaultWellFormedUtf16(key, field: 'Canonical JSON object key');
      entries.add(MapEntry(key, entry.value));
    }
    entries.sort((left, right) => _compareUnicodeScalars(left.key, right.key));
    output.write('{');
    for (var index = 0; index < entries.length; index++) {
      if (index > 0) {
        output.write(',');
      }
      _writeCanonicalString(output, entries[index].key);
      output.write(':');
      _writeCanonicalJson(output, entries[index].value);
    }
    output.write('}');
    return;
  }
  throw const AtlasVaultFormatException(
    'Canonical JSON contains an unsupported value.',
  );
}

void _writeCanonicalString(StringBuffer output, String value) {
  requireAtlasVaultWellFormedUtf16(value, field: 'Canonical JSON string');
  output.write('"');
  for (final scalar in value.runes) {
    switch (scalar) {
      case 0x08:
        output.write(r'\b');
      case 0x09:
        output.write(r'\t');
      case 0x0A:
        output.write(r'\n');
      case 0x0C:
        output.write(r'\f');
      case 0x0D:
        output.write(r'\r');
      case 0x22:
        output.write(r'\"');
      case 0x5C:
        output.write(r'\\');
      default:
        if (scalar >= 0x20 && scalar <= 0x7E) {
          output.writeCharCode(scalar);
        } else if (scalar <= 0xFFFF) {
          output.write(r'\u');
          output.write(scalar.toRadixString(16).padLeft(4, '0'));
        } else {
          final adjusted = scalar - 0x10000;
          final high = 0xD800 + (adjusted >> 10);
          final low = 0xDC00 + (adjusted & 0x3FF);
          output
            ..write(r'\u')
            ..write(high.toRadixString(16).padLeft(4, '0'))
            ..write(r'\u')
            ..write(low.toRadixString(16).padLeft(4, '0'));
        }
    }
  }
  output.write('"');
}

int _compareUnicodeScalars(String left, String right) {
  final leftScalars = left.runes.iterator;
  final rightScalars = right.runes.iterator;
  while (true) {
    final hasLeft = leftScalars.moveNext();
    final hasRight = rightScalars.moveNext();
    if (!hasLeft || !hasRight) {
      if (hasLeft == hasRight) {
        return 0;
      }
      return hasLeft ? 1 : -1;
    }
    final comparison = leftScalars.current.compareTo(rightScalars.current);
    if (comparison != 0) {
      return comparison;
    }
  }
}
