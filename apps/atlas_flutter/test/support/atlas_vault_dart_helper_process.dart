import 'dart:io';

Future<Process> startAtlasVaultDartHelper(
  String script,
  List<String> arguments,
) {
  final dartExecutable = _resolveDartExecutable();

  return Process.start(dartExecutable, <String>[
    'run',
    script,
    ...arguments,
  ], workingDirectory: Directory.current.path);
}

String _resolveDartExecutable() {
  final executableName = Platform.isWindows ? 'dart.exe' : 'dart';
  final flutterRoot = Platform.environment['FLUTTER_ROOT'];
  if (flutterRoot != null && flutterRoot.isNotEmpty) {
    final bundled = <String>[
      flutterRoot,
      'bin',
      'cache',
      'dart-sdk',
      'bin',
      executableName,
    ].join(Platform.pathSeparator);
    if (File(bundled).existsSync()) return bundled;
  }

  final path = Platform.environment['PATH'];
  if (path != null) {
    for (var directory in path.split(Platform.isWindows ? ';' : ':')) {
      directory = directory.trim();
      if (directory.startsWith('"') && directory.endsWith('"')) {
        directory = directory.substring(1, directory.length - 1);
      }
      if (directory.isEmpty) continue;
      final candidate = <String>[
        directory,
        executableName,
      ].join(Platform.pathSeparator);
      if (File(candidate).existsSync()) return candidate;
      if (Platform.isWindows &&
          (File(
                <String>[directory, 'dart.bat'].join(Platform.pathSeparator),
              ).existsSync() ||
              File(
                <String>[directory, 'flutter.bat'].join(Platform.pathSeparator),
              ).existsSync())) {
        final bundled = <String>[
          directory,
          'cache',
          'dart-sdk',
          'bin',
          'dart.exe',
        ].join(Platform.pathSeparator);
        if (File(bundled).existsSync()) return bundled;
      }
    }
  }
  throw StateError('Dart executable is unavailable.');
}
